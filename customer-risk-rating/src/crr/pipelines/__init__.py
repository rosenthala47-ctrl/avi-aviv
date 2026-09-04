"""Event-driven re-scoring: trigger matching, debounce, and band-change notification."""

from crr.pipelines.notifications import (
    BandChangeNotification,
    InMemoryNotificationSink,
    JsonNotificationFormatter,
    LoggingNotificationSink,
    MultiNotificationSink,
    NotificationSink,
    configure_notification_logging,
)
from crr.pipelines.rescoring import EventInput, RescoringEngine, RescoringOutcome, event_input_from_stored

__all__ = [
    "BandChangeNotification",
    "EventInput",
    "InMemoryNotificationSink",
    "JsonNotificationFormatter",
    "LoggingNotificationSink",
    "MultiNotificationSink",
    "NotificationSink",
    "RescoringEngine",
    "RescoringOutcome",
    "configure_notification_logging",
    "event_input_from_stored",
]
