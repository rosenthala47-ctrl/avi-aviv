"""Structured audit logging.

Every score emits one structured line carrying exactly what a reviewer needs to
reconstruct the decision: who was scored, when, by which model and policy version,
the input hash, the output, and how long it took. The durable copy lives in the
score-history table; this is the streaming, greppable, ship-to-your-log-aggregator
copy.

Emitting the fields as a JSON object rather than a formatted string is deliberate:
audit logs get queried, and "find every Extreme-band score served by model version
X last Tuesday" should be a filter, not a regex.
"""

from __future__ import annotations

import json
import logging
from typing import Any

audit_logger = logging.getLogger("crr.audit")


def emit(record: dict[str, Any]) -> None:
    """Write one audit record as a structured JSON log line."""
    audit_logger.info("score.served", extra={"audit": record})


class JsonAuditFormatter(logging.Formatter):
    """Render audit records as single-line JSON, other logs as plain text."""

    def format(self, record: logging.LogRecord) -> str:
        audit = getattr(record, "audit", None)
        if audit is not None:
            return json.dumps({"event": record.getMessage(), **audit}, default=str)
        return super().format(record)


def configure_audit_logging(level: int = logging.INFO) -> None:
    """Attach a JSON handler to the audit logger (idempotent)."""
    if any(getattr(h, "_crr_audit", False) for h in audit_logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonAuditFormatter())
    handler._crr_audit = True  # type: ignore[attr-defined]
    audit_logger.addHandler(handler)
    audit_logger.setLevel(level)
    audit_logger.propagate = False
