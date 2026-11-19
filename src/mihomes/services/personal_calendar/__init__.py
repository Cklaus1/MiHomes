"""Per-person Google Calendar sync (2026-10-30). See `models/calendar_connection.py`.

    google_client.py   the Calendar REST calls MiHomes needs, over httpx (no google SDK)
    sync.py            reconcile one connection: desired events vs what was last written
    trigger.py         run a sync after a save, off the request thread
    connect.py         the OAuth consent flow's two halves, and disconnect
"""

#: The narrowest scope that fits: create secondary calendars and manage events **on calendars
#: this app created** — no read access to anything else in the person's Google account.
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
