"""Google OIDC — authorization-code flow with PKCE, Phase 0 scope only.

**"Stub" describes the SCOPE, not the rigor.** The ID token signature is verified
for real against Google's published keys, and the audience, issuer and expiry are
all checked. What makes this a stub is where it stops: it extracts the verified
email, calls `waitlist.signup(source="google")`, and does nothing else.

Per D8 and §7-N2 it creates **no `users` row and no session cookie**.
`SAAS_PRD:125` is explicit that there is no `users` table before Phase 1, so
building sessions now means building them against a schema Phase 1 changes.

Three checks here are each independently sufficient to break authentication if
dropped:

- **Signature.** An unverified token is a total bypass — anyone could mint
  `{"email": "someone@else.com"}` and take a row as them.
- **Audience.** Google signs every relying party's tokens with the same keys, so
  without an `aud` check a token issued to *any other* Google app authenticates
  here.
- **State.** Without it the callback is CSRF-able.

The access token is discarded the moment the ID token is read
(BILLING/ONBOARDING 3.2): Phase 0 needs the email and nothing else, and a stored
token is a liability with no matching capability.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

router = APIRouter()

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

STATE_COOKIE = "oauth_state"
STATE_MAX_AGE = 600          # 10 minutes: long enough to sign in, short enough to matter
CLOCK_SKEW_SECONDS = 60


class OAuthError(Exception):
    """Any failure in the OIDC flow. Never surfaced to the user verbatim."""


def _client_id() -> str:
    value = os.environ.get("GOOGLE_CLIENT_ID")
    if not value:
        raise OAuthError("GOOGLE_CLIENT_ID is not set")
    return value


def _client_secret() -> str:
    value = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not value:
        raise OAuthError("GOOGLE_CLIENT_SECRET is not set")
    return value


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        raise OAuthError("SECRET_KEY is not set — it signs the OAuth state cookie")
    return URLSafeTimedSerializer(secret, salt="oauth-state")


def _redirect_uri() -> str:
    from mihomes.landing.routes import base_url

    return f"{base_url()}/auth/google/callback"


def read_state_cookie(cookies) -> str:
    """Extract the state value from a signed cookie. Test helper and internal use."""
    raw = cookies.get(STATE_COOKIE) if hasattr(cookies, "get") else None
    if not raw:
        return ""
    try:
        payload = _serializer().loads(raw, max_age=STATE_MAX_AGE)
    except BadSignature:
        return ""
    return payload.get("state", "")


def _validate_claims(claims: dict, *, client_id: str) -> dict:
    """Check issuer, audience and expiry. Raises OAuthError on any mismatch.

    Separated from signature verification so it is unit-testable without a live
    JWKS fetch — these are the checks people forget, precisely because a valid
    signature feels like enough.
    """
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise OAuthError(f"unexpected issuer: {claims.get('iss')!r}")

    if claims.get("aud") != client_id:
        raise OAuthError(f"unexpected audience: {claims.get('aud')!r}")

    exp = claims.get("exp")
    # `<=`, not `<`: a token expired by exactly the skew window is expired. The
    # strict form leaves a one-second hole on the boundary, which is the kind of
    # off-by-one that only ever shows up in a test written at exactly that offset.
    if exp is None or int(exp) + CLOCK_SKEW_SECONDS <= int(time.time()):
        raise OAuthError("id token expired")

    return claims


def verify_id_token(id_token: str, *, client_id: str | None = None) -> dict:
    """Verify the ID token signature against Google's keys, then its claims."""
    import json
    import urllib.request

    from authlib.jose import JsonWebKey, jwt

    client = client_id or _client_id()

    with urllib.request.urlopen(GOOGLE_CERTS_URL, timeout=10) as response:
        jwks = json.loads(response.read())

    try:
        claims = jwt.decode(id_token, JsonWebKey.import_key_set(jwks))
    except Exception as exc:
        raise OAuthError(f"id token signature verification failed: {exc}") from exc

    return _validate_claims(dict(claims), client_id=client)


def exchange_code(*, code: str, code_verifier: str) -> dict:
    """Exchange the authorization code for tokens (PKCE, no client-side secret leak)."""
    import json
    import urllib.parse
    import urllib.request

    payload = urllib.parse.urlencode({
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }).encode()

    request = urllib.request.Request(
        GOOGLE_TOKEN_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except Exception as exc:
        raise OAuthError(f"code exchange failed: {exc}") from exc


@router.get("/auth/google/start")
async def google_start(request: Request) -> RedirectResponse:
    """Begin OIDC authorization-code flow with PKCE.

    State + verifier live in a signed, short-lived cookie rather than server
    memory, so the flow survives a machine swap mid-sign-in (Fly runs several).
    """
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Phase 0 asks for an email once; no refresh token, no offline access.
        "access_type": "online",
        "prompt": "select_account",
    }

    response = RedirectResponse(
        f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}", status_code=302
    )
    response.set_cookie(
        STATE_COOKIE,
        _serializer().dumps({"state": state, "verifier": verifier}),
        max_age=STATE_MAX_AGE,
        httponly=True,
        secure=not _is_local(),
        samesite="lax",   # must survive Google's cross-site redirect back
    )
    return response


def _is_local() -> bool:
    from mihomes.landing.routes import base_url

    return base_url().startswith("http://")


def _render(template: str, data: dict, status_code: int = 200) -> HTMLResponse:
    from mihomes.landing.templates_env import render_page

    return HTMLResponse(render_page(template, data), status_code=status_code)


@router.get("/auth/google/callback")
async def google_callback(request: Request) -> HTMLResponse:
    """Verify state, exchange code, VERIFY THE ID TOKEN SIGNATURE, extract
    email + name, call waitlist.signup(source='google').

    Creates NO users row and NO session — there is no users table in Phase 0
    (SAAS_PRD:125). Discards the access token immediately.
    """
    from mihomes.landing.db import landing_session
    from mihomes.services import waitlist as waitlist_service

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")

    try:
        cookie_raw = request.cookies.get(STATE_COOKIE)
        if not cookie_raw:
            raise OAuthError("missing state cookie")
        payload = _serializer().loads(cookie_raw, max_age=STATE_MAX_AGE)

        # Constant-time compare, and BEFORE the code exchange: a mismatched state
        # is CSRF, and exchanging the code first would hand an attacker's code to
        # Google on a victim's behalf.
        if not state or not secrets.compare_digest(state, payload.get("state", "")):
            raise OAuthError("state mismatch")

        if not code:
            raise OAuthError("missing authorization code")

        tokens = exchange_code(code=code, code_verifier=payload.get("verifier", ""))
        id_token = tokens.get("id_token")
        if not id_token:
            raise OAuthError("no id_token in the token response")

        # The access token is deliberately never read past this point.
        claims = verify_id_token(id_token)

        if not claims.get("email_verified"):
            # An unverified Google email is not proof of ownership, and Phase 1
            # invites this cohort by email.
            raise OAuthError("email is not verified")

        email = claims.get("email")
        if not email:
            raise OAuthError("no email claim in the id token")

    except (OAuthError, BadSignature) as exc:
        # Log the reason, tell the user nothing specific: the detail would help an
        # attacker distinguish "bad signature" from "wrong audience".
        logger.warning("oauth callback rejected: %s", exc)
        return _render("oauth_failed.html", {}, status_code=400)

    session = landing_session()
    try:
        waitlist_service.signup(
            session,
            email=email,
            name=claims.get("name"),
            source="google",
            signup_ip=_client_ip_of(request),
            user_agent=request.headers.get("user-agent"),
        )
        session.commit()
    except ValueError:
        session.rollback()
        logger.warning("oauth callback: Google returned an implausible address")
        return _render("oauth_failed.html", {}, status_code=400)
    except Exception:
        session.rollback()
        logger.exception("oauth signup failed")
        raise
    finally:
        session.close()

    # No session cookie is set — that is the point of the stub (D8, N2).
    return _render("confirmed.html", {"confirmed": True})


def _client_ip_of(request: Request) -> str:
    from mihomes.landing.ratelimit import client_ip

    return client_ip(request)
