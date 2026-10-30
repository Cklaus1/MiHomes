"""The handful of Google Calendar REST calls the sync makes, over httpx.

No `google-api-python-client`: it is not a dependency (`pyproject.toml` has httpx), and five
endpoints do not justify one. Tests substitute a fake with the same methods.

Errors are normalised to three outcomes the sync acts on, because they need different
responses: the person must reconnect (`ReconnectNeeded`), the calendar was deleted in Google and
must be recreated (`CalendarGone`), or Google is busy and the run should stop for now
(`GoogleBusy`). Anything else is `GoogleError`.
"""

from __future__ import annotations

import os
import time

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/calendar/v3"


class GoogleError(Exception):
    pass


class ReconnectNeeded(GoogleError):
    """The refresh token was refused — revoked in Google, expired, or the scope removed."""


class CalendarGone(GoogleError):
    """The MiHomes calendar no longer exists in Google (deleted by the person)."""


class GoogleBusy(GoogleError):
    """Rate limited or a server error: stop this run, the next one picks up."""


class EventExists(GoogleError):
    """`insert` with an id Google already has (possibly a deleted event)."""


def refresh_access_token(refresh_token: str, *, http: httpx.Client | None = None) -> str:
    client = http or httpx.Client(timeout=15)
    resp = client.post(TOKEN_URL, data={
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    try:
        error = resp.json().get("error") if resp.status_code == 400 else None
    except ValueError:
        error = None
    if error in ("invalid_grant", "unauthorized_client"):
        raise ReconnectNeeded("Google refused the stored sign-in; reconnect the calendar")
    if resp.status_code >= 500 or resp.status_code == 429:
        raise GoogleBusy(f"token endpoint {resp.status_code}")
    if resp.status_code != 200:
        raise GoogleError(f"token endpoint {resp.status_code}")
    return resp.json()["access_token"]


def revoke(refresh_token: str) -> None:
    """Best effort: a token already revoked answers 400, which is the outcome wanted anyway."""
    try:
        httpx.post(REVOKE_URL, data={"token": refresh_token}, timeout=10)
    except httpx.HTTPError:
        pass


class GoogleCalendarClient:
    """One connection's view of Google Calendar, authenticated by its refresh token."""

    def __init__(self, refresh_token: str, *, pace: float = 0.12,
                 http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=20)
        self._token = refresh_access_token(refresh_token, http=self._http)
        # Google rate-limits bursts of writes per user; a first sync of a busy estate is the
        # burst. A small fixed gap keeps a backfill under the limit without a retry ladder.
        self._pace = pace

    def _call(self, method: str, path: str, *, json: dict | None = None,
              calendar: bool = True) -> dict | None:
        resp = self._http.request(method, f"{API}{path}", json=json,
                                  headers={"Authorization": f"Bearer {self._token}"})
        if method != "GET" and self._pace:
            time.sleep(self._pace)
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code == 204:
            return None
        if resp.status_code == 401:
            raise ReconnectNeeded("Google refused the access token")
        if resp.status_code == 409:
            raise EventExists(path)
        if resp.status_code == 429 or resp.status_code >= 500 or (
            resp.status_code == 403 and "ateLimit" in resp.text
        ):
            raise GoogleBusy(f"{method} {path}: {resp.status_code}")
        if resp.status_code == 403:
            # The calendar scope was un-ticked on the consent screen, or removed since.
            raise ReconnectNeeded("Google denied calendar access")
        if resp.status_code in (404, 410):
            raise _NotFound(path)
        raise GoogleError(f"{method} {path}: {resp.status_code} {resp.text[:200]}")

    # ── calendars ───────────────────────────────────────────────────────────

    def create_calendar(self, summary: str, time_zone: str) -> str:
        return self._call("POST", "/calendars",
                          json={"summary": summary, "timeZone": time_zone})["id"]

    def delete_calendar(self, calendar_id: str) -> None:
        try:
            self._call("DELETE", f"/calendars/{_q(calendar_id)}")
        except _NotFound:
            pass

    # ── events ──────────────────────────────────────────────────────────────

    def put_event(self, calendar_id: str, event_id: str, body: dict) -> None:
        """Create or overwrite the event with this id.

        The id is ours (derived from the connection and the item), so a retry after a lost
        response cannot make a twin: the second insert answers 409 and becomes an update. An
        update also revives an event that was deleted — Google keeps the id as `cancelled`.
        """
        cal = _q(calendar_id)
        full = {**body, "id": event_id, "status": "confirmed"}
        try:
            self._call("POST", f"/calendars/{cal}/events", json=full)
        except EventExists:
            self._call("PUT", f"/calendars/{cal}/events/{event_id}", json=full)
        except _NotFound:
            raise CalendarGone(calendar_id) from None

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        try:
            self._call("DELETE", f"/calendars/{_q(calendar_id)}/events/{event_id}")
        except _NotFound:
            pass  # already gone — the outcome wanted


class _NotFound(GoogleError):
    pass


def _q(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
