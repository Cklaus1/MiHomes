"""The estate's clock (services/localtime): its today and its local times (2026-11-04)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from mihomes.services import localtime
from mihomes.services.config_service import set_config
from mihomes.web.deps import _localtime

# 01:30 UTC on 4 Nov 2026 is 20:30 the evening before in New York (EST, UTC-5): the hour the
# server's date used to flip a day early.
LATE_EVENING_UTC = datetime(2026, 11, 4, 1, 30)


def _with(zone, fn):
    token = localtime.estate_tz.set(zone)
    try:
        return fn()
    finally:
        localtime.estate_tz.reset(token)


def test_unbound_is_utc_as_before():
    assert localtime.today() == datetime.now(timezone.utc).date()


def test_today_is_the_estates_date():
    for zone in ("Pacific/Kiritimati", "Pacific/Pago_Pago", "America/New_York"):
        assert _with(zone, localtime.today) == datetime.now(ZoneInfo(zone)).date(), zone


def test_naive_instants_are_utc_and_shown_in_the_estate_zone():
    local = _with("America/New_York", lambda: localtime.to_local(LATE_EVENING_UTC))
    assert (local.day, local.hour, local.minute) == (3, 20, 30)


def test_aware_instants_convert_too():
    aware = LATE_EVENING_UTC.replace(tzinfo=timezone.utc)
    assert _with("Asia/Tokyo", lambda: localtime.to_local(aware)).hour == 10


def test_unknown_zone_falls_back_to_utc():
    assert _with("Mars/Olympus_Mons", lambda: localtime.to_local(LATE_EVENING_UTC)).hour == 1


def test_template_filter():
    assert _localtime(None) == ""
    out = _with("America/New_York", lambda: _localtime(LATE_EVENING_UTC, "%b %d, %H:%M"))
    assert out == "Nov 03, 20:30"


def test_bound_reads_the_account_setting(session):
    set_config(session, "locale.timezone", "Europe/Madrid")
    session.flush()
    with localtime.bound(session):
        assert localtime.estate_tz.get() == "Europe/Madrid"
    assert localtime.estate_tz.get() == "UTC"
