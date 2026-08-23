"""Point-in-time aggregation of the raw event stream.

This module is where the project's most expensive class of bug is prevented.

A feature computed from events is only legitimate if every event it used happened
**at or before the customer's snapshot date**. Get that wrong by even a few days
and the model learns from the future: development metrics look excellent, and the
model is worthless the moment it meets a customer whose future has not happened
yet. The filter in :func:`_within_window` is the whole point of this file, and
:func:`build_event_features` refuses to run without a snapshot column.

Aggregating from raw events rather than accepting pre-aggregated columns is a
deliberate cost. It is what lets the same code path serve batch training and
real-time scoring, which is the only reliable defence against training/serving
skew.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from crr.features.contract import FeatureSpec

#: Trailing windows, in days.
EVENT_WINDOWS: tuple[int, ...] = (30, 90, 180)

#: Event types broken out individually. These are the ones a risk analyst asks
#: about by name, and the ones the re-scoring policy triggers on.
TRACKED_EVENT_TYPES: tuple[str, ...] = (
    "missed_payment",
    "overdraft_breach",
    "chargeback",
    "cash_deposit",
    "wire_transfer_out",
    "crypto_transfer",
)

#: Window used for the per-type breakdowns.
TYPE_WINDOW = 90

_NIGHT_START_HOUR = 22
_NIGHT_END_HOUR = 6


def _within_window(events: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Events at or before the snapshot and no older than ``window_days``.

    ``age_days`` is measured in whole days between calendar dates, so an event
    on the snapshot date itself has age 0 and is included: a snapshot is taken at
    the end of its day. Anything with a negative age is in the future relative to
    the snapshot and is dropped — that is the leakage guard.
    """
    return events[(events["age_days"] >= 0) & (events["age_days"] < window_days)]


def _prepare(events: pd.DataFrame, index: pd.DataFrame) -> pd.DataFrame:
    """Join events onto their customer's snapshot and compute event age in days."""
    required = {"customer_id", "as_of", "home_country"}
    missing = required - set(index.columns)
    if missing:
        raise ValueError(f"index frame is missing {sorted(missing)}")

    merged = events.merge(index[["customer_id", "as_of", "home_country"]], on="customer_id", how="inner")
    if merged.empty:
        return merged.assign(age_days=pd.Series(dtype="int64"))

    event_date = pd.to_datetime(merged["event_ts"]).dt.normalize()
    as_of_date = pd.to_datetime(merged["as_of"]).dt.normalize()
    merged["age_days"] = (as_of_date - event_date).dt.days
    merged["hour"] = pd.to_datetime(merged["event_ts"]).dt.hour
    merged["is_night"] = ((merged["hour"] >= _NIGHT_START_HOUR) | (merged["hour"] < _NIGHT_END_HOUR)).astype(float)
    merged["is_foreign"] = (merged["counterparty_country"] != merged["home_country"]).astype(float)
    merged["abs_amount"] = merged["amount"].abs()
    merged["inflow"] = merged["amount"].clip(lower=0)
    merged["outflow"] = (-merged["amount"]).clip(lower=0)
    return merged


def _window_aggregates(events: pd.DataFrame, window: int) -> pd.DataFrame:
    """Volume, flow and mix aggregates for one trailing window."""
    scoped = _within_window(events, window)
    if scoped.empty:
        return pd.DataFrame()
    grouped = scoped.groupby("customer_id", observed=True)
    frame = grouped.agg(
        **{
            f"event_count_{window}d": ("event_id", "size"),
            f"inflow_{window}d": ("inflow", "sum"),
            f"outflow_{window}d": ("outflow", "sum"),
            f"max_abs_amount_{window}d": ("abs_amount", "max"),
            f"distinct_counterparty_countries_{window}d": ("counterparty_country", "nunique"),
            f"foreign_event_ratio_{window}d": ("is_foreign", "mean"),
            f"night_event_ratio_{window}d": ("is_night", "mean"),
        }
    )
    frame[f"net_flow_{window}d"] = frame[f"inflow_{window}d"] - frame[f"outflow_{window}d"]
    return frame


def _type_aggregates(events: pd.DataFrame, window: int = TYPE_WINDOW) -> pd.DataFrame:
    """Counts and amounts for the individually tracked event types."""
    scoped = _within_window(events, window)
    if scoped.empty:
        return pd.DataFrame()

    counts = (
        scoped[scoped["event_type"].isin(TRACKED_EVENT_TYPES)]
        .groupby(["customer_id", "event_type"], observed=True)
        .size()
        .unstack(fill_value=0)
    )
    counts.columns = [f"{name}_count_{window}d" for name in counts.columns]

    amounts = (
        scoped[scoped["event_type"].isin(TRACKED_EVENT_TYPES)]
        .groupby(["customer_id", "event_type"], observed=True)["abs_amount"]
        .sum()
        .unstack(fill_value=0.0)
    )
    amounts.columns = [f"{name}_amount_{window}d" for name in amounts.columns]

    triggers = (
        scoped.groupby("customer_id", observed=True)["is_trigger_event"]
        .sum()
        .rename(f"trigger_event_count_{window}d")
        .to_frame()
    )
    return counts.join(amounts, how="outer").join(triggers, how="outer")


def _recency(events: pd.DataFrame) -> pd.DataFrame:
    """Days since the most recent event of a few important kinds.

    Recency is not redundant with a count. Three missed payments last year and
    three last month are the same count and very different risks.
    """
    scoped = events[events["age_days"] >= 0]
    if scoped.empty:
        return pd.DataFrame()

    frames = [scoped.groupby("customer_id", observed=True)["age_days"].min().rename("days_since_last_event")]
    for label, subset in (
        ("trigger_event", scoped[scoped["is_trigger_event"] == 1]),
        ("missed_payment", scoped[scoped["event_type"] == "missed_payment"]),
        ("cash_deposit", scoped[scoped["event_type"] == "cash_deposit"]),
    ):
        if not subset.empty:
            frames.append(subset.groupby("customer_id", observed=True)["age_days"].min().rename(f"days_since_last_{label}"))
    return pd.concat(frames, axis=1)


def _empty_event_frame(keys: np.ndarray, windows: tuple[int, ...]) -> pd.DataFrame:
    """The feature row every customer with no event history receives.

    A count of zero is a fact (fill 0); a ratio or a recency over zero events is
    undefined (NaN). This reproduces exactly what the full path yields for empty
    events, so the fast path cannot change a score — only its cost.
    """
    n = len(keys)
    zeros = np.zeros(n, dtype=float)
    nans = np.full(n, np.nan, dtype=float)
    data: dict[str, np.ndarray] = {}
    for name, kind in _expected_columns(windows).items():
        data[name] = zeros.copy() if kind == "count" else nans.copy()
    data["has_event_history"] = zeros.copy()
    data["outflow_velocity_ratio"] = nans.copy()
    data["event_count_velocity_ratio"] = nans.copy()
    frame = pd.DataFrame(data, index=pd.Index(keys, name="customer_id"))
    return frame[sorted(frame.columns)]


def build_event_features(events: pd.DataFrame, index: pd.DataFrame, windows: tuple[int, ...] = EVENT_WINDOWS) -> pd.DataFrame:
    """Aggregate the event stream into per-customer features, point-in-time safe.

    ``index`` must carry ``customer_id``, ``as_of`` (the snapshot) and
    ``home_country``. The result is aligned to ``index`` row order, so customers
    with no event history still get a row.
    """
    if "customer_id" not in index.columns:
        raise ValueError("index frame must carry customer_id")

    keys = index["customer_id"].to_numpy()
    if events is None or events.empty:
        return _empty_event_frame(keys, windows)
    prepared = _prepare(events, index)

    pieces: list[pd.DataFrame] = []
    if not prepared.empty:
        pieces.extend(_window_aggregates(prepared, window) for window in windows)
        pieces.append(_type_aggregates(prepared))
        pieces.append(_recency(prepared))
    pieces = [piece for piece in pieces if not piece.empty]

    combined = pd.concat(pieces, axis=1) if pieces else pd.DataFrame(index=pd.Index([], name="customer_id"))
    combined = combined.reindex(keys)

    # Guarantee every declared column exists even when a window produced nothing
    # (a small or clean dataset). A feature that appears only sometimes is a
    # contract violation waiting to happen at serving time.
    for name, kind in _expected_columns(windows).items():
        if name not in combined.columns:
            combined[name] = 0.0 if kind == "count" else np.nan

    # A count of zero is a fact; a ratio over zero events is undefined. Filling
    # ratios and recency with 0 would tell the model this customer transacts
    # entirely domestically and was active today, which is the opposite of true.
    for name, kind in _expected_columns(windows).items():
        if kind == "count":
            combined[name] = combined[name].fillna(0.0)

    combined["has_event_history"] = (
        combined[[f"event_count_{max(windows)}d"]].fillna(0.0).to_numpy().ravel() > 0
    ).astype(float)

    # Velocity: recent burn rate against the medium-term baseline. This is the
    # feature the real-time re-scoring engine keys on in phase 6.
    short, long = min(windows), sorted(windows)[len(windows) // 2]
    combined["outflow_velocity_ratio"] = _rate_ratio(combined, f"outflow_{short}d", short, f"outflow_{long}d", long)
    combined["event_count_velocity_ratio"] = _rate_ratio(
        combined, f"event_count_{short}d", short, f"event_count_{long}d", long
    )

    combined = combined[sorted(combined.columns)]
    combined.index = pd.Index(keys, name="customer_id")
    return combined


def _rate_ratio(frame: pd.DataFrame, short_col: str, short_days: int, long_col: str, long_days: int) -> pd.Series:
    """Per-day rate in the short window over the per-day rate in the long window."""
    short_rate = frame[short_col].to_numpy(dtype=float) / short_days
    long_rate = frame[long_col].to_numpy(dtype=float) / long_days
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(long_rate > 0, short_rate / long_rate, np.nan)
    return pd.Series(ratio, index=frame.index)


def _expected_columns(windows: tuple[int, ...]) -> dict[str, str]:
    """Every column this module promises to emit, and whether it is a count."""
    columns: dict[str, str] = {}
    for window in windows:
        for prefix in ("event_count", "inflow", "outflow", "net_flow", "max_abs_amount", "distinct_counterparty_countries"):
            columns[f"{prefix}_{window}d"] = "count"
        for prefix in ("foreign_event_ratio", "night_event_ratio"):
            columns[f"{prefix}_{window}d"] = "ratio"
    for event_type in TRACKED_EVENT_TYPES:
        columns[f"{event_type}_count_{TYPE_WINDOW}d"] = "count"
        columns[f"{event_type}_amount_{TYPE_WINDOW}d"] = "count"
    columns[f"trigger_event_count_{TYPE_WINDOW}d"] = "count"
    for label in ("event", "trigger_event", "missed_payment", "cash_deposit"):
        columns[f"days_since_last_{label}"] = "recency"
    return columns


def event_feature_specs(windows: tuple[int, ...] = EVENT_WINDOWS) -> list[FeatureSpec]:
    """Contract entries for everything :func:`build_event_features` emits."""
    specs: list[FeatureSpec] = []
    for window in windows:
        specs += [
            FeatureSpec(f"event_count_{window}d", "numeric", "event", f"Events in the trailing {window} days.", minimum=0),
            FeatureSpec(f"inflow_{window}d", "numeric", "event", f"Money in over {window} days.", minimum=0),
            FeatureSpec(f"outflow_{window}d", "numeric", "event", f"Money out over {window} days.", minimum=0),
            FeatureSpec(f"net_flow_{window}d", "numeric", "event", f"Inflow minus outflow over {window} days."),
            FeatureSpec(f"max_abs_amount_{window}d", "numeric", "event", f"Largest single event over {window} days.", minimum=0),
            FeatureSpec(
                f"distinct_counterparty_countries_{window}d", "numeric", "event",
                f"Distinct counterparty jurisdictions over {window} days.", minimum=0,
            ),
            FeatureSpec(
                f"foreign_event_ratio_{window}d", "numeric", "event",
                f"Share of events with a foreign counterparty over {window} days.", minimum=0, maximum=1,
            ),
            FeatureSpec(
                f"night_event_ratio_{window}d", "numeric", "event",
                f"Share of events outside normal hours over {window} days.", minimum=0, maximum=1,
            ),
        ]
    for event_type in TRACKED_EVENT_TYPES:
        specs += [
            FeatureSpec(
                f"{event_type}_count_{TYPE_WINDOW}d", "numeric", "event",
                f"Count of {event_type.replace('_', ' ')} events over {TYPE_WINDOW} days.", minimum=0,
            ),
            FeatureSpec(
                f"{event_type}_amount_{TYPE_WINDOW}d", "numeric", "event",
                f"Total value of {event_type.replace('_', ' ')} events over {TYPE_WINDOW} days.", minimum=0,
            ),
        ]
    specs += [
        FeatureSpec(
            f"trigger_event_count_{TYPE_WINDOW}d", "numeric", "event",
            f"Re-scoring trigger events over {TYPE_WINDOW} days.", minimum=0,
        ),
        FeatureSpec("days_since_last_event", "numeric", "event", "Days since the most recent event.", minimum=0),
        FeatureSpec("days_since_last_trigger_event", "numeric", "event", "Days since the most recent trigger event.", minimum=0),
        FeatureSpec("days_since_last_missed_payment", "numeric", "event", "Days since the most recent missed payment.", minimum=0),
        FeatureSpec("days_since_last_cash_deposit", "numeric", "event", "Days since the most recent cash deposit.", minimum=0),
        FeatureSpec("has_event_history", "indicator", "event", "Customer has any event history at all.", minimum=0, maximum=1),
        FeatureSpec("outflow_velocity_ratio", "numeric", "event", "Short-window outflow rate over the medium-window rate.", minimum=0),
        FeatureSpec("event_count_velocity_ratio", "numeric", "event", "Short-window event rate over the medium-window rate.", minimum=0),
    ]
    return sorted(specs, key=lambda spec: spec.name)
