"""Tests for crr.workflow.gateway — the small FastAPI service that is the
only thing in this system able to issue an httpOnly session cookie.

Runs a real ASGI app through Starlette's TestClient against a real (file-
backed, tmp-dir) SQLite database — the same store the Streamlit app and the
gateway would share behind a reverse proxy in production."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from crr.workflow import WorkflowStore, create_session_factory
from crr.workflow.auth import SESSION_COOKIE_NAME
from crr.workflow.gateway import create_app


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'workflow.db'}"
    monkeypatch.setenv("CRR_WORKFLOW_DB_URL", url)
    return url


@pytest.fixture
def store(db_url):
    s = WorkflowStore(create_session_factory(db_url))
    s.seed_default_users()
    return s


@pytest.fixture
def client(db_url):
    # The gateway builds its own store from CRR_WORKFLOW_DB_URL in its
    # lifespan, exactly as it would as a separate process in production —
    # db_url's monkeypatch is what makes it point at the same tmp database
    # the `store` fixture also opens.
    with TestClient(create_app()) as c:
        yield c


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_adopt_sets_httponly_secure_samesite_cookie_for_a_valid_token(client, store):
    user = store.authenticate("officer", "officer123")
    token = store.create_login_session(user["id"])

    resp = client.get(f"/auth/adopt?token={token}&next=/dashboard", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    set_cookie = resp.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=lax" in set_cookie.lower()


def test_adopt_post_reads_the_token_from_the_form_body(client, store):
    user = store.authenticate("manager", "manager123")
    token = store.create_login_session(user["id"])

    resp = client.post("/auth/adopt", data={"token": token, "next": "/"}, follow_redirects=False)
    assert resp.status_code == 303
    assert f"{SESSION_COOKIE_NAME}=" in resp.headers.get("set-cookie", "")


def test_adopt_with_an_invalid_token_sets_no_cookie(client):
    resp = client.get("/auth/adopt?token=not-a-real-token&next=/", follow_redirects=False)
    assert resp.status_code == 303
    assert "set-cookie" not in {k.lower() for k in resp.headers}


def test_adopt_rejects_open_redirect_via_next(client, store):
    user = store.authenticate("analyst", "analyst123")
    token = store.create_login_session(user["id"])

    for malicious_next in ("https://evil.example/steal", "//evil.example/steal", "javascript:alert(1)"):
        resp = client.get(f"/auth/adopt?token={token}&next={malicious_next}", follow_redirects=False)
        assert resp.headers["location"] == "/", f"open redirect via next={malicious_next!r}"


def test_logout_clears_the_cookie_and_ends_the_session(client, store):
    user = store.authenticate("officer", "officer123")
    token = store.create_login_session(user["id"])
    assert store.resolve_session(token) is not None

    client.cookies.set(SESSION_COOKIE_NAME, token)
    resp = client.post("/auth/logout", data={"next": "/"}, follow_redirects=False)
    assert resp.status_code == 303
    set_cookie = resp.headers.get("set-cookie", "")
    assert f'{SESSION_COOKIE_NAME}=""' in set_cookie or "Max-Age=0" in set_cookie
    assert store.resolve_session(token) is None, "logout must end the session server-side too"


def test_logout_with_no_cookie_is_a_harmless_no_op(client):
    resp = client.get("/auth/logout?next=/", follow_redirects=False)
    assert resp.status_code == 303


def test_cookie_insecure_env_toggle_drops_the_secure_flag(monkeypatch, store):
    monkeypatch.setenv("CRR_COOKIE_SECURE", "false")
    with TestClient(create_app()) as insecure_client:
        user = store.authenticate("officer", "officer123")
        token = store.create_login_session(user["id"])
        resp = insecure_client.get(f"/auth/adopt?token={token}&next=/", follow_redirects=False)
        assert "Secure" not in resp.headers.get("set-cookie", "")
