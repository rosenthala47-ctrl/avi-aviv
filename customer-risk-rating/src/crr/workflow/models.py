"""SQLAlchemy ORM models for the case-management / workflow layer.

Everything the Streamlit console used to hold in ``st.session_state`` — case
decisions, review notes, the queue, custom rules, watchlist dispositions, user
accounts, and the audit trail — lives here instead, so it survives a browser
refresh, a new tab, and a process restart.

Deliberately a **separate declarative Base** from :mod:`crr.db.models` (which
owns the ML-serving tables: score history, events, extraction cache, batch
jobs). The two concerns run against potentially different databases and have
different lifecycles; keeping the metadata separate means ``create_all`` on the
workflow engine creates only the ``wf_*`` tables and nothing else, and a future
split into two physical databases needs no model surgery.

Written in SQLAlchemy 2.0 style and tested against SQLite (no server needed),
which exercises the same ORM code that targets PostgreSQL in production. The two
dialects differ in two places that matter and are handled at the engine
boundary (see :mod:`crr.workflow.db`): ``JSON`` maps to ``jsonb`` on PostgreSQL
and a TEXT-backed JSON on SQLite, and timestamps are stored timezone-aware
(``timestamptz`` on PostgreSQL, emulated on SQLite).

The ``wf_audit_log`` table is **append-only and tamper-evident**: every row
carries the hash of the previous row plus a hash of its own content, forming a
chain a verifier can walk to prove no entry was altered, reordered, or removed
after the fact (see :meth:`crr.workflow.store.WorkflowStore.verify_audit_chain`).
The store exposes no update or delete path for it — the same one-way guarantee a
real compliance audit trail enforces, here enforced structurally rather than by
convention.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata root for the workflow tables only — distinct from the
    ML-serving ``Base`` in :mod:`crr.db.models`."""


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --------------------------------------------------------------------------
# Identity & access
# --------------------------------------------------------------------------


class User(Base):
    """A real user account. The role here is the single source of truth for
    what the person can do — there is no session-state role switcher any more.
    """

    __tablename__ = "wf_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # Never a plaintext password. Stored as "pbkdf2_sha256$iterations$salt$hash"
    # (see crr.workflow.auth) — a per-user random salt and a high iteration
    # count, verified in constant time.
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class UserSession(Base):
    """One logged-in session. Only the SHA-256 of the bearer token is stored —
    the raw token is shown to the browser once and never persisted, so a leak
    of this table cannot be replayed as a login."""

    __tablename__ = "wf_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("wf_users.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------------------
# Case management
# --------------------------------------------------------------------------


class Case(Base):
    """One customer's case in the operations queue. Keyed by customer_id (one
    active case per customer, matching the queue's original semantics).

    ``result`` is the scoring-service response snapshot as it stood when the
    case was last scored — persisting it, rather than re-calling the model on
    every page load, is both faster and more correct for audit: it records what
    the model actually said at the moment a decision was taken. ``profile``
    includes the UI-only identity fields (name/DOB) the watchlist screen reads;
    those are never sent to the scoring API (see the app's Watchlist section)."""

    __tablename__ = "wf_cases"

    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_review")
    archetype: Mapped[str] = mapped_column(String(128), nullable=False, default="custom")
    profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    narratives: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scored_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")


class TimelineEvent(Base):
    """One entry on a case's timeline: a scoring, a decision, a note, or a
    re-scoring event. Heterogeneous per kind, so the kind-specific fields live
    in ``payload`` rather than a wide sparse table; the app reconstructs the
    flat dict it renders as ``{kind, at, actor, **payload}``. Append-only."""

    __tablename__ = "wf_timeline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("wf_cases.customer_id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)

    __table_args__ = (Index("ix_wf_timeline_customer_at", "customer_id", "at"),)


class CustomRule(Base):
    """A risk manager's dynamic rule from the Rule Builder. The full rule dict
    (conditions, combine, action) is kept in ``definition``; ``enabled``,
    ``name`` and ``position`` are promoted for ordering and quick filtering and
    kept in sync with the definition on every write."""

    __tablename__ = "wf_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class WatchlistEntry(Base):
    """One sanctioned/PEP/adverse-media party from a screening source —
    either a real OFAC/UN/EU sanctions record ingested by
    ``scripts/refresh_watchlists.py`` (see :mod:`crr.screening`), or one of
    the small set of fictional demo entries seeded on first run so the
    screening UI has something to show before anyone has run that script.

    ``source`` plus ``source_id`` is the natural key a refresh replaces on:
    :meth:`crr.workflow.store.WorkflowStore.replace_watchlist_source` deletes
    every row for one source and re-inserts the freshly parsed list in one
    transaction, so re-running the ingest for OFAC never touches UN or EU
    rows, and a mid-refresh failure cannot leave a source half-updated.
    ``aliases``/``dates_of_birth``/``countries`` are JSON lists rather than
    single columns because a real designation commonly carries more than one
    of each (see crr.screening.models.WatchlistRecord's docstring)."""

    __tablename__ = "wf_watchlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="sanctions")
    aliases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    dates_of_birth: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    countries: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    program: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    remarks: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_wf_watchlist_source_id"),)


class WatchlistDisposition(Base):
    """An investigator's ruling on one watchlist hit for one case. Terminal:
    one disposition per (customer, hit) — enforced by a unique constraint — and
    the store offers no update path, matching the UI where a disposed hit's
    form disappears."""

    __tablename__ = "wf_watchlist_dispositions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hit_id: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    hit_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    list_source: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (UniqueConstraint("customer_id", "hit_id", name="uq_wf_disposition_hit"),)


class FiledReport(Base):
    """A suspicious transaction/activity report filed against a case (see
    :mod:`crr.reporting` for how it is assembled and serialized). ``id`` is
    the report's own ``entity_reference`` — already a globally unique,
    human-meaningful string (``CRR-<customer_id>-<timestamp>``), so no
    separate surrogate key is needed. ``xml_content`` is the exact goAML-
    style XML this report was filed with, snapshotted at filing time: a
    later change to the serializer must never change what an already-filed
    report is recorded as having said, the same reasoning that keeps
    ``wf_cases.result`` a snapshot rather than a recomputed value."""

    __tablename__ = "wf_filed_reports"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    report_code: Mapped[str] = mapped_column(String(16), nullable=False, default="STR")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    indicators: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    xml_content: Mapped[str] = mapped_column(Text, nullable=False)
    filed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    filed_by_role: Mapped[str] = mapped_column(String(32), nullable=False)
    filed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


# --------------------------------------------------------------------------
# Immutable audit log
# --------------------------------------------------------------------------


class AuditEntry(Base):
    """One append-only audit record, chained to the previous one by hash.

    ``entry_hash = sha256(prev_hash + canonical(content))``; the first row's
    ``prev_hash`` is a fixed genesis constant. Walking the chain and recomputing
    each hash proves the log has not been altered, reordered, or truncated —
    any tampered or missing row breaks the chain at that point. The store never
    updates or deletes a row here."""

    __tablename__ = "wf_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
