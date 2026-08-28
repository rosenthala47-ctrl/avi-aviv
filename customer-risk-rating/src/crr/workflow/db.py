"""Engine, session factory, and one-shot schema setup for the workflow store.

Database selection is by URL, resolved once from the environment:

* ``CRR_WORKFLOW_DB_URL`` — any SQLAlchemy URL. Set this to
  ``postgresql+psycopg://user:pass@host/db`` in production (the ``db`` extra
  installs the psycopg driver).
* unset — a file-backed SQLite database at ``data/workflow.db`` under the repo,
  which needs no server and survives a browser refresh and a process restart.
  This is the lightweight/local fallback the brief asks for.

Schema setup is ``Base.metadata.create_all`` — idempotent, so it runs safely on
every startup and is a no-op once the tables exist. Alembic is deliberately not
required here: it is an optional production extra, and for a create-only schema
with no destructive migrations yet, ``create_all`` is the honest, dependency-
free choice. When real column migrations arrive, Alembic drops in against this
same metadata.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from crr.workflow.models import Base

#: Repo root: src/crr/workflow/db.py -> parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SQLITE_PATH = _REPO_ROOT / "data" / "workflow.db"

_ENV_URL = "CRR_WORKFLOW_DB_URL"


def resolve_database_url() -> str:
    """The workflow database URL: the env override, or the local SQLite file."""
    override = os.environ.get(_ENV_URL, "").strip()
    if override:
        return override
    _DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_DEFAULT_SQLITE_PATH}"


def _json_default(value: object) -> str:
    """Last-resort serialiser so a JSON column can hold any JSON-shaped value
    (e.g. a datetime inside a rule definition) without the save failing on a
    type nobody pre-sanitised — one guard at the engine boundary, matching
    crr.api.repository's approach."""
    import datetime as dt

    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def create_engine_for(url: str, *, echo: bool = False) -> Engine:
    """Build an engine tuned for the URL's dialect.

    SQLite needs ``check_same_thread=False`` because Streamlit runs each
    session's script on pooled worker threads that differ from the one that
    opened the connection; an in-memory SQLite URL additionally needs a
    ``StaticPool`` so every thread shares the one database rather than each
    opening its own empty one. A file or PostgreSQL URL uses the normal pool."""
    kwargs: dict = {
        "echo": echo,
        "future": True,
        "json_serializer": lambda obj: json.dumps(obj, default=_json_default),
    }
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        is_memory = ":memory:" in url or url in ("sqlite://", "sqlite:///")
        if is_memory:
            kwargs["poolclass"] = StaticPool
    return create_engine(url, **kwargs)


def create_session_factory(url: str | None = None, *, echo: bool = False) -> sessionmaker[Session]:
    """Engine + ``create_all`` + a session factory, in one call.

    ``expire_on_commit=False`` so the store can read attributes off an object
    after it commits without a refresh round-trip; the store converts rows to
    plain dicts before returning, so no live ORM object ever escapes it."""
    engine = create_engine_for(url or resolve_database_url(), echo=echo)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False, future=True)
