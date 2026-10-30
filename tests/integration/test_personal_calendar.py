"""Per-person Google Calendar sync (2026-10-30).

A fake Google stands in at the client seam (`client_factory`), so these tests exercise every
decision the sync makes — what is visible, what changed, what must be deleted — without HTTP.
The suite runs as a superuser, so RLS itself is not what is under test here; the ORM tenant
filter and the sync's own refusals are.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, time, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from mihomes.auth.csrf import CSRF_COOKIE
from mihomes.models.calendar_connection import CalendarConnection, CalendarEventLink
from mihomes.services.personal_calendar import CALENDAR_SCOPE, connect, trigger
from mihomes.services.personal_calendar.google_client import CalendarGone, ReconnectNeeded
from mihomes.services.personal_calendar.sync import event_id_for, sync_connection
from mihomes.tenancy import account_context

CSRF = "csrf-cal"


@pytest.fixture(autouse=True)
def revoked(monkeypatch):
    """No test may reach Google's revoke endpoint; record what would have been revoked."""
    from mihomes.services.personal_calendar import google_client

    calls = []
    monkeypatch.setattr(google_client, "revoke", calls.append)
    return calls


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    from mihomes import crypto

    monkeypatch.setenv(crypto.SECRET_KEY_ENV, crypto.generate_key())
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")


class FakeGoogle:
    def __init__(self):
        self.calendars: dict[str, tuple[str, str]] = {}
        self.events: dict[tuple[str, str], dict] = {}
        self.writes = self.deletes = 0
        self.tokens: list[str] = []
        self.refuse = False

    def __call__(self, refresh_token):  # the client factory
        self.tokens.append(refresh_token)
        if self.refuse:
            raise ReconnectNeeded("revoked")
        return self

    def create_calendar(self, summary, time_zone):
        cid = f"cal-{len(self.calendars) + 1}"
        self.calendars[cid] = (summary, time_zone)
        return cid

    def delete_calendar(self, cid):
        self.calendars.pop(cid, None)
        self.events = {k: v for k, v in self.events.items() if k[0] != cid}

    def put_event(self, cid, eid, body):
        if cid not in self.calendars:
            raise CalendarGone(cid)
        self.events[(cid, eid)] = body
        self.writes += 1

    def delete_event(self, cid, eid):
        self.events.pop((cid, eid), None)
        self.deletes += 1

    def bodies(self):
        return {b["summary"]: b for b in self.events.values()}


@pytest.fixture
def world(web_client_as, account_a):
    """An estate with two properties, an owner client, and a session factory on the test's
    connection (savepoint-joined, so everything rolls back)."""
    from mihomes.services.appointment import create_appointment
    from mihomes.services.property import create_property
    from mihomes.services.task import create_task

    conn = web_client_as.connection
    Session = sessionmaker(bind=conn, future=True, join_transaction_mode="create_savepoint")

    @contextmanager
    def factory():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    soon = date.today() + timedelta(days=5)
    with account_context(account_a), factory() as s:
        main = create_property(s, "Main House", address="1 Belle Rd")
        cottage = create_property(s, "Guest Cottage")
        create_task(s, "Clean gutters", main.slug, due_date=soon)
        create_task(s, "Paint shed", cottage.slug, due_date=soon)
        create_task(s, "No date yet", main.slug)
        create_appointment(s, "HVAC service", main.slug, soon, start_time=time(10, 30))
        ids = {"main": main.id, "cottage": cottage.id}

    class W:
        pass

    w = W()
    w.factory, w.account, w.props, w.conn, w.soon = factory, account_a, ids, conn, soon
    w.owner = web_client_as("owner")
    w.owner.cookies.set(CSRF_COOKIE, CSRF)
    w.make = web_client_as
    return w


def _connect(w, membership_id, token="rt-1", calendar_id=None) -> uuid.UUID:
    from mihomes.crypto import encrypt

    with account_context(w.account), w.factory() as s:
        c = CalendarConnection(membership_id=membership_id, provider="google",
                               google_email="me@gmail.com", refresh_token=encrypt(token),
                               status="active", calendar_id=calendar_id)
        s.add(c)
        s.flush()
        return c.id


def _sync(w, conn_id, fake):
    with account_context(w.account):
        return sync_connection(w.factory, conn_id, client_factory=fake)


# ── The sync ─────────────────────────────────────────────────────────────────

def test_first_sync_creates_one_calendar_and_every_visible_item(world):
    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    r = _sync(world, cid, fake)
    assert r.status == "ok", r
    assert list(fake.calendars.values())[0][0].startswith("MiHomes — ")
    assert set(fake.bodies()) == {"Task: Clean gutters", "Task: Paint shed", "HVAC service"}
    assert fake.tokens == ["rt-1"], "the stored token was not decrypted for the client"


def test_event_shapes_use_the_estate_time_zone_and_exclusive_all_day_end(world):
    fake = FakeGoogle()
    _sync(world, _connect(world, world.owner.membership_id), fake)
    appt = fake.bodies()["HVAC service"]
    assert appt["start"]["dateTime"] == f"{world.soon.isoformat()}T10:30:00"
    assert appt["start"]["timeZone"] == "America/New_York"
    assert appt["location"] == "1 Belle Rd"
    task = fake.bodies()["Task: Clean gutters"]
    assert task["start"] == {"date": world.soon.isoformat()}
    assert task["end"] == {"date": (world.soon + timedelta(days=1)).isoformat()}


def test_a_second_sync_writes_nothing(world):
    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    _sync(world, cid, fake)
    before = fake.writes
    r = _sync(world, cid, fake)
    assert fake.writes == before and r.unchanged == 3
    assert len(fake.calendars) == 1, "a second sync made a second calendar"


def test_edits_update_and_finished_tasks_are_removed(world):
    from mihomes.models.task import Task, TaskStatus

    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    _sync(world, cid, fake)
    with account_context(world.account), world.factory() as s:
        gutters = s.execute(select(Task).where(Task.title == "Clean gutters")).scalar_one()
        gutters.title = "Clean ALL gutters"
        shed = s.execute(select(Task).where(Task.title == "Paint shed")).scalar_one()
        shed.status = TaskStatus.COMPLETED
    r = _sync(world, cid, fake)
    assert set(fake.bodies()) == {"Task: Clean ALL gutters", "HVAC service"}
    assert r.written == 1 and r.deleted == 1


def test_event_ids_are_stable_so_a_retry_cannot_duplicate(world):
    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    _sync(world, cid, fake)
    with account_context(world.account), world.factory() as s:
        links = s.execute(select(CalendarEventLink)).scalars().all()
        for link in links:
            assert link.google_event_id == event_id_for(cid, link.entity_type, link.entity_id)
        # Simulate a lost response: the link was never saved, so the next run re-sends it.
        s.delete(links[0])
    _sync(world, cid, fake)
    assert len(fake.events) == 3, "a re-sent event created a twin"


def test_staff_only_get_the_properties_they_can_see(world):
    fake = FakeGoogle()
    staff = world.make("staff", scoped_to=[world.props["cottage"]])
    _sync(world, _connect(world, staff.membership_id), fake)
    assert set(fake.bodies()) == {"Task: Paint shed"}


def test_narrowing_scope_or_revoking_removes_events(world, revoked):
    fake = FakeGoogle()
    staff = world.make("staff", scoped_to=[world.props["main"], world.props["cottage"]])
    cid = _connect(world, staff.membership_id)
    _sync(world, cid, fake)
    assert len(fake.events) == 3
    world.conn.execute(text("delete from membership_property_scopes where membership_id = :m "
                            "and property_id = :p"),
                       {"m": staff.membership_id, "p": world.props["main"]})
    _sync(world, cid, fake)
    assert set(fake.bodies()) == {"Task: Paint shed"}
    world.conn.execute(text("update memberships set status = 'revoked' where id = :m"),
                       {"m": staff.membership_id})
    assert _sync(world, cid, fake).status == "removed"
    assert revoked == ["rt-1"]
    assert fake.events == {} and fake.calendars == {}, "a removed member kept the calendar"
    assert world.conn.execute(text("select count(*) from calendar_connections where id = :i"),
                              {"i": cid}).scalar() == 0, "a removed member's token was kept"


def test_items_that_age_out_of_the_window_keep_their_event(world):
    from mihomes.models.task import Task

    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    _sync(world, cid, fake)
    with account_context(world.account), world.factory() as s:
        t = s.execute(select(Task).where(Task.title == "Clean gutters")).scalar_one()
        t.due_date = date.today() - timedelta(days=90)
    r = _sync(world, cid, fake)
    assert r.deleted == 0 and "Task: Clean gutters" in fake.bodies()


def test_a_calendar_deleted_in_google_is_recreated(world):
    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    _sync(world, cid, fake)
    fake.calendars.clear()
    fake.events.clear()
    from mihomes.models.task import Task

    with account_context(world.account), world.factory() as s:
        s.execute(select(Task).where(Task.title == "Paint shed")).scalar_one().title = "Paint it"
    r = _sync(world, cid, fake)
    assert r.status == "ok"
    assert len(fake.calendars) == 1 and len(fake.events) == 3


def test_revoked_google_access_marks_needs_reconnect_and_stops(world):
    fake = FakeGoogle()
    fake.refuse = True
    cid = _connect(world, world.owner.membership_id)
    r = _sync(world, cid, fake)
    assert r.status == "reconnect"
    with account_context(world.account), world.factory() as s:
        conn = s.get(CalendarConnection, cid)
        assert conn.status == "needs_reconnect" and conn.last_error
        assert conn.syncing_until is None, "the lease was not released"
    assert _sync(world, cid, FakeGoogle()).status == "skipped"


def test_a_sync_already_running_is_not_doubled(world):
    from datetime import UTC, datetime

    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    with account_context(world.account), world.factory() as s:
        s.get(CalendarConnection, cid).syncing_until = datetime.now(UTC) + timedelta(minutes=5)
    r = _sync(world, cid, fake)
    assert r.status == "busy" and fake.tokens == []
    with account_context(world.account), world.factory() as s:
        assert s.get(CalendarConnection, cid).needs_sync_at is not None


def test_refuses_to_run_without_an_account_bound(world):
    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    with pytest.raises(LookupError):
        sync_connection(world.factory, cid, client_factory=fake)
    assert fake.tokens == []


def test_another_estates_context_cannot_touch_the_connection(world, account_b):
    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    _sync(world, cid, fake)
    with account_context(account_b):
        r = sync_connection(world.factory, cid, client_factory=fake)
    assert r.status == "skipped" and len(fake.events) == 3


def test_the_refresh_token_is_encrypted_at_rest_and_redacted_in_exports(world):
    from mihomes.services.privacy.export import build_export

    cid = _connect(world, world.owner.membership_id, token="plain-refresh-token")
    stored = world.conn.execute(text("select refresh_token from calendar_connections "
                                     "where id = :i"), {"i": cid}).scalar()
    assert "plain-refresh-token" not in stored and stored.startswith("enc:")
    with account_context(world.account), world.factory() as s:
        rows = build_export(s, world.account).tables["calendar_connections"]
    assert rows and all(r["refresh_token"] == "[redacted]" for r in rows)


# ── Connecting ───────────────────────────────────────────────────────────────

def _fake_finish(scope=CALENDAR_SCOPE, refresh="rt-new", email="me@gmail.com"):
    def finish(db, **kw):
        return connect.finish(
            db, **kw,
            exchange=lambda code, verifier: {"id_token": "t", "scope": f"openid email {scope}",
                                             "refresh_token": refresh},
            verify=lambda t: {"email": email})
    return finish


def _round_trip(client, monkeypatch, finish):
    monkeypatch.setattr("mihomes.web.routes.auth._calendar_finish", lambda: finish)
    start = client.post("/settings/calendar/google/connect", data={"csrf_token": CSRF},
                        follow_redirects=False)
    assert start.status_code == 303, start.text
    state = client.cookies.get("mihomes_oauth_state")
    return start, client.get(f"/auth/google/callback?code=c&state={state}",
                             follow_redirects=False)


def test_connect_asks_google_for_offline_calendar_access(world, monkeypatch):
    start, _ = _round_trip(world.owner, monkeypatch, _fake_finish())
    url = start.headers["location"]
    assert "calendar.app.created" in url and "access_type=offline" in url
    assert "prompt=consent" in url and "login_hint=" in url


def test_connect_stores_an_encrypted_connection(world, monkeypatch):
    from mihomes.crypto import decrypt

    _, done = _round_trip(world.owner, monkeypatch, _fake_finish())
    assert "calendar=connected" in done.headers["location"]
    with account_context(world.account), world.factory() as s:
        conn = s.execute(select(CalendarConnection)).scalar_one()
        assert conn.membership_id == world.owner.membership_id
        assert decrypt(conn.refresh_token) == "rt-new" and conn.refresh_token != "rt-new"
    assert not world.owner.cookies.get("mihomes_oauth_intent")


@pytest.mark.parametrize("kw,code", [({"scope": ""}, "scope_denied"),
                                     ({"refresh": None}, "no_refresh")])
def test_connect_refuses_partial_grants(world, monkeypatch, kw, code):
    _, done = _round_trip(world.owner, monkeypatch, _fake_finish(**kw))
    assert f"calendar_error={code}" in done.headers["location"]
    assert world.conn.execute(text("select count(*) from calendar_connections")).scalar() == 0


def test_connect_needs_csrf(world):
    r = world.owner.post("/settings/calendar/google/connect", data={"csrf_token": "x"},
                         follow_redirects=False)
    assert r.status_code == 403


def test_the_flow_finishes_only_for_who_started_it(world, monkeypatch):
    monkeypatch.setattr("mihomes.web.routes.auth._calendar_finish", lambda: _fake_finish())
    world.owner.post("/settings/calendar/google/connect", data={"csrf_token": CSRF},
                     follow_redirects=False)
    other = world.make("admin")
    from mihomes.auth.sessions import SESSION_COOKIE

    world.owner.cookies.set(SESSION_COOKIE, other.cookies.get(SESSION_COOKIE))
    state = world.owner.cookies.get("mihomes_oauth_state")
    r = world.owner.get(f"/auth/google/callback?code=c&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert world.conn.execute(text("select count(*) from calendar_connections")).scalar() == 0


def test_reconnecting_a_different_google_account_starts_a_fresh_calendar(world, monkeypatch):
    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    _sync(world, cid, fake)
    _round_trip(world.owner, monkeypatch, _fake_finish(email="other@gmail.com"))
    with account_context(world.account), world.factory() as s:
        conn = s.get(CalendarConnection, cid)
        assert conn.calendar_id is None and conn.google_email == "other@gmail.com"
        assert s.execute(select(CalendarEventLink)).first() is None


# ── Settings card ────────────────────────────────────────────────────────────

def test_settings_offers_connect_then_shows_status(world):
    page = world.owner.get("/settings").text
    assert 'action="/settings/calendar/google/connect"' in page
    _connect(world, world.owner.membership_id)
    page = world.owner.get("/settings").text
    assert "me@gmail.com" in page and 'action="/settings/calendar/sync"' in page


def test_staff_see_the_card_too(world):
    staff = world.make("staff", scoped_to=[world.props["cottage"]])
    assert 'id="calendar"' in staff.get("/settings/connections").text


def test_sync_now_queues_a_background_sync(world, monkeypatch):
    calls = []
    monkeypatch.setattr(trigger, "request_sync", calls.append)
    _connect(world, world.owner.membership_id)
    r = world.owner.post("/settings/calendar/sync", data={"csrf_token": CSRF},
                         follow_redirects=False)
    assert "calendar=sync_started" in r.headers["location"]
    assert calls == [world.account]


def test_disconnect_removes_the_connection_and_cleans_up_google(world, monkeypatch):
    calls = []
    monkeypatch.setattr(connect, "forget_in_google",
                        lambda token, cal, *, delete_calendar: calls.append((token, cal,
                                                                             delete_calendar)))
    _connect(world, world.owner.membership_id, token="rt-x", calendar_id="cal-9")
    r = world.owner.post("/settings/calendar/disconnect",
                         data={"csrf_token": CSRF, "delete_calendar": "1"},
                         follow_redirects=False)
    assert "calendar=disconnected" in r.headers["location"]
    assert calls == [("rt-x", "cal-9", True)]
    assert world.conn.execute(text("select count(*) from calendar_connections")).scalar() == 0


# ── The retired machine-wide calendar ────────────────────────────────────────

def test_the_calendar_page_no_longer_reads_a_machine_wide_google_calendar(world, monkeypatch):
    from mihomes.services import calendar_sync

    def boom():
        raise AssertionError("the Calendar page read the machine-wide Google calendar")

    monkeypatch.setattr(calendar_sync, "is_google_auth_available", lambda: True)
    monkeypatch.setattr(calendar_sync, "_get_provider", boom)
    assert world.owner.get("/calendar/").status_code == 200


# ── The save trigger ─────────────────────────────────────────────────────────

def test_a_committed_save_queues_its_account_and_a_rollback_does_not(world, monkeypatch):
    from mihomes.models.task import Task

    calls = []
    monkeypatch.setenv("MIHOMES_CALENDAR_AUTOSYNC", "1")
    monkeypatch.setattr(trigger, "request_sync", calls.append)
    trigger.install()
    with account_context(world.account), world.factory() as s:
        s.execute(select(Task).where(Task.title == "Paint shed")).scalar_one().title = "x"
    assert calls == [world.account]

    calls.clear()
    with account_context(world.account):
        with pytest.raises(RuntimeError), world.factory() as s:
            s.execute(select(Task).where(Task.title == "x")).scalar_one().title = "y"
            s.flush()
            raise RuntimeError("abort")
    assert calls == []


def test_nothing_changed_means_no_call_to_google_at_all(world):
    """Every save in the estate queues a sync; an idle one must not even refresh the token."""
    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    _sync(world, cid, fake)
    _sync(world, cid, fake)
    assert fake.tokens == ["rt-1"]


def test_purge_removes_every_members_calendar_from_google(world, monkeypatch):
    from mihomes.services.personal_calendar import connect as connect_mod

    calls = []
    monkeypatch.setattr(connect_mod, "forget_in_google",
                        lambda token, cal, *, delete_calendar: calls.append((token, cal,
                                                                             delete_calendar)))
    _connect(world, world.owner.membership_id, token="rt-p", calendar_id="cal-p")
    with account_context(world.account), world.factory() as s:
        assert connect_mod.forget_account(s, world.account) == 1
    assert calls == [("rt-p", "cal-p", True)]


# ── Google's 403s mean different things ──────────────────────────────────────

def _client_answering(status, reason=None):
    import httpx

    from mihomes.services.personal_calendar.google_client import GoogleCalendarClient

    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at"})
        body = {"error": {"errors": [{"reason": reason}]}} if reason else {}
        return httpx.Response(status, json=body)

    return GoogleCalendarClient("rt", pace=0, http=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"])
def test_403_rate_and_quota_limits_are_busy_not_reconnect(reason):
    from mihomes.services.personal_calendar.google_client import GoogleBusy

    with pytest.raises(GoogleBusy):
        _client_answering(403, reason).create_calendar("x", "UTC")


def test_403_api_not_enabled_is_a_setup_error_not_reconnect():
    from mihomes.services.personal_calendar.google_client import GoogleError, ReconnectNeeded

    with pytest.raises(GoogleError) as info:
        _client_answering(403, "accessNotConfigured").create_calendar("x", "UTC")
    assert not isinstance(info.value, ReconnectNeeded)
    assert "isn't enabled" in str(info.value)


def test_403_permission_denied_asks_to_reconnect():
    with pytest.raises(ReconnectNeeded):
        _client_answering(403, "forbidden").create_calendar("x", "UTC")
