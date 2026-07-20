"""Google Calendar provider — OAuth2-based integration using google-api-python-client."""

import json
import os
from datetime import datetime, date, timezone
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = Path(os.path.expanduser("~/.mihomes/google_credentials.json"))
TOKEN_FILE = Path(os.path.expanduser("~/.mihomes/google_token.json"))


def _get_service():
    """Authenticate and return a Google Calendar API service object."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                # Refresh token revoked or expired — delete token and force full re-auth
                TOKEN_FILE.unlink(missing_ok=True)
                creds = None
        if not creds:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Google credentials not found at {CREDENTIALS_FILE}. "
                    "Download OAuth credentials from Google Cloud Console and save as "
                    f"{CREDENTIALS_FILE}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def _to_rfc3339(dt) -> str:
    """Convert date or datetime to RFC3339 string."""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    # date object — use all-day format
    return dt.isoformat()


def _parse_dt(event_time: dict):
    """Parse Google Calendar event time dict into a datetime or date."""
    if "dateTime" in event_time:
        return datetime.fromisoformat(event_time["dateTime"])
    if "date" in event_time:
        return datetime.fromisoformat(event_time["date"] + "T00:00:00")
    return None


class GoogleCalendarProvider:
    """Google Calendar integration via OAuth2."""

    def __init__(self, calendar_id: str = "primary"):
        self.calendar_id = calendar_id
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = _get_service()
        return self._service

    def create_event(self, title: str, start: datetime, end: datetime, *,
                     description: str | None = None,
                     location: str | None = None,
                     attendees: list[str] | None = None) -> dict:
        body = {
            "summary": title,
            "start": {"dateTime": _to_rfc3339(start), "timeZone": "UTC"},
            "end": {"dateTime": _to_rfc3339(end), "timeZone": "UTC"},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]

        result = self.service.events().insert(
            calendarId=self.calendar_id, body=body
        ).execute()
        return {"event_id": result["id"], "html_link": result.get("htmlLink")}

    def update_event(self, event_id: str, **kwargs) -> dict:
        event = self.service.events().get(
            calendarId=self.calendar_id, eventId=event_id
        ).execute()

        if "title" in kwargs:
            event["summary"] = kwargs["title"]
        if "description" in kwargs:
            event["description"] = kwargs["description"]
        if "start" in kwargs:
            event["start"] = {"dateTime": _to_rfc3339(kwargs["start"]), "timeZone": "UTC"}
        if "end" in kwargs:
            event["end"] = {"dateTime": _to_rfc3339(kwargs["end"]), "timeZone": "UTC"}

        result = self.service.events().update(
            calendarId=self.calendar_id, eventId=event_id, body=event
        ).execute()
        return {"event_id": result["id"], "html_link": result.get("htmlLink")}

    def delete_event(self, event_id: str) -> bool:
        self.service.events().delete(
            calendarId=self.calendar_id, eventId=event_id
        ).execute()
        return True

    def list_events(self, start: datetime, end: datetime) -> list[dict]:
        result = self.service.events().list(
            calendarId=self.calendar_id,
            timeMin=_to_rfc3339(start),
            timeMax=_to_rfc3339(end),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = []
        for e in result.get("items", []):
            events.append({
                "id": e["id"],
                "title": e.get("summary", "(no title)"),
                "start": _parse_dt(e["start"]),
                "end": _parse_dt(e["end"]),
                "description": e.get("description"),
                "location": e.get("location"),
                "html_link": e.get("htmlLink"),
            })
        return events

    def sync_from_mihomes(self, events: list[dict]) -> list[dict]:
        """Push MiHomes events to Google Calendar."""
        results = []
        for ev in events:
            try:
                start = ev.get("start") or datetime.now(timezone.utc)
                end = ev.get("end") or start
                if isinstance(start, date) and not isinstance(start, datetime):
                    start = datetime(start.year, start.month, start.day, 9, 0, tzinfo=timezone.utc)
                    end = datetime(end.year, end.month, end.day, 10, 0, tzinfo=timezone.utc)
                result = self.create_event(
                    title=ev["title"],
                    start=start,
                    end=end,
                    description=ev.get("description"),
                )
                results.append({"title": ev["title"], "status": "created", **result})
            except Exception as e:
                results.append({"title": ev["title"], "status": "error", "error": str(e)})
        return results
