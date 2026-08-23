"""SQLAlchemy ORM models for scores and batch jobs.

Written in SQLAlchemy 2.0 style. Tested against SQLite (no server needed), which
exercises the same ORM code that targets PostgreSQL in production. The two
dialects differ in a few places that matter here and are handled explicitly:

* JSON — ``JSON`` maps to ``jsonb`` on PostgreSQL and to a TEXT-backed JSON on
  SQLite, so the full explanation round-trips on both.
* Timestamps — stored timezone-aware; on SQLite that is emulated, on PostgreSQL
  it is ``timestamptz``.

The ``score_history`` table is append-only: every score ever served is retained,
because "reproduce the decision you made on this customer two years ago" is a
question a regulator will actually ask.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ScoreRecord(Base):
    """One score served for one customer at one point in time. Append-only."""

    __tablename__ = "score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scored_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    model_band: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_band: Mapped[str] = mapped_column(String(16), nullable=False)
    band_floor_applied: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    credit_probability: Mapped[float] = mapped_column(Float, nullable=False)
    financial_crime_probability: Mapped[float] = mapped_column(Float, nullable=False)
    requires_review: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)

    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    audience: Mapped[str] = mapped_column(String(16), nullable=False, default="internal")

    # The exact customer input that produced this score — what makes
    # reproducibility literal (recompute from this, not just verify a hash
    # against it) and what the policy simulator replays against a proposed
    # rule set (crr.rules.simulate).
    customer_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Full explanation payload (every SHAP factor and fired rule, unfiltered),
    # so GET /explain reads and audience-filters rather than recomputes.
    explanation: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_score_customer_time", "customer_id", "scored_at"),
        Index("ix_score_input_hash", "input_hash"),
    )


class EventRecord(Base):
    """One transaction/lifecycle event received for a customer. Append-only.

    This is what makes phase 6's "recompute only the features the event
    invalidates" claim literal rather than aspirational: the re-scoring engine
    rebuilds the event-derived feature block from this log plus the newly
    arrived event, while the customer's static profile is served from the last
    stored ``ScoreRecord.customer_snapshot`` — the caller pushing one event
    never needs to resend the other ~65 profile fields.
    """

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    counterparty_country: Mapped[str] = mapped_column(String(3), nullable=False, default="IL")
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="online")
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_event_customer_time", "customer_id", "event_ts"),)


class JobRecord(Base):
    """A batch-scoring job."""

    __tablename__ = "batch_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
