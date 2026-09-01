"""Establishing a signed-in session — the seam both sign-in paths share (SPEC-010 §6 Step 3).

`ONBOARDING_AUTH_RBAC:60` promised email/password could be added *"without touching call
sites"*, on the strength of the `IdentityProvider` abstraction. **That promise was false**, and
G0 corrected the document: `IdentityProvider` is `authorization_url` / `exchange_code` /
`verify` — three OAuth-shaped methods, none of which a password login implements. There is no
sensible `authorization_url` for a form post.

**The seam that does generalise is this one: what happens *after* an identity is established.**
It was already written, inside `routes/auth.py`'s OIDC callback — rotate the session, mint a new
one, set two cookies with three flags each, then route by whether the user has an account.
Every line of that is identical for a password login, and none of it is about Google.

So it moves here rather than being copied. That matters for one reason above the others:
**session rotation and cookie flags are the kind of thing that gets fixed once and then not
again in the copy.** `test_auth.py:318` already records a cookie-flag test that was
conditionally skipped and called *"a red gate"*; two divergent implementations of the same
flags is how that happens for real.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from mihomes.auth.csrf import CSRF_COOKIE, issue_csrf_token
from mihomes.auth.sessions import (
    SESSION_COOKIE,
    SESSION_TTL,
    create_session,
    revoke_session,
)
from mihomes.models.membership import Membership

# Core table, not the ORM class: this read runs before tenant context exists, so an ORM query
# would demand the account it is being used to discover. The carve-out `deps.py` documents.
_MEMBERSHIPS = Membership.__table__


def is_loopback(request: Request) -> bool:
    host = (request.url.hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def set_auth_cookie(
    response: Response,
    request: Request,
    name: str,
    value: str,
    *,
    max_age: int,
    http_only: bool = True,
) -> None:
    """One definition of the three cookie flags, for every route that sets one.

        httpOnly   no script may read the session id, so an XSS bug cannot exfiltrate it
        Secure     never sent over plain HTTP, so a network observer cannot capture it
        SameSite   Lax: not sent on cross-site POSTs, blunting CSRF before our own checks run

    `Secure` drops **only** on a loopback host, because a browser will not store a Secure cookie
    over `http://localhost` and dev could not sign in at all. Decided from the request's own
    host rather than a `DEBUG` flag: a flag can be wrong in production, "the request arrived at
    localhost" cannot.
    """
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=http_only,
        secure=not is_loopback(request),
        samesite="lax",
        path="/",
    )


def destination_for(db: DbSession, user_id: uuid.UUID) -> str:
    """`/` for a member, `/onboarding/` for someone who has no account yet.

    A first-time user has no account and `/` requires one — `resolve_principal` raises 403 "No
    account selected" for exactly that state, so sending everyone to `/` means a new user's
    reward for signing in is an error page. `/onboarding/` resumes wherever they left off and
    redirects to `/` once the account exists, so returning users pass straight through.
    """
    has_account = db.execute(
        select(_MEMBERSHIPS.c.id).where(
            _MEMBERSHIPS.c.user_id == user_id,
            _MEMBERSHIPS.c.status == "active",
        )
    ).first()
    return "/" if has_account else "/onboarding/"


def establish_session(
    db: DbSession,
    request: Request,
    user_id: uuid.UUID,
    *,
    destination: str | None = None,
) -> RedirectResponse:
    """Rotate, mint, set cookies, redirect. **Does not commit** — the caller owns the transaction.

    **The rotation is the security-relevant half, and it is not `new != old`.** Any session
    cookie the browser arrived with is *revoked*, not merely replaced: a fixation attack plants
    a known session id before the victim signs in, and if that id stays valid afterwards the
    attacker is holding a live authenticated session. Minting a second id alongside it defends
    nothing, which is why A8 asserts the OLD id no longer resolves rather than that the new one
    differs.
    """
    stale = request.cookies.get(SESSION_COOKIE)
    if stale:
        revoke_session(db, stale)

    raw_session, _row = create_session(db, user_id)

    if destination is None:
        destination = destination_for(db, user_id)

    response = RedirectResponse(destination, status_code=303)
    set_auth_cookie(
        response, request, SESSION_COOKIE, raw_session,
        max_age=int(SESSION_TTL.total_seconds()),
    )
    # Readable by script on purpose — the page must echo it back in a form field.
    set_auth_cookie(
        response, request, CSRF_COOKIE, issue_csrf_token(),
        max_age=int(SESSION_TTL.total_seconds()), http_only=False,
    )
    return response


def safe_next(path: str, query: str = "") -> str | None:
    r"""A same-site destination to return to after sign-in, or `None` (SPEC-010 A14).

    **The whole job of this function is refusing to be an open redirector.** A login page that
    reflects an arbitrary `?next=` is a phishing primitive: the victim signs in at the genuine
    site, sees the genuine domain in the address bar, and is then sent wherever the attacker
    named — often a convincing copy asking them to "confirm" the password they just typed.

    So the rule is allow-list shaped rather than deny-list shaped: **a path beginning with a
    single `/`, and nothing else.** Everything a deny-list would have to remember is excluded
    by construction —

        https://evil.example/x   rejected: does not start with `/`
        //evil.example/x         rejected: protocol-relative, a browser reads the host
        /\evil.example/x        rejected: some browsers normalise `\` to `/`
        javascript:alert(1)      rejected: no leading `/`

    `/login` and `/signup` are refused too, so a chain of redirects cannot loop.
    """
    if not path or not path.startswith("/"):
        return None
    # Protocol-relative (`//host`) and the backslash variants some browsers normalise.
    if path.startswith("//") or path.startswith("/\\") or "\\" in path:
        return None
    # A control character or newline would let a caller inject a second header.
    if any(c in path for c in "\r\n\t") or any(ord(c) < 0x20 for c in path):
        return None
    if path in ("/login", "/signup") or path.startswith("/login/"):
        return None
    return f"{path}?{query}" if query else path
