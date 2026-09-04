"""Notifications for a band change — the "alert fatigue kills these systems"
half of requirement 4a.

A re-score happens on every matched, non-debounced event, but a **notification**
fires only when the outcome actually changed something a downstream consumer
(a review queue, a case-management system) needs to act on: the published band
moved. Scoring a customer twelve times in an hour because they made twelve card
payments and getting the same "Low" answer back twelve times is not a signal
worth interrupting anyone for; getting "Low" then "Extreme" is.

Behind an interface for the same reason every other backend in this project is:
the service runs and is fully testable with no infrastructure, and the
production implementation (a webhook, a queue publisher) is a thin swap once
there is somewhere real to send it to.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Protocol

notification_logger = logging.getLogger("crr.notifications")


@dataclass(frozen=True)
class BandChangeNotification:
    """One published-band change, worth telling someone about."""

    customer_id: str
    previous_band: str
    new_band: str
    risk_score: float
    requires_review: bool
    triggered_by: str
    """The event type or reason that caused this re-score, e.g.
    'missed_payment' or 'stale:92d' — so a downstream consumer can tell a
    genuine compliance trigger apart from a routine staleness sweep."""
    scored_at: dt.datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "customer_id": self.customer_id,
            "previous_band": self.previous_band,
            "new_band": self.new_band,
            "risk_score": round(self.risk_score, 2),
            "requires_review": self.requires_review,
            "triggered_by": self.triggered_by,
            "scored_at": self.scored_at.isoformat(),
        }


class NotificationSink(Protocol):
    def notify(self, notification: BandChangeNotification) -> None: ...


class InMemoryNotificationSink:
    """Captures notifications in a list. The default; testable and demoable
    with no infrastructure, same as every other in-memory backend here."""

    def __init__(self) -> None:
        self.sent: list[BandChangeNotification] = []

    def notify(self, notification: BandChangeNotification) -> None:
        self.sent.append(notification)


class LoggingNotificationSink:
    """Emits a structured JSON log line per notification.

    The realistic zero-infrastructure production default: a downstream system
    already tailing structured logs (the same pattern ``crr.api.audit`` uses
    for served scores) picks these up with no new integration. A real
    deployment swaps this for a webhook or queue publisher behind the same
    ``NotificationSink`` interface; nothing else in the pipeline changes.
    """

    def notify(self, notification: BandChangeNotification) -> None:
        notification_logger.info(
            "rescoring.band_changed",
            extra={"audit": notification.to_dict()},
        )


class MultiNotificationSink:
    """Fan out to several sinks — e.g. log AND capture in-memory for tests."""

    def __init__(self, sinks: list[NotificationSink]) -> None:
        self._sinks = sinks

    def notify(self, notification: BandChangeNotification) -> None:
        for sink in self._sinks:
            sink.notify(notification)


class JsonNotificationFormatter(logging.Formatter):
    """Render notification records as single-line JSON, mirroring
    ``crr.api.audit.JsonAuditFormatter``."""

    def format(self, record: logging.LogRecord) -> str:
        audit = getattr(record, "audit", None)
        if audit is not None:
            return json.dumps({"event": record.getMessage(), **audit}, default=str)
        return super().format(record)


def configure_notification_logging(level: int = logging.INFO) -> None:
    """Attach a JSON handler to the notification logger (idempotent)."""
    if any(getattr(h, "_crr_notifications", False) for h in notification_logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonNotificationFormatter())
    handler._crr_notifications = True  # type: ignore[attr-defined]
    notification_logger.addHandler(handler)
    notification_logger.setLevel(level)
    notification_logger.propagate = False
