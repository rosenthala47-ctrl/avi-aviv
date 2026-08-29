"""The auth gateway: the one thing in this system that can set a cookie.

Streamlit's runtime can only *read* cookies and headers on the request that
opened a session (``st.context.cookies`` / ``st.context.headers``) — there is
no public API to issue a ``Set-Cookie``, because that header can only come
from a real HTTP response, and a Streamlit script has no hook into the page's
initial response. So the httpOnly cookie a reverse proxy setup calls for has
to be set by *something* else that sits behind the same proxy, on the same
origin, sharing the same workflow database. This tiny FastAPI app is that
something — and nothing more:

* ``GET``/``POST /auth/adopt`` — cookie-izes an ALREADY-VALID session token
  that Streamlit's own in-app login form just minted (see ``app.py``'s
  ``render_login_page``). It does not authenticate a username/password itself
  — ``crr.workflow.store.WorkflowStore.authenticate`` remains the one place
  credentials are checked, unchanged from before this file existed. This is
  what "extract a token set by the proxy, as an alternative to the URL query
  parameter" cashes out to on the server side: the query parameter is still
  how the token reaches the browser for that one hop, but a cookie takes over
  on every request after it.

  ``app.py`` drives this hop with the ``GET`` variant, via an in-page
  ``<meta http-equiv="refresh">`` redirect — the only real top-level browser
  navigation a Streamlit script can trigger. (An earlier attempt used an
  auto-submitting ``<form method="POST">`` rendered inside
  ``components.v1.html``'s iframe; that iframe is sandboxed without
  ``allow-top-navigation``, so the browser silently blocks the submit. There
  is no Streamlit API to lift that restriction, so a real cross-origin POST
  from inside the app is not available here — only a GET redirect is.) The
  ``POST`` form is kept for a non-Streamlit caller that *can* issue one (a
  custom login page, a test, curl) and wants the token to skip the URL/logs
  for this one hop; it is otherwise equivalent.
* ``POST``/``GET /auth/logout`` — clears the cookie and ends the session.
* ``GET /healthz`` — for the proxy/orchestrator's health check.

Run it with:

    uvicorn crr.workflow.gateway:create_app --factory --host 127.0.0.1 --port 8600

and point a reverse proxy's ``/auth/*`` route at it (see ``deploy/Caddyfile``,
``deploy/nginx.conf``). It is a separate process from both the scoring API
(``crr.api``) and Streamlit — it depends on neither, only on
``crr.workflow.store``, keeping "session/cookie plumbing" and "risk scoring"
as unrelated concerns that happen to share a reverse proxy.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse

from crr.workflow.auth import SESSION_COOKIE_NAME
from crr.workflow.db import create_session_factory, resolve_database_url
from crr.workflow.store import SESSION_TTL, WorkflowStore

#: Toggle for local HTTP development, where a `Secure` cookie would never be
#: sent back by the browser at all (no TLS). Defaults to secure — an operator
#: has to deliberately opt out, not deliberately opt in.
_COOKIE_SECURE_ENV = "CRR_COOKIE_SECURE"
#: "lax" (default) lets the cookie ride along on the top-level GET redirect
#: this gateway itself issues; "strict" is available for deployments that do
#: not need that and want the tighter setting.
_COOKIE_SAMESITE_ENV = "CRR_COOKIE_SAMESITE"


def _cookie_flags() -> dict[str, object]:
    secure = os.environ.get(_COOKIE_SECURE_ENV, "true").strip().lower() not in ("0", "false", "no")
    samesite = os.environ.get(_COOKIE_SAMESITE_ENV, "lax").strip().lower()
    if samesite not in ("lax", "strict", "none"):
        samesite = "lax"
    return {"httponly": True, "secure": secure, "samesite": samesite, "path": "/"}


def _safe_next(next_path: str) -> str:
    """Only ever redirect somewhere on this same origin.

    ``next`` is caller-supplied (a query/form value), so treating it as a
    trustworthy absolute URL would make this endpoint an open redirect. A
    leading single ``/`` is a same-origin path; anything else — a scheme, a
    host, a protocol-relative ``//evil.example`` — falls back to the root.
    """
    if next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return "/"


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = WorkflowStore(create_session_factory(resolve_database_url()))
        app.state.store.seed_default_users()
        yield

    app = FastAPI(title="CRR Auth Gateway", summary="Cookie issuance for the workflow login.",
                  docs_url=None, redoc_url=None, lifespan=lifespan)

    def _store(request: Request) -> WorkflowStore:
        return request.app.state.store

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    def _adopt(request: Request, token: str, next: str) -> RedirectResponse:
        """Set the httpOnly cookie for a token app.py already minted.

        Deliberately does not create a new session or touch credentials — it
        only checks the token is currently valid (the same check
        ``resolve_session`` already does on every request) before cookie-izing
        it. An invalid/expired token redirects on through with no cookie set,
        so this endpoint can never grant a session it wasn't already given."""
        user = _store(request).resolve_session(token)
        response = RedirectResponse(url=_safe_next(next), status_code=303)
        if user is not None:
            response.set_cookie(
                SESSION_COOKIE_NAME, token, max_age=int(SESSION_TTL.total_seconds()), **_cookie_flags()
            )
        return response

    @app.post("/auth/adopt")
    def adopt_post(request: Request, token: str = Form(...), next: str = Form("/")) -> RedirectResponse:
        return _adopt(request, token, next)

    @app.get("/auth/adopt")
    def adopt_get(request: Request, token: str, next: str = "/") -> RedirectResponse:
        return _adopt(request, token, next)

    def _logout(request: Request, next: str) -> RedirectResponse:
        _store(request).end_session(request.cookies.get(SESSION_COOKIE_NAME))
        response = RedirectResponse(url=_safe_next(next), status_code=303)
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return response

    @app.post("/auth/logout")
    def logout_post(request: Request, next: str = Form("/")) -> RedirectResponse:
        return _logout(request, next)

    @app.get("/auth/logout")
    def logout_get(request: Request, next: str = "/") -> RedirectResponse:
        return _logout(request, next)

    return app
