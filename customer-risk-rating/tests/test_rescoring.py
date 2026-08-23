"""Tests for event-driven re-scoring (crr.pipelines.rescoring).

Trigger thresholds (min_amount, debounce_minutes) are read from the real,
live policy rather than hardcoded here — the same reason crr.policy is the
one source of truth everywhere else in this project: a threshold edited in
config/risk_policy.yaml should not silently desync a duplicated copy in a
test file.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from crr.api.cache import InMemoryCache
from crr.api.projections import assessment_to_stored
from crr.api.repository import InMemoryEventRepository, InMemoryScoreRepository
from crr.api.scoring import ModelBundle, ScoringService
from crr.data.synthetic import GeneratorConfig, generate
from crr.pipelines.notifications import InMemoryNotificationSink
from crr.pipelines.rescoring import EventInput, RescoringEngine

REPO_ROOT = pytest.importorskip("pathlib").Path(__file__).resolve().parents[1]
PII = ["full_name", "national_id", "email", "phone", "address_line", "split"]


def _needs_models() -> bool:
    return not (REPO_ROOT / "models" / "default_12m" / "model.txt").exists()


pytestmark = pytest.mark.skipif(
    _needs_models(),
    reason="trained models not present; run scripts/train_baseline.py for both targets",
)


@pytest.fixture(scope="module")
def bundle():
    return ModelBundle.load()


@pytest.fixture(scope="module")
def customers():
    dataset = generate(GeneratorConfig(n_customers=20, seed=11))
    rows = []
    for _, row in dataset.customers.iterrows():
        record = row.to_dict()
        for key in PII:
            record.pop(key, None)
        rows.append({k: (None if pd.isna(v) else v) for k, v in record.items()})
    return rows


@pytest.fixture
def customer(customers):
    return dict(customers[0])


@pytest.fixture
def rig(bundle):
    """A fresh engine plus its own service/repos, isolated per test — in
    particular a fresh InMemoryCache, since debounce state must not leak
    between tests."""
    service = ScoringService(bundle)
    scores = InMemoryScoreRepository()
    events = InMemoryEventRepository()
    cache = InMemoryCache()
    notifications = InMemoryNotificationSink()
    engine = RescoringEngine(service, events, scores, cache, notifications)
    return engine, service, scores, events, notifications


def _snap_utc(customer: dict) -> dt.datetime:
    return pd.Timestamp(customer["snapshot_date"]).to_pydatetime().replace(tzinfo=dt.UTC)


def _score_and_save(service, scores, customer, *, now):
    assessment = service.score(dict(customer), [], audience="internal", explain=True, now=now)
    scores.save(assessment_to_stored(assessment))
    return assessment


def _trigger(service, event_type: str):
    trigger = service.policy.rescoring.trigger_for(event_type)
    assert trigger is not None, f"policy has no rescoring trigger for {event_type!r} -- test needs updating"
    return trigger


# --------------------------------------------------------------------------
# Storage vs. triggering are separate concerns
# --------------------------------------------------------------------------


def test_event_before_any_score_is_stored_but_not_rescored(rig, customer):
    engine, _service, _scores, events, _notifications = rig
    now = _snap_utc(customer)
    outcome = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=now, event_type="missed_payment", amount=1), now=now
    )
    assert outcome.reason == "not_yet_scored"
    assert outcome.rescored is False
    assert outcome.notified is False
    assert outcome.assessment is None
    stored = events.history(customer["customer_id"])
    assert len(stored) == 1 and stored[0].event_type == "missed_payment"


def test_non_trigger_event_type_is_stored_but_not_rescored(rig, customer):
    engine, service, scores, events, _notifications = rig
    now = _snap_utc(customer)
    _score_and_save(service, scores, customer, now=now)

    outcome = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=now, event_type="salary_credit", amount=8000), now=now
    )
    assert outcome.reason == "no_trigger"
    assert outcome.rescored is False
    assert len(events.history(customer["customer_id"])) == 1


# --------------------------------------------------------------------------
# Trigger matching
# --------------------------------------------------------------------------


def test_event_below_min_amount_does_not_trigger(rig, customer):
    engine, service, scores, _events, _notifications = rig
    trigger = _trigger(service, "cash_deposit")
    assert trigger.min_amount is not None, "test assumes cash_deposit has a min_amount in the real policy"
    now = _snap_utc(customer)
    _score_and_save(service, scores, customer, now=now)

    outcome = engine.ingest_event(
        customer["customer_id"],
        EventInput(event_ts=now, event_type="cash_deposit", amount=trigger.min_amount - 1),
        now=now,
    )
    assert outcome.reason == "no_trigger"
    assert outcome.rescored is False


def test_event_at_or_above_min_amount_triggers(rig, customer):
    engine, service, scores, _events, _notifications = rig
    trigger = _trigger(service, "cash_deposit")
    now = _snap_utc(customer)
    _score_and_save(service, scores, customer, now=now)

    outcome = engine.ingest_event(
        customer["customer_id"],
        EventInput(event_ts=now, event_type="cash_deposit", amount=trigger.min_amount),
        now=now,
    )
    assert outcome.reason == "triggered"
    assert outcome.rescored is True
    assert outcome.assessment is not None


# --------------------------------------------------------------------------
# Debounce
# --------------------------------------------------------------------------


def test_debounce_suppresses_immediate_repeat(rig, customer):
    engine, service, scores, _events, _notifications = rig
    trigger = _trigger(service, "cash_deposit")
    assert trigger.debounce_minutes > 0, "test assumes cash_deposit debounces in the real policy"
    now = _snap_utc(customer)
    _score_and_save(service, scores, customer, now=now)

    first = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=now, event_type="cash_deposit", amount=trigger.min_amount), now=now
    )
    second = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=now, event_type="cash_deposit", amount=trigger.min_amount), now=now
    )
    assert first.reason == "triggered"
    assert second.reason == "debounced"
    assert second.rescored is False


def test_zero_debounce_event_type_always_fires(rig, customer):
    engine, service, scores, _events, _notifications = rig
    trigger = _trigger(service, "missed_payment")
    assert trigger.debounce_minutes == 0, "test assumes missed_payment has no debounce in the real policy"
    now = _snap_utc(customer)
    _score_and_save(service, scores, customer, now=now)

    first = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=now, event_type="missed_payment", amount=1), now=now
    )
    second = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=now, event_type="missed_payment", amount=1), now=now
    )
    assert first.reason == "triggered"
    assert second.reason == "triggered"


def test_debounce_is_scoped_per_event_type(rig, customer):
    """Debouncing cash_deposit must not suppress an unrelated trigger type for
    the same customer arriving right after."""
    engine, service, scores, _events, _notifications = rig
    cash = _trigger(service, "cash_deposit")
    now = _snap_utc(customer)
    _score_and_save(service, scores, customer, now=now)

    first = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=now, event_type="cash_deposit", amount=cash.min_amount), now=now
    )
    other = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=now, event_type="missed_payment", amount=1), now=now
    )
    assert first.reason == "triggered"
    assert other.reason == "triggered"


# --------------------------------------------------------------------------
# The core promise: a new event actually moves the score
# --------------------------------------------------------------------------


def test_event_after_the_original_snapshot_date_moves_the_score(rig, customer):
    """Regression test for a real bug: the engine used to re-score using the
    ORIGINAL score's stale snapshot_date unchanged, so any event dated after it
    — i.e. every realistic event, since a re-score is triggered by something
    that just happened — was silently treated as being in the future by the
    leakage guard and dropped. The recompute would run and 'succeed' but be a
    mathematical no-op. Fixed in RescoringEngine._rescore by advancing
    snapshot_date to `now` (safe: crr.features.pipeline.DROP_COLUMNS drops it
    before modelling, so it only ever anchors the event window)."""
    engine, service, scores, _events, _notifications = rig
    now = _snap_utc(customer)
    initial = _score_and_save(service, scores, customer, now=now)

    later = now + dt.timedelta(days=5)
    outcome = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=later, event_type="missed_payment", amount=1), now=later
    )
    assert outcome.reason == "triggered"
    assert outcome.assessment is not None
    assert outcome.assessment.risk_score != initial.risk_score


def test_multiple_post_snapshot_events_accumulate(rig, customer):
    """A second, later event must see the first one still in the window --
    the event log is cumulative, not a rolling window of size one."""
    engine, service, scores, events, _notifications = rig
    now = _snap_utc(customer)
    _score_and_save(service, scores, customer, now=now)

    day5 = now + dt.timedelta(days=5)
    engine.ingest_event(customer["customer_id"], EventInput(event_ts=day5, event_type="missed_payment", amount=1), now=day5)
    assert len(events.history(customer["customer_id"])) == 1

    day10 = now + dt.timedelta(days=10)
    engine.ingest_event(customer["customer_id"], EventInput(event_ts=day10, event_type="missed_payment", amount=1), now=day10)
    assert len(events.history(customer["customer_id"])) == 2


# --------------------------------------------------------------------------
# Notification: only on an actual band change
# --------------------------------------------------------------------------


def test_notification_fires_when_the_band_actually_changes(rig, customer):
    engine, service, scores, _events, notifications = rig
    now = _snap_utc(customer)
    real = _score_and_save(service, scores, customer, now=now)

    # Plant a prior record whose band differs from what a real recompute will
    # produce, to force a genuine band-change path deterministically rather
    # than hoping a random customer/event combination happens to flip one.
    # InMemoryScoreRepository.latest() is max(scores, key=scored_at); an equal
    # scored_at would lose the tie to the record saved first, so this one
    # needs a strictly later timestamp to actually become "latest".
    stored = assessment_to_stored(real)
    stored.risk_band = "Extreme" if real.risk_band != "Extreme" else "Low"
    stored.scored_at = real.scored_at + dt.timedelta(seconds=1)
    scores.save(stored)

    later = now + dt.timedelta(days=1)
    outcome = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=later, event_type="missed_payment", amount=1), now=later
    )
    assert outcome.band_changed is True
    assert outcome.notified is True
    assert len(notifications.sent) == 1
    sent = notifications.sent[0]
    assert sent.previous_band == stored.risk_band
    assert sent.new_band == outcome.assessment.risk_band
    assert sent.triggered_by == "missed_payment"


def test_no_notification_when_the_band_does_not_change(rig, customer):
    """The real policy has notify_on_band_change_only: true, so a triggered
    re-score that lands back in the same band must stay silent.

    Whether one missed_payment event actually flips this particular
    customer's band is not something to guess at: probe the model directly
    (mirroring exactly what RescoringEngine._rescore does internally — see
    its comment on why snapshot_date advances to `now`) to learn the real
    post-event band, then plant a prior at exactly that band. band_changed is
    then False by construction, not by hoping the fixture customer cooperates."""
    engine, service, scores, _events, notifications = rig
    assert service.policy.rescoring.notify_on_band_change_only is True
    now = _snap_utc(customer)
    real = _score_and_save(service, scores, customer, now=now)

    later = now + dt.timedelta(days=1)
    probe_event = {
        "event_id": "EVT-PROBE", "event_ts": later, "event_type": "missed_payment", "amount": 1,
        "counterparty_country": "IL", "channel": "online",
    }
    probe_snapshot = dict(real.customer_snapshot)
    probe_snapshot["snapshot_date"] = later.date()
    probe = service.score(probe_snapshot, [probe_event], audience="internal", explain=False, now=later)

    # See the sibling test above for why scored_at must strictly advance:
    # InMemoryScoreRepository.latest() loses ties to the record saved first.
    stored = assessment_to_stored(real)
    stored.risk_band = probe.risk_band
    stored.scored_at = real.scored_at + dt.timedelta(seconds=1)
    scores.save(stored)

    outcome = engine.ingest_event(
        customer["customer_id"], EventInput(event_ts=later, event_type="missed_payment", amount=1), now=later
    )
    assert outcome.band_changed is False
    assert outcome.notified is False
    assert len(notifications.sent) == 0


# --------------------------------------------------------------------------
# Staleness sweep
# --------------------------------------------------------------------------


def test_sweep_stale_rescored_customers_past_max_age(rig, customer):
    engine, service, scores, _events, _notifications = rig
    max_age = service.policy.rescoring.max_score_age_days
    now = _snap_utc(customer)
    _score_and_save(service, scores, customer, now=now)

    far_future = now + dt.timedelta(days=max_age + 5)
    outcomes = engine.sweep_stale(now=far_future)

    matching = [o for o in outcomes if o.customer_id == customer["customer_id"]]
    assert len(matching) == 1
    assert matching[0].rescored is True
    assert matching[0].reason == "stale"
    assert matching[0].triggered_by == f"stale:{max_age}d"
    assert matching[0].assessment is not None


def test_sweep_stale_ignores_recently_scored_customers(rig, customer):
    engine, service, scores, _events, _notifications = rig
    now = _snap_utc(customer)
    _score_and_save(service, scores, customer, now=now)

    soon = now + dt.timedelta(days=1)
    outcomes = engine.sweep_stale(now=soon)
    assert not any(o.customer_id == customer["customer_id"] for o in outcomes)
