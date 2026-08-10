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

from mihomes.landing.db import get_landing_engine, landing_session
from mihomes.landing.ratelimit import client_ip as _client_ip

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
    """Form submit. Rate-limited. Always renders the same success partial,
    whether the email is new or already known — see §7-N3.

    **Every exit path returns the identical page.** A new address, a repeat, an
    already-confirmed one, one past the resend ceiling, and a malformed one all
    render `submitted.html`. Any branch that renders something else — even a
    friendly "you're already on the list" — turns this endpoint into an
    email-enumeration oracle.
    """
    from mihomes.landing.templates_env import render_page
    from mihomes.services import waitlist as waitlist_service
    from mihomes.services.email import EmailService, get_email_provider

    form = await request.form()
    same_response = HTMLResponse(render_page("submitted.html", {}))

    raw_email = (form.get("email") or "").strip()
    tri_state = {"yes": True, "no": False}

    session = landing_session()
    try:
        try:
            row, raw_token = waitlist_service.signup(
                session,
                email=raw_email,
                name=(form.get("name") or None),
                num_homes=(form.get("num_homes") or None),
                has_staff=tri_state.get((form.get("has_staff") or "").lower()),
                source="form",
                utm={
                    key: form[key]
                    for key in ("utm_campaign", "utm_source", "utm_medium")
                    if form.get(key)
                },
                signup_ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        except ValueError:
            # Malformed address. Same page, no row — telling the user their address
            # is invalid is fine UX but would also confirm which addresses exist,
            # so keep the response uniform and let the missing email speak for it.
            logger.info("waitlist: rejected an implausible address")
            return same_response

        # Commit BEFORE sending. A10: the row must survive a dead provider, so the
        # transaction must not still be open when the send is attempted.
        session.commit()
        email_address = row.email
    except Exception:
        session.rollback()
        logger.exception("waitlist signup failed")
        raise
    finally:
        session.close()

    if raw_token:
        confirm_url = f"{base_url()}/waitlist/confirm?token={raw_token}"
        # EmailService swallows EmailSendError by design (§5.3) — this call cannot
        # fail the request, which is what makes A10 hold.
        EmailService(get_email_provider()).send_waitlist_confirmation(
            email_address, confirm_url=confirm_url
        )

    return same_response


@router.get("/waitlist/confirm", response_class=HTMLResponse)
async def confirm_waitlist(request: Request, token: str = "") -> HTMLResponse:
    """Double opt-in landing. Sets confirmed_at.

    Idempotent and never 500s: users click twice, mail scanners pre-fetch links,
    and corporate link-rewriters mangle them.
    """
    from mihomes.landing.templates_env import render_page
    from mihomes.services import waitlist as waitlist_service

    session = landing_session()
    try:
        row = waitlist_service.confirm(session, raw_token=token)
        if row is not None:
            session.commit()
    except Exception:
        session.rollback()
        logger.exception("waitlist confirm failed")
        row = None
    finally:
        session.close()

    return HTMLResponse(render_page("confirmed.html", {"confirmed": row is not None}))


def base_url() -> str:
    """Absolute base for confirm links (§10 LANDING_BASE_URL)."""
    return os.environ.get("LANDING_BASE_URL", "http://localhost:8080").rstrip("/")
