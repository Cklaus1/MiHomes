"""Landing routes — the complete public surface of Phase 0.

The route table here **is** the §7-N1 allowlist:

    GET  /                      landing page
    POST /waitlist              signup
    GET  /waitlist/confirm      double opt-in
    GET  /auth/google/start     OAuth stub      (G8)
    GET  /auth/google/callback  OAuth stub      (G8)
    GET  /healthz               liveness
    GET  /static/*              hero image, CSS

Nothing from `mihomes.web` is imported, let alone mounted. That app has 22
routers, no authentication, and live estate data behind them.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

from mihomes.landing.db import get_landing_engine

logger = logging.getLogger(__name__)

router = APIRouter()


def check_database() -> None:
    """Raise if Postgres is unreachable. Patched in tests to simulate an outage."""
    engine = get_landing_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


@router.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness for Fly. Checks DB connectivity. No auth, no PII.

    Must genuinely fail when the database is gone: Fly restarts on a failing
    healthcheck, so a check that always returns 200 keeps a broken deploy in
    rotation. The body carries a status and nothing else — no DSN, no versions,
    no counts.
    """
    try:
        check_database()
    except Exception:
        logger.exception("healthz: database unreachable")
        return JSONResponse({"status": "unhealthy"}, status_code=503)
    return JSONResponse({"status": "ok"})


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    """The marketing page. Captures utm_* from query params into the form.

    G6 fills in the nine GTM sections; this is the skeleton that G5's tests boot.
    """
    from mihomes.landing.templates_env import render_page

    utm = {
        key: request.query_params[key]
        for key in ("utm_campaign", "utm_source", "utm_medium")
        if key in request.query_params
    }
    return HTMLResponse(render_page("index.html", {"utm": utm}))


@router.post("/waitlist", response_class=HTMLResponse)
async def join_waitlist(request: Request) -> HTMLResponse:
    """Form submit. Wired in G7 (Step 7).

    Placeholder that already returns the *same* shape N3 requires, so the
    enumeration guarantee is not something G7 has to retrofit.
    """
    from mihomes.landing.templates_env import render_page

    return HTMLResponse(render_page("submitted.html", {}))


@router.get("/waitlist/confirm", response_class=HTMLResponse)
async def confirm_waitlist(request: Request, token: str = "") -> HTMLResponse:
    """Double opt-in landing. Sets confirmed_at. Wired in G7 (Step 7)."""
    from mihomes.landing.templates_env import render_page

    return HTMLResponse(render_page("confirmed.html", {"confirmed": False}))


def base_url() -> str:
    """Absolute base for confirm links (§10 LANDING_BASE_URL)."""
    return os.environ.get("LANDING_BASE_URL", "http://localhost:8080").rstrip("/")
