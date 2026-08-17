"""G12 · §6 Step 12 — the identity provider (A15).

**The token verification is SPEC-001's, not a second implementation.** `landing/oauth.py` already
does the real work — JWKS fetch, signature check, `aud`/`iss`/`exp` validation with a 60-second
skew — and it has tests. Writing a second verifier would mean two places to get `aud` wrong, and the
one that is not being read is the one that rots. This module wraps it behind a Protocol so tests can
substitute a fake without patching a live HTTP call, and adds the part Phase 0 deliberately refused:
a `users` row.

**`sub` is the identity, not the email.** Google's `sub` is stable and unique forever; an email
address can change hands or be re-assigned within a workspace. Upserting on email would eventually
hand one person's account to another. The email is stored for display only, and refreshed on each
sign-in so the UI does not show a stale address.

**`email_verified` is enforced.** Google will issue an ID token for an unverified address; treating
that as an identity lets someone claim an email they do not control if the provider ever permits an
unverified sign-up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from mihomes.models.user import User

__all__ = [
    "GoogleOIDCProvider",
    "IdentityClaims",
    "IdentityProvider",
    "InvalidIdentityToken",
    "upsert_user",
]


class InvalidIdentityToken(Exception):
    """The ID token could not be verified, or its claims are unusable."""


@dataclass(frozen=True)
class IdentityClaims:
    """The claims the application actually uses. Anything else is deliberately dropped."""

    subject: str
    email: str
    name: str | None = None
    picture: str | None = None


@runtime_checkable
class IdentityProvider(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        ...

    def exchange_code(self, *, code: str, code_verifier: str) -> str:
        """Swap an authorization code for an **ID token** (the raw JWT)."""
        ...

    def verify(self, id_token: str) -> IdentityClaims:
        """Verify signature and claims, or raise `InvalidIdentityToken`."""
        ...


class GoogleOIDCProvider:
    """Google, via the verifier SPEC-001 already ships."""

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        from urllib.parse import urlencode

        from mihomes.landing import oauth

        params = {
            "client_id": oauth._client_id(),
            "redirect_uri": oauth._redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # `select_account` rather than the default: on a shared machine the default silently
            # reuses whoever is already signed in to Google, which is a surprising way to end up in
            # someone else's estate.
            "prompt": "select_account",
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    def exchange_code(self, *, code: str, code_verifier: str) -> str:
        from mihomes.landing import oauth

        try:
            tokens = oauth.exchange_code(code=code, code_verifier=code_verifier)
        except Exception as e:  # noqa: BLE001 — normalised to our own type
            raise InvalidIdentityToken(f"code exchange failed: {e}") from e
        id_token = tokens.get("id_token")
        if not id_token:
            raise InvalidIdentityToken("token response carried no id_token")
        return id_token

    def verify(self, id_token: str) -> IdentityClaims:
        from mihomes.landing import oauth

        try:
            claims = oauth.verify_id_token(id_token)
        except Exception as e:  # noqa: BLE001 — a bad signature, aud, iss or exp all land here
            raise InvalidIdentityToken(str(e)) from e
        return claims_from_dict(claims)


def claims_from_dict(claims: dict) -> IdentityClaims:
    """Validate the claim *content* after the signature has been checked.

    A valid signature only means Google issued the token; it says nothing about whether the claims
    are usable. `sub` must be present (it is the identity) and the email must be **verified**.
    """
    subject = claims.get("sub")
    if not subject:
        raise InvalidIdentityToken("token has no 'sub' claim, so there is no stable identity")

    email = claims.get("email")
    if not email:
        raise InvalidIdentityToken("token has no 'email' claim")

    verified = claims.get("email_verified")
    # Google sends a real boolean; some providers send the string "true". Accept both, reject
    # anything else — including absent, which must not be read as verified.
    if verified not in (True, "true", "True"):
        raise InvalidIdentityToken(
            f"email {email!r} is not verified by the identity provider; refusing to treat it as "
            "an identity"
        )

    return IdentityClaims(
        subject=str(subject),
        email=str(email),
        name=claims.get("name"),
        picture=claims.get("picture"),
    )


def upsert_user(db: DbSession, claims: IdentityClaims) -> User:
    """Find-or-create the `users` row for these claims, keyed on `sub`.

    `users` is GLOBAL (D3), so this runs with no account context — which is exactly why the G8 read
    filter skips statements touching no tenant-owned entity. Sign-in has to work *before* any
    account exists, and an unconditional tenant check here would make it impossible (the defect
    recorded against §4.4).
    """
    user = db.execute(
        select(User).where(User.google_sub == claims.subject)
    ).scalar_one_or_none()

    if user is None:
        user = User(
            id=uuid.uuid4(),
            google_sub=claims.subject,
            email=claims.email,
            name=claims.name,
            avatar_url=claims.picture,
        )
        db.add(user)
    else:
        # Refreshed on every sign-in: display fields drift, and `sub` is what identity is keyed on,
        # so updating them cannot move the account to a different person.
        user.email = claims.email
        if claims.name:
            user.name = claims.name
        if claims.picture:
            user.avatar_url = claims.picture

    db.flush()
    return user
