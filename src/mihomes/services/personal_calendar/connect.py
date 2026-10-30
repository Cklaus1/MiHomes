"""Connecting a Google Calendar — consent, token storage, disconnect.

**Separate from sign-in on purpose.** Google sign-in asks for `openid email profile` only; the
calendar is a second, explicit consent from Settings, and may be a *different* Google account
from the one the person signs in with (a work calendar, say). It reuses the sign-in callback
URL (`/auth/google/callback`, an intent cookie tells the two apart), so no extra redirect URI
has to be registered in Google Cloud Console.

What is refused, and why:

- **No refresh token** — without one the connection dies within the hour. `prompt=consent` is
  what makes Google issue one on a re-connect, so this should not happen; if it does, say so
  rather than store a connection that silently stops.
- **Calendar scope not granted** — Google's consent screen lets people untick individual
  permissions. A connection without the scope would fail on every write.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

from sqlalchemy import select

from mihomes.models.calendar_connection import CalendarConnection, CalendarEventLink
from mihomes.services.personal_calendar import CALENDAR_SCOPE

#: Refusals and notices by code — codes, not text, cross the redirect back to Settings.
ERRORS = {
    "scope_denied": (
        "Google didn't grant calendar access. Connect again and leave the calendar box ticked."
    ),
    "no_refresh": "Google didn't give MiHomes lasting access. Please connect again.",
    "exchange_failed": "Google sign-in for the calendar failed. Please try again.",
    "not_configured": "Google isn't set up on this server yet.",
    "needs_reconnect": "Google stopped accepting MiHomes' access. Connect again to resume syncing.",
    "not_connected": "No calendar is connected.",
}
NOTICES = {
    "connected": "Google Calendar connected. Your events are being added now — this can take a "
                 "minute for a busy estate.",
    "sync_started": "Sync started. Refresh in a moment to see the time it finished.",
    "disconnected": "Google Calendar disconnected.",
}


class CalendarConnectError(Exception):
    def __init__(self, code: str) -> None:
        assert code in ERRORS, code
        self.code = code
        super().__init__(ERRORS[code])


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def authorization_url(*, state: str, code_challenge: str, login_hint: str | None) -> str:
    from mihomes.landing import oauth

    params = {
        "client_id": oauth._client_id(),
        "redirect_uri": oauth._redirect_uri(),
        "response_type": "code",
        "scope": f"openid email {CALENDAR_SCOPE}",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # offline + consent: the only combination that reliably returns a refresh token, which
        # is the whole point — the sync runs when the person is not there.
        "access_type": "offline",
        "prompt": "consent select_account",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def _default_exchange(code: str, verifier: str) -> dict:
    from mihomes.landing import oauth

    return oauth.exchange_code(code=code, code_verifier=verifier)


def _default_verify(id_token: str) -> dict:
    from mihomes.landing import oauth

    return oauth.verify_id_token(id_token)


def finish(db, *, membership_id, code: str, verifier: str,
           exchange=_default_exchange, verify=_default_verify) -> CalendarConnection:
    """Exchange the code, check what Google granted, and store the connection. No commit.

    Runs under the account's context (the route binds it). Reconnecting replaces the token in
    place; reconnecting a *different* Google account forgets the old calendar and its links, so
    the next sync builds a fresh one in the new account.
    """
    from mihomes.crypto import encrypt

    try:
        tokens = exchange(code, verifier)
        claims = verify(tokens.get("id_token", ""))
    except Exception:  # noqa: BLE001 — normalised; the provider's text can carry token fragments
        raise CalendarConnectError("exchange_failed") from None

    if CALENDAR_SCOPE not in (tokens.get("scope") or "").split():
        raise CalendarConnectError("scope_denied")
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise CalendarConnectError("no_refresh")
    email = claims.get("email") or ""

    conn = db.execute(select(CalendarConnection).where(
        CalendarConnection.membership_id == membership_id)).scalar_one_or_none()
    if conn is None:
        conn = CalendarConnection(membership_id=membership_id, provider="google",
                                  google_email=email, refresh_token=encrypt(refresh),
                                  status="active")
        db.add(conn)
    else:
        if conn.google_email.lower() != email.lower():
            conn.calendar_id = None
            for link in db.execute(select(CalendarEventLink).where(
                    CalendarEventLink.connection_id == conn.id)).scalars():
                db.delete(link)
        conn.google_email = email
        conn.refresh_token = encrypt(refresh)
        conn.status = "active"
        conn.last_error = None
        conn.syncing_until = None
    db.flush()
    return conn


def own_connection(db, membership_id) -> CalendarConnection | None:
    return db.execute(select(CalendarConnection).where(
        CalendarConnection.membership_id == membership_id)).scalar_one_or_none()


def forget_in_google(refresh_token: str, calendar_id: str | None, *, delete_calendar: bool,
                     client_factory=None) -> None:
    """Delete the MiHomes calendar (if asked) and revoke the token. Best effort, never raises.

    Delete *before* revoking: once the token is revoked nothing can remove the calendar, and the
    person would be left with an orphan to delete by hand.
    """
    import logging

    from mihomes.services.personal_calendar import google_client

    log = logging.getLogger(__name__)
    if delete_calendar and calendar_id:
        try:
            client = (client_factory or google_client.GoogleCalendarClient)(refresh_token)
            client.delete_calendar(calendar_id)
        except Exception:  # noqa: BLE001 — the local disconnect has already happened
            log.exception("could not delete the MiHomes calendar in Google")
    google_client.revoke(refresh_token)
