"""Persistence for scores and batch jobs, behind an interface.

The API never talks to a database class directly; it talks to these Protocols. The
default wiring is in-memory, so the service runs, is fully testable, and can be
demoed with no PostgreSQL. The SQLAlchemy implementation is the same code path used
in production and is exercised in tests against SQLite.

Storing every score (append-only ``save``) is the durable audit trail: the record
holds the model version, policy version and input hash that make a served score
reproducible.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from crr.db.models import Base, JobRecord, ScoreRecord


@dataclass
class StoredScore:
    """The persisted form of an assessment (storage-agnostic)."""

    customer_id: str
    scored_at: dt.datetime
    risk_score: float
    risk_band: str
    credit_probability: float
    financial_crime_probability: float
    requires_review: bool
    model_version: str
    policy_version: int
    input_hash: str
    audience: str
    explanation: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoredJob:
    job_id: str
    status: str
    total: int
    processed: int
    submitted_at: dt.datetime
    completed_at: dt.datetime | None = None
    error: str | None = None


class ScoreRepository(Protocol):
    def save(self, score: StoredScore) -> None: ...
    def latest(self, customer_id: str) -> StoredScore | None: ...
    def history(self, customer_id: str, limit: int = 100) -> list[StoredScore]: ...
    def find_by_input_hash(self, customer_id: str, input_hash: str) -> StoredScore | None: ...


class JobRepository(Protocol):
    def create(self, job: StoredJob) -> None: ...
    def get(self, job_id: str) -> StoredJob | None: ...
    def update(self, job: StoredJob) -> None: ...


# --------------------------------------------------------------------------
# In-memory (default; tests and infra-free local runs)
# --------------------------------------------------------------------------


class InMemoryScoreRepository:
    def __init__(self) -> None:
        self._scores: dict[str, list[StoredScore]] = {}

    def save(self, score: StoredScore) -> None:
        self._scores.setdefault(score.customer_id, []).append(score)

    def latest(self, customer_id: str) -> StoredScore | None:
        scores = self._scores.get(customer_id)
        return max(scores, key=lambda s: s.scored_at) if scores else None

    def history(self, customer_id: str, limit: int = 100) -> list[StoredScore]:
        scores = sorted(self._scores.get(customer_id, []), key=lambda s: s.scored_at, reverse=True)
        return scores[:limit]

    def find_by_input_hash(self, customer_id: str, input_hash: str) -> StoredScore | None:
        for score in self._scores.get(customer_id, []):
            if score.input_hash == input_hash:
                return score
        return None


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, StoredJob] = {}

    def create(self, job: StoredJob) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> StoredJob | None:
        return self._jobs.get(job_id)

    def update(self, job: StoredJob) -> None:
        self._jobs[job.job_id] = job


# --------------------------------------------------------------------------
# SQLAlchemy (production; tested against SQLite)
# --------------------------------------------------------------------------


def create_session_factory(url: str, *, echo: bool = False) -> sessionmaker[Session]:
    """Build a session factory and create tables. ``url`` is any SQLAlchemy URL
    (``sqlite://`` for tests, ``postgresql+psycopg://...`` in production).

    In-memory SQLite gets a ``StaticPool`` so every connection shares the one
    database: without it each thread — and the API scores requests in a
    threadpool — opens its own empty in-memory database and the tables vanish.
    The special-casing is confined to in-memory SQLite; a file or PostgreSQL URL
    uses the normal pool.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    is_memory_sqlite = url.startswith("sqlite") and (":memory:" in url or url in ("sqlite://", "sqlite:///"))
    kwargs: dict = {"echo": echo, "future": True}
    if is_memory_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool

    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False, future=True)


def _to_stored(record: ScoreRecord) -> StoredScore:
    return StoredScore(
        customer_id=record.customer_id,
        scored_at=record.scored_at,
        risk_score=record.risk_score,
        risk_band=record.risk_band,
        credit_probability=record.credit_probability,
        financial_crime_probability=record.financial_crime_probability,
        requires_review=bool(record.requires_review),
        model_version=record.model_version,
        policy_version=record.policy_version,
        input_hash=record.input_hash,
        audience=record.audience,
        explanation=record.explanation,
    )


class SqlAlchemyScoreRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, score: StoredScore) -> None:
        with self._session_factory() as session:
            session.add(
                ScoreRecord(
                    customer_id=score.customer_id,
                    scored_at=score.scored_at,
                    risk_score=score.risk_score,
                    risk_band=score.risk_band,
                    credit_probability=score.credit_probability,
                    financial_crime_probability=score.financial_crime_probability,
                    requires_review=int(score.requires_review),
                    model_version=score.model_version,
                    policy_version=score.policy_version,
                    input_hash=score.input_hash,
                    audience=score.audience,
                    explanation=score.explanation,
                )
            )
            session.commit()

    def latest(self, customer_id: str) -> StoredScore | None:
        with self._session_factory() as session:
            record = session.scalars(
                select(ScoreRecord).where(ScoreRecord.customer_id == customer_id)
                .order_by(ScoreRecord.scored_at.desc()).limit(1)
            ).first()
            return _to_stored(record) if record else None

    def history(self, customer_id: str, limit: int = 100) -> list[StoredScore]:
        with self._session_factory() as session:
            records = session.scalars(
                select(ScoreRecord).where(ScoreRecord.customer_id == customer_id)
                .order_by(ScoreRecord.scored_at.desc()).limit(limit)
            ).all()
            return [_to_stored(r) for r in records]

    def find_by_input_hash(self, customer_id: str, input_hash: str) -> StoredScore | None:
        with self._session_factory() as session:
            record = session.scalars(
                select(ScoreRecord).where(
                    ScoreRecord.customer_id == customer_id, ScoreRecord.input_hash == input_hash
                ).order_by(ScoreRecord.scored_at.desc()).limit(1)
            ).first()
            return _to_stored(record) if record else None


class SqlAlchemyJobRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, job: StoredJob) -> None:
        with self._session_factory() as session:
            session.add(JobRecord(**asdict(job)))
            session.commit()

    def get(self, job_id: str) -> StoredJob | None:
        with self._session_factory() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                return None
            return StoredJob(
                job_id=record.job_id, status=record.status, total=record.total,
                processed=record.processed, submitted_at=record.submitted_at,
                completed_at=record.completed_at, error=record.error,
            )

    def update(self, job: StoredJob) -> None:
        with self._session_factory() as session:
            record = session.get(JobRecord, job.job_id)
            if record is None:
                session.add(JobRecord(**asdict(job)))
            else:
                for key, value in asdict(job).items():
                    setattr(record, key, value)
            session.commit()
