"""Event-driven re-scoring (requirement 4a): the score moves when something
happens, not once a year.

The design in one sentence: **every event is stored, only a matched and
non-debounced trigger event causes a re-score, and only an actual band change
causes a notification.** Three separate thresholds for three separate concerns
— completeness of the feature history, cost of re-scoring, and cost of
interrupting a human — and conflating any two of them produces either missed
signal or alert fatigue.

Recomputing only what changed
------------------------------
A caller pushing one event does not need to resend the customer's ~65-field
profile. The engine rebuilds the customer's input from the **stored snapshot**
of their last score (``ScoreRepository`` already keeps this — see
``crr.api.repository.StoredScore.customer_snapshot``, added in phase 5) plus
the **full event log** (``EventRepository``) including the newly arrived event.
The static profile block is therefore never recomputed from a stale or
re-supplied payload — only the event-derived feature block, which is
inherently a function of the trailing window and does need to see the new
event to reflect it. Finer-grained incremental maintenance of individual
rolling-window aggregates (patching one cell instead of recomputing the window)
is a legitimate further optimisation, not attempted here — see
``docs/ROADMAP.md`` for why it is not needed to clear the 5-second exit
criterion by more than two orders of magnitude.

A customer must have been scored at least once before an event alone can
re-score them: there is no snapshot to build a profile from otherwise. The
first score for a new customer is always a normal ``POST /api/v1/score`` call
with their full profile.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Literal

from crr.api.cache import KeyValueStore
from crr.api.projections import assessment_to_stored
from crr.api.repository import EventRepository, ScoreRepository, StoredEvent
from crr.api.scoring import Assessment, ScoringService
from crr.pipelines.notifications import BandChangeNotification, NotificationSink

Reason = Literal["triggered", "debounced", "no_trigger", "not_yet_scored", "stale"]

_DEBOUNCE_KEY_PREFIX = "rescoring:debounce"


@dataclass(frozen=True)
class RescoringOutcome:
    """What happened when one event (or the staleness sweep) was considered."""

    customer_id: str
    reason: Reason
    rescored: bool
    notified: bool
    triggered_by: str
    assessment: Assessment | None = None
    previous_band: str | None = None

    @property
    def band_changed(self) -> bool:
        return (
            self.assessment is not None
            and self.previous_band is not None
            and self.assessment.risk_band != self.previous_band
        )


@dataclass
class EventInput:
    """The event fields the engine needs, independent of transport (API
    request body, a replayed CSV row, a queue message)."""

    event_ts: dt.datetime
    event_type: str
    amount: float
    counterparty_country: str = "IL"
    channel: str = "online"
    event_id: str = field(default_factory=lambda: f"EVT-{uuid.uuid4().hex[:12]}")


class RescoringEngine:
    """Wires the event log, score history, debounce cache and notification
    sink together around one ``ScoringService``. Re-reads the policy's
    ``rescoring`` section fresh on every call — a risk manager retuning a
    debounce window or adding a trigger takes effect on the next event, the
    same "no deploy" property the phase 5 rule engine has."""

    def __init__(
        self,
        service: ScoringService,
        events: EventRepository,
        scores: ScoreRepository,
        cache: KeyValueStore,
        notifications: NotificationSink,
    ) -> None:
        self.service = service
        self.events = events
        self.scores = scores
        self.cache = cache
        self.notifications = notifications

    # ---- event-triggered path ---------------------------------------------

    def ingest_event(
        self, customer_id: str, event: EventInput, *, now: dt.datetime | None = None
    ) -> RescoringOutcome:
        """Record one event and re-score if it matches a trigger and clears
        debounce. Always stores the event first, regardless of outcome — every
        event contributes to the trailing-window features the NEXT re-score
        (triggered or staleness-swept) will see, whether or not it is itself a
        trigger type."""
        now = now or dt.datetime.now(dt.UTC)
        self.events.append(
            StoredEvent(
                event_id=event.event_id, customer_id=customer_id, event_ts=event.event_ts,
                event_type=event.event_type, amount=event.amount,
                counterparty_country=event.counterparty_country, channel=event.channel, received_at=now,
            )
        )

        policy = self.service.policy
        trigger = policy.rescoring.trigger_for(event.event_type)
        if trigger is None or not trigger.matches(event.event_type, event.amount):
            return RescoringOutcome(customer_id, "no_trigger", False, False, event.event_type)

        if trigger.debounce_minutes > 0 and self._is_debounced(customer_id, trigger.event_type):
            return RescoringOutcome(customer_id, "debounced", False, False, event.event_type)

        prior = self.scores.latest(customer_id)
        if prior is None:
            return RescoringOutcome(customer_id, "not_yet_scored", False, False, event.event_type)

        if trigger.debounce_minutes > 0:
            self._mark_debounced(customer_id, trigger.event_type, trigger.debounce_minutes)

        return self._rescore(customer_id, prior, triggered_by=event.event_type, now=now)

    # ---- staleness sweep ---------------------------------------------------

    def sweep_stale(self, *, now: dt.datetime | None = None, limit: int = 10_000) -> list[RescoringOutcome]:
        """Re-score every customer whose last score is older than the
        policy's ``max_score_age_days`` — the "whatever the events say"
        fallback. Catches customers whose accumulated non-trigger events
        (small purchases, salary credits) should still eventually move the
        score even though none of them individually crossed a trigger."""
        now = now or dt.datetime.now(dt.UTC)
        policy = self.service.policy
        cutoff = now - dt.timedelta(days=policy.rescoring.max_score_age_days)
        outcomes = []
        for customer_id in self.scores.stale_customers(cutoff, limit=limit):
            prior = self.scores.latest(customer_id)
            if prior is None:  # pragma: no cover — stale_customers only returns scored customers
                continue
            outcomes.append(
                self._rescore(
                    customer_id, prior, triggered_by=f"stale:{policy.rescoring.max_score_age_days}d", now=now
                )
            )
        return outcomes

    # ---- shared machinery ---------------------------------------------------

    def _rescore(self, customer_id: str, prior, *, triggered_by: str, now: dt.datetime) -> RescoringOutcome:
        event_history = [
            {
                "event_id": e.event_id, "event_ts": e.event_ts, "event_type": e.event_type, "amount": e.amount,
                "counterparty_country": e.counterparty_country, "channel": e.channel,
            }
            for e in self.events.history(customer_id)
        ]
        # The stored snapshot's own snapshot_date is stale — it is when the ~65
        # profile fields were true, not when this re-score is happening. Advance
        # only that one field to `now` before handing the snapshot back to the
        # feature pipeline: snapshot_date is never used to derive any profile
        # feature (crr.features.pipeline.DROP_COLUMNS drops it before modelling),
        # its only job is anchoring the event leakage guard's "at or before"
        # boundary. Leaving it at the original score's date would make every
        # event since then look like it is in the future and get silently
        # dropped — the trailing-window features would never move and this
        # engine could not do the one thing it exists to do.
        snapshot = dict(prior.customer_snapshot)
        snapshot["snapshot_date"] = now.date()
        assessment = self.service.score(snapshot, event_history, audience="internal", explain=True, now=now)
        self.scores.save(assessment_to_stored(assessment))

        notified = False
        policy = self.service.policy
        band_changed = assessment.risk_band != prior.risk_band
        if band_changed or not policy.rescoring.notify_on_band_change_only:
            self.notifications.notify(
                BandChangeNotification(
                    customer_id=customer_id, previous_band=prior.risk_band, new_band=assessment.risk_band,
                    risk_score=assessment.risk_score, requires_review=assessment.requires_review,
                    triggered_by=triggered_by, scored_at=assessment.scored_at,
                )
            )
            notified = True

        reason: Reason = "stale" if triggered_by.startswith("stale:") else "triggered"
        return RescoringOutcome(
            customer_id=customer_id, reason=reason, rescored=True, notified=notified,
            triggered_by=triggered_by, assessment=assessment, previous_band=prior.risk_band,
        )

    def _debounce_key(self, customer_id: str, event_type: str) -> str:
        return f"{_DEBOUNCE_KEY_PREFIX}:{customer_id}:{event_type}"

    def _is_debounced(self, customer_id: str, event_type: str) -> bool:
        return self.cache.get(self._debounce_key(customer_id, event_type)) is not None

    def _mark_debounced(self, customer_id: str, event_type: str, debounce_minutes: int) -> None:
        self.cache.set(self._debounce_key(customer_id, event_type), "1", ttl_seconds=debounce_minutes * 60)


def event_input_from_stored(event: StoredEvent) -> EventInput:
    """Rebuild an :class:`EventInput` from a persisted event — used when
    replaying stored history (the verification script, a future backfill)."""
    return EventInput(
        event_ts=event.event_ts, event_type=event.event_type, amount=event.amount,
        counterparty_country=event.counterparty_country, channel=event.channel, event_id=event.event_id,
    )
