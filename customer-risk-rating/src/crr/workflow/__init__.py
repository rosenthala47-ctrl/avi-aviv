"""Persisted case-management / auth / audit layer for the operations console.

The Streamlit front end used to hold the whole reviewer workflow in
``st.session_state`` — gone on refresh. This package moves it into a database
(SQLite by default, PostgreSQL via ``CRR_WORKFLOW_DB_URL``) so case decisions,
notes, timelines, dynamic rules, watchlist dispositions, user accounts, and an
immutable audit trail survive a refresh, a new tab, and a restart.

    from crr.workflow import WorkflowStore, create_session_factory, resolve_database_url

    store = WorkflowStore(create_session_factory(resolve_database_url()))
    store.seed_default_users()
"""

from __future__ import annotations

from crr.workflow.db import create_session_factory, resolve_database_url
from crr.workflow.store import DEMO_USERS, WorkflowStore

__all__ = ["WorkflowStore", "create_session_factory", "resolve_database_url", "DEMO_USERS"]
