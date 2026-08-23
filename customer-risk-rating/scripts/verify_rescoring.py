#!/usr/bin/env python3
"""Phase 6 exit criterion, measured against real trained models: "Trigger-to-
updated-score under 5 seconds at p95, replayable against the generated event
stream, with a measured false-alert rate."

Replay methodology
------------------
The synthetic generator's point-in-time discipline means every event it
produces is dated at or before its owning customer's ``snapshot_date`` — there
is no "future" event stream to replay as-is. So for each customer this script
rolls their snapshot back by ``--replay-days`` days, computes an initial score
from only the events at or before that rolled-back date (a normal, honest
score — nothing here is exposed to its own future), and then replays the
remaining events (the ones between the rollback point and the real snapshot)
one at a time through the real ``RescoringEngine``, using each event's own
timestamp as the simulated "now". This is the same trajectory a real customer
follows: scored once, then living their life while the engine watches.

Only the *timed* calls — an event that matches a trigger and clears debounce —
count toward the p95. ``false_alert_rate`` is the fraction of those triggered,
non-debounced re-scores whose recomputed band was the SAME as before: a
recompute that fired for nothing, in the sense of "nothing a downstream
consumer would see" (the separate, and by construction always-zero, question
of a wrongly-fired *notification* is not what this measures — notification
already gates on the band actually changing; see ``crr.pipelines.rescoring``).

A known limitation, stated rather than hidden: the debounce cache's TTL is
real wall-clock time (``time.monotonic()``, shared with the idempotency and
hot-score caches elsewhere in the API — see ``crr.api.cache``), not the
simulated ``now`` this replay passes around. Replaying two same-type trigger
events for the same customer in quick succession can therefore show as
debounced here even when they are far apart in simulated time. Given
~1 event per customer per 15 days on average (``events_per_customer`` /
``event_window_days`` in the generator config) this is rare in practice, and
it only ever under-counts triggered re-scores, never inflates the pass — the
safe direction for a verification script to be wrong in.

Examples
--------
    python scripts/verify_rescoring.py
    python scripts/verify_rescoring.py --customers 1200 --replay-days 20
"""

from __future__ import annotations

import argparse
import gc
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

import datetime as dt  # noqa: E402

import numpy as np  # noqa: E402

from crr.api.cache import InMemoryCache  # noqa: E402
from crr.api.projections import assessment_to_stored  # noqa: E402
from crr.api.repository import InMemoryEventRepository, InMemoryScoreRepository  # noqa: E402
from crr.api.scoring import ModelBundle, ScoringService  # noqa: E402
from crr.data.synthetic import GeneratorConfig, generate  # noqa: E402
from crr.pipelines.notifications import InMemoryNotificationSink  # noqa: E402
from crr.pipelines.rescoring import EventInput, RescoringEngine  # noqa: E402

TARGET_P95_SECONDS = 5.0
PII_COLUMNS = ("full_name", "national_id", "email", "phone", "address_line", "split")


def _tune_gc() -> None:
    """Same tuning ``crr.api.app`` applies at startup — see its docstring.
    Measuring latency under different GC settings than production would be
    measuring the wrong thing."""
    gc.collect()
    gc.freeze()
    gc.set_threshold(50_000, 500, 1000)


def _customer_dict(row: dict) -> dict:
    cleaned = {k: v for k, v in row.items() if k not in PII_COLUMNS}
    return {k: (None if _is_nan(v) else v) for k, v in cleaned.items()}


def _is_nan(value: object) -> bool:
    return isinstance(value, float) and value != value


def _event_dict(row) -> dict:
    return {
        "event_id": row.event_id, "event_ts": row.event_ts, "event_type": row.event_type,
        "amount": float(row.amount), "counterparty_country": row.counterparty_country, "channel": row.channel,
    }


def _as_utc(value: dt.date | dt.datetime) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    return dt.datetime.combine(value, dt.time.min, tzinfo=dt.UTC)


def run(n_customers: int, replay_days: int, seed: int) -> dict:
    print(f"Generating {n_customers} synthetic customers with events (seed={seed})...")
    dataset = generate(GeneratorConfig(n_customers=n_customers, seed=seed, generate_events=True))
    events_by_customer = {cid: grp for cid, grp in dataset.events.groupby("customer_id", observed=True)}

    print("Loading trained model bundle...")
    bundle = ModelBundle.load()
    service = ScoringService(bundle)
    _tune_gc()

    scores = InMemoryScoreRepository()
    events_repo = InMemoryEventRepository()
    cache = InMemoryCache()
    notifications = InMemoryNotificationSink()
    engine = RescoringEngine(service, events_repo, scores, cache, notifications)

    latencies_s: list[float] = []
    score_deltas: list[float] = []
    band_changed_count = 0
    triggered_count = 0
    reason_counts: dict[str, int] = {}
    scored_customers = 0

    for _, crow in dataset.customers.iterrows():
        customer = _customer_dict(crow.to_dict())
        customer_id = customer["customer_id"]
        real_snapshot = customer["snapshot_date"]
        if isinstance(real_snapshot, str):
            real_snapshot = dt.date.fromisoformat(real_snapshot[:10])
        elif hasattr(real_snapshot, "date"):
            real_snapshot = real_snapshot.date()
        rollback_date = real_snapshot - dt.timedelta(days=replay_days)

        own_events = events_by_customer.get(customer_id)
        if own_events is None or own_events.empty:
            history_events, replay_events = [], []
        else:
            ts = own_events["event_ts"]
            ts_date = ts.dt.date if hasattr(ts, "dt") else ts
            before = own_events[ts_date <= rollback_date].sort_values("event_ts")
            after = own_events[ts_date > rollback_date].sort_values("event_ts")
            history_events = [_event_dict(r) for r in before.itertuples()]
            replay_events = [_event_dict(r) for r in after.itertuples()]

        initial_customer = dict(customer)
        initial_customer["snapshot_date"] = rollback_date
        initial = service.score(
            initial_customer, history_events, audience="internal", explain=False, now=_as_utc(rollback_date)
        )
        scores.save(assessment_to_stored(initial))
        scored_customers += 1
        prior_score = initial.risk_score

        for ev in replay_events:
            now = _as_utc(ev["event_ts"])
            event_input = EventInput(
                event_ts=ev["event_ts"], event_type=ev["event_type"], amount=ev["amount"],
                counterparty_country=ev["counterparty_country"], channel=ev["channel"], event_id=ev["event_id"],
            )
            started = time.perf_counter()
            outcome = engine.ingest_event(customer_id, event_input, now=now)
            elapsed = time.perf_counter() - started
            reason_counts[outcome.reason] = reason_counts.get(outcome.reason, 0) + 1
            if outcome.rescored:
                triggered_count += 1
                latencies_s.append(elapsed)
                score_deltas.append(abs(outcome.assessment.risk_score - prior_score))
                prior_score = outcome.assessment.risk_score
                if outcome.band_changed:
                    band_changed_count += 1

    p95 = float(np.percentile(latencies_s, 95)) if latencies_s else None
    p50 = float(np.percentile(latencies_s, 50)) if latencies_s else None
    false_alert_rate = (triggered_count - band_changed_count) / triggered_count if triggered_count else None

    return {
        "scored_customers": scored_customers,
        "reason_counts": reason_counts,
        "triggered_count": triggered_count,
        "band_changed_count": band_changed_count,
        "p50_latency_s": p50,
        "p95_latency_s": p95,
        "max_latency_s": max(latencies_s) if latencies_s else None,
        "false_alert_rate": false_alert_rate,
        "zero_delta_count": sum(1 for d in score_deltas if d < 1e-9),
        "mean_abs_score_delta": float(np.mean(score_deltas)) if score_deltas else None,
        "median_abs_score_delta": float(np.median(score_deltas)) if score_deltas else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--customers", type=int, default=600)
    parser.add_argument("--replay-days", type=int, default=20, help="how far back to roll the initial score")
    parser.add_argument("--seed", type=int, default=99)
    args = parser.parse_args(argv)

    print("=" * 74)
    print("PHASE 6 EXIT CRITERION — real-time re-scoring")
    print("=" * 74)

    result = run(args.customers, args.replay_days, args.seed)

    print()
    print(f"  customers scored (initial):        {result['scored_customers']}")
    print(f"  replayed-event outcomes by reason:  {result['reason_counts']}")
    print(f"  triggered (timed) re-scores:        {result['triggered_count']}")
    print(f"  of those, band actually changed:    {result['band_changed_count']}")
    print()

    if result["p95_latency_s"] is None:
        print("  [FAIL] trigger-to-updated-score p95 < 5s")
        print("         no triggered re-score occurred in this replay — nothing to measure. "
              "Try --customers higher or --replay-days larger.")
        return 1

    p95_ok = result["p95_latency_s"] < TARGET_P95_SECONDS
    print(f"  [{'PASS' if p95_ok else 'FAIL'}] trigger-to-updated-score p95 < {TARGET_P95_SECONDS:.0f}s")
    print(f"         p50={result['p50_latency_s'] * 1000:.1f}ms  p95={result['p95_latency_s'] * 1000:.1f}ms  "
          f"max={result['max_latency_s'] * 1000:.1f}ms  (n={result['triggered_count']})")
    print()
    print("  [INFO] false-alert rate (triggered re-score, band did not change)")
    print(f"         {result['false_alert_rate']:.1%} of {result['triggered_count']} triggered re-scores")
    print(f"         for scale: |Δrisk_score| on every triggered re-score — mean={result['mean_abs_score_delta']:.3f} "
          f"median={result['median_abs_score_delta']:.3f} (0-100 scale, 25-point-wide bands); "
          f"exact-zero deltas: {result['zero_delta_count']}/{result['triggered_count']}")
    print("         a high rate here with near-zero deltas would mean the recompute is a no-op; "
          "non-zero deltas that still rarely cross a 25-point band is bands being coarse, not a broken engine.")
    print("=" * 74)
    return 0 if p95_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
