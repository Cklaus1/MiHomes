"""G12 · §6 Step 12 — `/auth/google/start`, `/callback`, `/signout` (A15, A16, A17).

**Cookie flags, and why each one (A16).**

    httpOnly   no script may read the session id, so an XSS bug cannot exfiltrate the session
    Secure     never sent over plain HTTP, so a network observer cannot capture it
    SameSite   Lax: not sent on cross-site POSTs, which blunts CSRF before our own checks run

`Secure` is dropped **only** on a loopback host, because a browser will not store a Secure cookie
over `http://localhost` and dev would be unable to sign in at all. That exception is decided from the
request's own host rather than a `DEBUG` flag: a flag can be wrong in production, whereas
"the request arrived at localhost" cannot.

**The session id rotates on sign-in.** Any pre-existing session cookie is discarded and a new id
issued, so a fixation attack — planting a known session id in the victim's browser before they sign
in — ends up authenticating the attacker's id to nobody.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from mihomes.auth.csrf import CSRF_COOKIE, issue_csrf_token, tokens_match
from mihomes.auth.oidc import (
    GoogleOIDCProvider,
    InvalidIdentityToken,
    upsert_user,
)
from mihomes.auth.sessions import (
    SESSION_COOKIE,
    SESSION_TTL,
    create_session,
    lookup_session,
    revoke_all_sessions,
    revoke_session,
)
from mihomes.models.membership import Membership

# `templates` from `deps`, not `app`: it is the `RedactingTemplates` instance every other route
# renders through, and importing from `web.app` here would create a cycle (app imports routes).
from mihomes.web.deps import get_db, templates

router = APIRouter()

# Core table, not the ORM class: this read runs before any tenant context exists, so an ORM
# query would demand the account it is being used to discover. Same carve-out `deps.py` and
# `auth/sessions.py` both document.
_MEMBERSHIPS = Membership.__table__

_STATE_COOKIE = "mihomes_oauth_state"
_VERIFIER_COOKIE = "mihomes_oauth_verifier"
# The OAuth round trip is a browser redirect to Google and back. Ten minutes is generous for that
# and short enough that a captured state cookie is useless by the time anyone finds it.
_FLOW_MAX_AGE = 600


def _provider():
    """Indirection so tests can substitute a fake provider without patching HTTP."""
    return GoogleOIDCProvider()


def _is_loopback(request: Request) -> bool:
    host = (request.url.hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _set_cookie(
    response: Response,
    request: Request,
    name: str,
    value: str,
    *,
    max_age: int,
    http_only: bool = True,
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=http_only,
        # See the module docstring: only loopback drops Secure, and it is decided from the request.
        secure=not _is_loopback(request),
        samesite="lax",
        path="/",
    )


@router.get("/login")
def login(request: Request, db: DbSession = Depends(get_db), error: str = ""):
    """The sign-in front door.

    **Before this there was none.** `/auth/google/start` redirects straight to Google, so an
    unauthenticated visitor hitting any page got a bare `401 Not authenticated` with nothing to
    click — the app looked broken rather than locked.

    Already signed in? Go where you were going. A login page that re-prompts someone who has a
    valid session is a dead end reached by pressing Back.
    """
    if lookup_session(db, request.cookies.get(SESSION_COOKIE)) is not None:
        return RedirectResponse("/", status_code=303)

    # Reported to the template rather than discovered on click: without credentials
    # `/auth/google/start` raises `OAuthError` and the visitor gets a 500 that does not say
    # what to do. `_provider()` is not called — constructing it is what raises.
    oauth_configured = bool(
        os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")
    )

    return templates.TemplateResponse(
        request,
        "login.html",
        {"oauth_configured": oauth_configured, "error": error},
        # 401 rather than 200: this *is* the unauthenticated response, and a crawler or an API
        # client should read it as one. The browser renders the body either way.
        status_code=401 if not error else 400,
    )


@router.get("/auth/google/start")
def start(request: Request):
    """Begin the flow: PKCE challenge + anti-forgery state, both parked in signed-free cookies.

    The verifier is kept in an `httpOnly` cookie rather than server state so the flow needs no
    storage for an unauthenticated visitor — and `httpOnly` matters here: a verifier readable by
    script would let an XSS bug complete someone else's sign-in.
    """
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )

    response = RedirectResponse(
        _provider().authorization_url(state=state, code_challenge=challenge),
        status_code=307,
    )
    _set_cookie(response, request, _STATE_COOKIE, state, max_age=_FLOW_MAX_AGE)
    _set_cookie(response, request, _VERIFIER_COOKIE, verifier, max_age=_FLOW_MAX_AGE)
    return response


@router.get("/auth/google/callback")
def callback(request: Request, code: str = "", state: str = "", db: DbSession = Depends(get_db)):
    """Complete the flow: verify state, exchange the code, verify the token, create the session."""
    expected_state = request.cookies.get(_STATE_COOKIE)
    verifier = request.cookies.get(_VERIFIER_COOKIE)

    # Constant-time, and both-present-required: `not state or state != expected` with `==` would
    # both leak timing and treat two missing values as a match.
    if not tokens_match(expected_state, state):
        raise HTTPException(status_code=400, detail="Invalid or expired sign-in request.")
    if not code or not verifier:
        raise HTTPException(status_code=400, detail="Invalid or expired sign-in request.")

    provider = _provider()
    try:
        id_token = provider.exchange_code(code=code, code_verifier=verifier)
        claims = provider.verify(id_token)
    except InvalidIdentityToken:
        # Deliberately not echoing the provider's message: it can contain token fragments, and the
        # user cannot act on "aud mismatch" anyway. The detail is logged, not returned.
        raise HTTPException(status_code=401, detail="Sign-in failed.") from None

    user = upsert_user(db, claims)

    # Rotate: discard whatever session the browser arrived with, so a planted id is not adopted.
    stale = request.cookies.get(SESSION_COOKIE)
    if stale:
        revoke_session(db, stale)

    raw_session, _row = create_session(db, user.id)
    db.commit()

    # **A first-time user has no account, and `/` requires one.** `resolve_principal` raises
    # 403 "No account selected" for exactly that state, so sending everyone to `/` meant a new
    # user's reward for signing in was an error page — with the onboarding wizard that handles
    # this case sitting one route away, already built (SPEC-003), and unreachable.
    #
    # `/onboarding/` resumes wherever they left off and redirects to `/` once the account
    # exists, so returning users pass straight through and this costs them one 303.
    has_account = db.execute(
        select(_MEMBERSHIPS.c.id).where(
            _MEMBERSHIPS.c.user_id == user.id,
            _MEMBERSHIPS.c.status == "active",
        )
    ).first()
    destination = "/" if has_account else "/onboarding/"

    response = RedirectResponse(destination, status_code=303)
    _set_cookie(
        response, request, SESSION_COOKIE, raw_session,
        max_age=int(SESSION_TTL.total_seconds()),
    )
    # Readable by script on purpose — the page must echo it back in a form field.
    _set_cookie(
        response, request, CSRF_COOKIE, issue_csrf_token(),
        max_age=int(SESSION_TTL.total_seconds()), http_only=False,
    )
    # The flow cookies have done their job; leaving them would keep a usable verifier around.
    response.delete_cookie(_STATE_COOKIE, path="/")
    response.delete_cookie(_VERIFIER_COOKIE, path="/")
    return response


@router.post("/signout")
def signout(request: Request, csrf_token: str = Form(""), db: DbSession = Depends(get_db)):
    """End this session.

    A POST with CSRF, not a GET: a `GET /signout` can be triggered by any third-party page (an
    `<img src>` is enough), which is a nuisance rather than a breach but a real one.
    """
    if not tokens_match(request.cookies.get(CSRF_COOKIE), csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")

    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        revoke_session(db, raw)
        db.commit()

    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.post("/signout-all")
def signout_everywhere(
    request: Request, csrf_token: str = Form(""), db: DbSession = Depends(get_db)
):
    """End every session for this user — the "sign out everywhere" case in Step 12.

    Resolves the user from the *current* session rather than accepting a user id: taking one from
    the request would let anybody sign out anybody.
    """
    if not tokens_match(request.cookies.get(CSRF_COOKIE), csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")

    current = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    if current is not None:
        revoke_all_sessions(db, current.user_id)
        db.commit()

    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response
