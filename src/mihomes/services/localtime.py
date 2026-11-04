"""The estate's clock: its "today" and its local times, not the server's (2026-11-04).

The server runs on UTC, so `date.today()` turned over at 7–8pm in Atlanta. After that, the
dashboard's Today list, "My Overdue", the Tasks page, alerts and the calendar were a day ahead.
Chat, conversations and notifications printed UTC times.

The estate's zone is the Settings value `locale.timezone` (default America/New_York; see
`config_service.estate_zone`). It is bound once per request in `web/deps.py`, beside the
estate currency, and once per account in the `mihomes jobs` sweeps, so code with no session at
hand still gets the right day. Unbound means UTC: the CLI's previous behaviour, unchanged.

- `today()` replaces `date.today()` everywhere a date means "the estate's today".
- `to_local(dt)` converts a stored instant for display. Naive datetimes are UTC by this
  codebase's convention (`models.TimestampMixin` stamps `datetime.now(timezone.utc)` into naive
  columns).
- The `localtime` template filter (`web/deps.py`) is `to_local` plus `strftime`.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

estate_tz: ContextVar[str] = ContextVar("estate_tz", default="UTC")


def zone() -> ZoneInfo:
    try:
        return ZoneInfo(estate_tz.get())
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def today() -> date:
    """Today's date where the estate is."""
    return datetime.now(zone()).date()


def to_local(dt: datetime | None) -> datetime | None:
    """`dt` in the estate's zone. A naive `dt` is read as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone())


@contextmanager
def bound(session):
    """Bind the estate zone from `session`'s account settings for the duration of the block."""
    from mihomes.services.config_service import estate_zone

    token = estate_tz.set(estate_zone(session).key)
    try:
        yield
    finally:
        estate_tz.reset(token)
