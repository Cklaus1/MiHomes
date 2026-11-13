"""Waitlist mode — who may create a *new* account (2026-11-13).

Set ``MIHOMES_SIGNUP_MODE=waitlist`` and the public front door stops creating accounts: the
homepage shows the waitlist form instead of "Start free", and `/signup` and a first Google
sign-in are refused unless the visitor holds an invitation. Leave it unset (evo-dev, laptop,
tests) and nothing changes.

**Only account creation is gated.** Everyone who already has a user row signs in exactly as
before — password or Google — so test accounts and existing households are untouched.

Two kinds of invitation open the door:

* **Tester invite** — a link minted by ``mihomes waitlist invite EMAIL``. The token is an HMAC
  over (email, expiry) under ``MIHOMES_SECRET_KEY``: no table, nothing to clean up, and it only
  works for the address it was made for. Reuse is harmless — once that address has an account,
  a second signup is refused as "already registered" anyway.
* **Team invite** — the existing ``/invite/{token}`` from Settings → Members. The invitee is sent
  to ``/signup?next=/invite/{token}``; a pending invite token in ``next`` lets them through.

Google sign-in cannot carry ``next`` or a query string through Google and back, so ``/signup``
parks whichever invitation it was opened with in a short-lived httpOnly cookie, and the OAuth
callback reads it from there.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from mihomes.crypto import secret_key

SIGNUP_MODE_ENV = "MIHOMES_SIGNUP_MODE"
#: Carries an invitation from `/signup` across the Google round trip.
INVITE_COOKIE = "mihomes_signup_invite"
INVITE_COOKIE_MAX_AGE = 60 * 30  # the Google detour takes seconds; half an hour is generous

DEFAULT_TESTER_DAYS = 14
_TEAM_INVITE_NEXT = re.compile(r"^/invite/([A-Za-z0-9_-]{10,200})(?:/accept)?$")


def waitlist_mode() -> bool:
    """True when new accounts need an invitation. Read per call, so env + restart is the switch."""
    return os.environ.get(SIGNUP_MODE_ENV, "").strip().lower() == "waitlist"


# ── Tester invites ────────────────────────────────────────────────────────────────────────────


def _key() -> bytes:
    key = secret_key()
    if key is None:
        # No unsigned fallback: a forgeable invite in a deployment where nothing looks wrong is
        # worse than refusing to mint one.
        raise RuntimeError("MIHOMES_SECRET_KEY is not set, so tester invites cannot be signed.")
    return key.encode("utf-8")


def _sign(email: str, expires: int) -> str:
    message = f"mihomes-tester-invite-v1\x00{email.strip().lower()}\x00{expires}".encode()
    return hmac.new(_key(), message, hashlib.sha256).hexdigest()


def tester_invite_token(email: str, *, days: int = DEFAULT_TESTER_DAYS) -> str:
    """``<expiry-unix-seconds>.<hmac>`` — valid for ``email`` only, until the expiry."""
    expires = int(time.time()) + days * 86400
    return f"{expires}.{_sign(email, expires)}"


def tester_invite_valid(email: str, token: str) -> bool:
    """Does ``token`` admit ``email`` right now? Never raises on a malformed token."""
    if not email or not token or "." not in token:
        return False
    raw_expiry, _, signature = token.partition(".")
    if not raw_expiry.isdigit() or int(raw_expiry) < time.time():
        return False
    try:
        expected = _sign(email, int(raw_expiry))
    except RuntimeError:
        return False
    return hmac.compare_digest(expected, signature)


def tester_invite_link(base_url: str, email: str, *, days: int = DEFAULT_TESTER_DAYS) -> str:
    query = urlencode({"email": email.strip().lower(), "invite": tester_invite_token(email, days=days)})
    return f"{base_url.rstrip('/')}/signup?{query}"


# ── Team invites ──────────────────────────────────────────────────────────────────────────────


def team_token_from_next(next_path: str) -> str:
    """The invite token in a ``/invite/{token}`` destination, or ``""``."""
    match = _TEAM_INVITE_NEXT.match(next_path or "")
    return match.group(1) if match else ""


def _team_invite_pending(db: Session, token: str) -> bool:
    if not token:
        return False
    from mihomes.services import invite_service

    return invite_service.find_pending(db, token) is not None


# ── The decision ──────────────────────────────────────────────────────────────────────────────


def may_create_account(
    db: Session, *, email: str, tester_token: str = "", team_token: str = ""
) -> bool:
    """May someone create a new account with ``email``? Always yes outside waitlist mode."""
    if not waitlist_mode():
        return True
    if tester_invite_valid(email, tester_token):
        return True
    return _team_invite_pending(db, team_token)


def encode_cookie(*, tester_token: str = "", team_token: str = "") -> str:
    """The invite cookie's value; a team invite wins if both are present."""
    if team_token:
        return f"team:{team_token}"
    return f"tester:{tester_token}"


def decode_cookie(value: str | None) -> tuple[str, str]:
    """``(tester_token, team_token)`` from the invite cookie; empty strings when absent."""
    kind, _, token = (value or "").partition(":")
    if kind == "team":
        return "", token
    if kind == "tester":
        return token, ""
    return "", ""
