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
from sqlalchemy import func, select, text
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
    """Google Calendar as the sync sees it: etags, deletions kept as `cancelled`, and an
    incremental change feed keyed by a sync token. `events` holds the live (non-cancelled) ones.
    `phone_*` simulate someone editing the calendar on their phone."""

    def __init__(self):
        self.calendars: dict[str, tuple[str, str]] = {}
        self.events: dict[tuple[str, str], dict] = {}
        self.cancelled: dict[tuple[str, str], dict] = {}
        self.writes = self.deletes = 0
        self.tokens: list[str] = []
        self.refuse = False
        self.seq = 0
        self.changed: dict[tuple[str, str], int] = {}
        self.expire_tokens = False

    def __call__(self, refresh_token):  # the client factory
        self.tokens.append(refresh_token)
        if self.refuse:
            raise ReconnectNeeded("revoked")
        return self

    def _touch(self, key, ev):
        self.seq += 1
        ev["etag"] = f"etag-{self.seq}"
        self.changed[key] = self.seq
        return ev["etag"]

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
        ev = {**body, "id": eid, "status": "confirmed"}
        self.cancelled.pop((cid, eid), None)
        self.events[(cid, eid)] = ev
        self.writes += 1
        return self._touch((cid, eid), ev)

    def delete_event(self, cid, eid):
        ev = self.events.pop((cid, eid), None)
        if ev is not None:
            ev = {**ev, "status": "cancelled"}
            self.cancelled[(cid, eid)] = ev
            self._touch((cid, eid), ev)
        self.deletes += 1

    def list_changes(self, cid, sync_token):
        from mihomes.services.personal_calendar.google_client import SyncTokenExpired

        if cid not in self.calendars:
            raise CalendarGone(cid)
        if sync_token and self.expire_tokens:
            self.expire_tokens = False
            raise SyncTokenExpired(cid)
        since = int(sync_token) if sync_token else 0
        out = [dict(ev) for key, ev in {**self.events, **self.cancelled}.items()
               if key[0] == cid and self.changed.get(key, 0) > since]
        return out, str(self.seq)

    # ── someone on their phone ──
    def phone_add(self, cid, summary, start, *, location=None, description=None, **extra):
        eid = f"phone{self.seq + 1}"
        ev = {"id": eid, "status": "confirmed", "summary": summary, "start": start,
              "end": start, **extra}
        if location:
            ev["location"] = location
        if description:
            ev["description"] = description
        self.events[(cid, eid)] = ev
        self._touch((cid, eid), ev)
        return eid

    def phone_edit(self, cid, eid, **fields):
        ev = self.events[(cid, eid)]
        ev.update(fields)
        self._touch((cid, eid), ev)

    def phone_delete(self, cid, eid):
        self.delete_event(cid, eid)
        self.deletes -= 1

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
    page = world.owner.get("/settings/connections").text
    assert 'action="/settings/calendar/google/connect"' in page
    _connect(world, world.owner.membership_id)
    page = world.owner.get("/settings/connections").text
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


def test_an_idle_sync_writes_nothing_to_google(world):
    """Every save in the estate queues a sync; one with nothing to change must only read
    (the pull, since 2026-11-11), never write."""
    fake = FakeGoogle()
    cid = _connect(world, world.owner.membership_id)
    _sync(world, cid, fake)
    writes, deletes = fake.writes, fake.deletes
    _sync(world, cid, fake)
    assert (fake.writes, fake.deletes) == (writes, deletes)


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


# ── Calendar page entry point (2026-11-10) ───────────────────────────────────

def test_calendar_page_offers_google_sync_when_not_connected(world):
    page = world.owner.get("/calendar/").text
    assert 'data-testid="gcal-status"' in page
    assert "Sync to Google Calendar" in page
    assert 'action="/settings/calendar/google/connect"' in page


def test_calendar_page_shows_synced_status_and_sync_now(world):
    _connect(world, world.owner.membership_id)
    page = world.owner.get("/calendar/").text
    assert "Synced to Google" in page and 'action="/settings/calendar/sync"' in page
    assert "Sync to Google Calendar" not in page


def test_calendar_page_asks_to_reconnect_when_access_lapsed(world):
    cid = _connect(world, world.owner.membership_id)
    world.conn.execute(text("update calendar_connections set status='needs_reconnect' "
                            "where id = :i"), {"i": cid})
    assert "Reconnect Google Calendar" in world.owner.get("/calendar/").text


def test_calendar_page_hides_google_when_not_configured(world, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID")
    assert 'data-testid="gcal-status"' not in world.owner.get("/calendar/").text


# ── Google → MiHomes (2026-11-11) ────────────────────────────────────────────

def _appts(w):
    from mihomes.models.appointment import Appointment

    with account_context(w.account), w.factory() as s:
        return {a.title: (a.property_id, a.date, a.start_time, a.notes)
                for a in s.execute(select(Appointment)).scalars()}


def _cal(fake):
    return next(iter(fake.calendars))


def _synced_owner(w, fake):
    cid = _connect(w, w.owner.membership_id)
    _sync(w, cid, fake)  # first run: creates the calendar, pushes, baselines the pull
    return cid


def _day(n=7):
    return {"date": (date.today() + timedelta(days=n)).isoformat()}


def _key(fake, summary):
    return next(k for k, v in fake.events.items() if v["summary"] == summary)


def test_an_event_added_on_a_phone_becomes_an_appointment(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    eid = fake.phone_add(_cal(fake), "Pool cleaning at the Guest Cottage", _day(),
                         description="bring chlorine")
    writes = fake.writes
    r = _sync(world, cid, fake)
    assert r.pull.imported == 1
    prop, day, at, notes = _appts(world)["Pool cleaning at the Guest Cottage"]
    assert prop == world.props["cottage"] and day == date.today() + timedelta(days=7)
    assert at is None and notes == "bring chlorine"
    assert fake.writes == writes, "the imported event was pushed back as a second copy"
    with account_context(world.account), world.factory() as s:
        link = s.execute(select(CalendarEventLink).where(
            CalendarEventLink.google_event_id == eid)).scalar_one()
        assert link.origin == "google"


def test_a_timed_phone_event_lands_in_the_estate_time_zone(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    d = date.today() + timedelta(days=3)
    fake.phone_add(_cal(fake), "Locksmith", {"dateTime": f"{d.isoformat()}T15:30:00Z"},
                   location="1 Belle Rd")
    _sync(world, cid, fake)
    prop, day, at, _ = _appts(world)["Locksmith"]
    assert prop == world.props["main"]
    assert at in (time(10, 30), time(11, 30)), at  # New York, EST or EDT
    assert day == d


def test_no_property_named_uses_the_default_else_is_skipped_with_a_note(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    fake.phone_add(_cal(fake), "Vague errand", _day())
    _sync(world, cid, fake)
    assert "Vague errand" not in _appts(world)
    note = world.conn.execute(text("select import_note from calendar_connections where id=:i"),
                              {"i": cid}).scalar()
    assert "default import property" in note

    world.conn.execute(text("update calendar_connections set default_property_id=:p, "
                            "sync_token=null where id=:i"),
                       {"p": world.props["main"], "i": cid})
    _sync(world, cid, fake)
    assert _appts(world)["Vague errand"][0] == world.props["main"]


def test_staff_need_the_grant_and_stay_inside_their_properties(world):
    fake = FakeGoogle()
    staff = world.make("staff", scoped_to=[world.props["cottage"]])
    cid = _connect(world, staff.membership_id)
    _sync(world, cid, fake)
    fake.phone_add(_cal(fake), "Guest Cottage gutters", _day())
    _sync(world, cid, fake)
    assert "Guest Cottage gutters" not in _appts(world), "imported without the grant"

    world.conn.execute(text("update memberships set can_import_calendar=true where id=:m"),
                       {"m": staff.membership_id})
    world.conn.execute(text("update calendar_connections set sync_token=null where id=:i"),
                       {"i": cid})
    fake.phone_add(_cal(fake), "Main House alarm check", _day())
    _sync(world, cid, fake)
    got = _appts(world)
    assert "Guest Cottage gutters" in got
    assert "Main House alarm check" not in got, "staff imported into a property they can't see"


def test_our_own_writes_coming_back_change_nothing(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    before = _appts(world)
    r = _sync(world, cid, fake)
    assert r.pull.updated == r.pull.imported == r.pull.deleted == 0
    assert _appts(world) == before


def test_the_first_pull_is_a_baseline(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    hvac = _key(fake, "HVAC service")
    world.conn.execute(text("update calendar_connections set sync_token=null where id=:i"),
                       {"i": cid})
    fake.phone_edit(*hvac, summary="HVAC moved by phone")
    _sync(world, cid, fake)
    assert "HVAC service" in _appts(world), "the baseline applied an edit"
    fake.phone_edit(*hvac, summary="HVAC moved again")
    _sync(world, cid, fake)
    assert "HVAC moved again" in _appts(world)


def test_a_permitted_phone_edit_flows_back(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    hvac = _key(fake, "HVAC service")
    new_day = (date.today() + timedelta(days=9)).isoformat()
    fake.phone_edit(*hvac, summary="HVAC service (rescheduled)",
                    start={"dateTime": f"{new_day}T14:00:00-05:00"})
    _sync(world, cid, fake)
    _, day, at, _ = _appts(world)["HVAC service (rescheduled)"]
    assert day.isoformat() == new_day and at is not None


def test_when_both_sides_changed_mihomes_wins(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    hvac = _key(fake, "HVAC service")
    fake.phone_edit(*hvac, summary="phone version")
    world.conn.execute(text("update appointments set title='MiHomes version' "
                            "where title='HVAC service'"))
    _sync(world, cid, fake)
    assert "MiHomes version" in _appts(world)
    assert fake.events[hvac]["summary"] == "MiHomes version"


def test_without_permission_google_edits_and_deletes_are_put_back(world):
    fake = FakeGoogle()
    staff = world.make("staff", scoped_to=[world.props["main"]])
    cid = _connect(world, staff.membership_id)
    _sync(world, cid, fake)
    hvac = _key(fake, "HVAC service")
    fake.phone_edit(*hvac, summary="staff renamed it")
    gutters = _key(fake, "Task: Clean gutters")
    fake.phone_delete(*gutters)
    _sync(world, cid, fake)
    assert "HVAC service" in _appts(world)
    assert fake.events[hvac]["summary"] == "HVAC service", "an unpermitted edit stuck"
    assert gutters in fake.events, "an unpermitted delete stuck"


def test_tasks_stay_mihomes_controlled_even_for_owners(world):
    from mihomes.models.task import Task

    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    shed = _key(fake, "Task: Paint shed")
    fake.phone_delete(*shed)
    _sync(world, cid, fake)
    assert shed in fake.events, "a task deleted from a phone was not put back"
    with account_context(world.account), world.factory() as s:
        assert s.execute(select(Task).where(Task.title == "Paint shed")).scalar_one()


def test_a_permitted_phone_delete_deletes_the_appointment_for_everyone(world):
    fake, other_fake = FakeGoogle(), FakeGoogle()
    cid = _synced_owner(world, fake)
    admin = world.make("admin")
    other = _connect(world, admin.membership_id)
    _sync(world, other, other_fake)
    assert "HVAC service" in other_fake.bodies()
    fake.phone_delete(*_key(fake, "HVAC service"))
    assert _sync(world, cid, fake).pull.deleted == 1
    assert "HVAC service" not in _appts(world)
    _sync(world, other, other_fake)
    assert "HVAC service" not in other_fake.bodies()


def test_deleting_a_serviced_appointment_from_a_phone_is_refused(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    world.conn.execute(text("update appointments set completed=true where title='HVAC service'"))
    hvac = _key(fake, "HVAC service")
    fake.phone_delete(*hvac)
    r = _sync(world, cid, fake)
    assert r.pull.refused_deletes == 1 and "HVAC service" in _appts(world)
    assert hvac in fake.events, "the refused event was not put back"


def test_a_mass_delete_is_held(world):
    from mihomes.services.appointment import create_appointment

    fake = FakeGoogle()
    with account_context(world.account), world.factory() as s:
        for i in range(11):
            create_appointment(s, f"Visit {i}", str(world.props["main"]), world.soon)
    cid = _synced_owner(world, fake)
    for key in [k for k, v in fake.events.items() if v["summary"].startswith("Visit")]:
        fake.phone_delete(*key)
    r = _sync(world, cid, fake)
    assert r.pull.deleted == 0 and r.pull.held_deletes == 11
    assert sum(t.startswith("Visit") for t in _appts(world)) == 11
    assert sum(v["summary"].startswith("Visit") for v in fake.events.values()) == 11


def test_mihomes_own_deletions_never_delete_appointments(world):
    from mihomes.models.task import Task, TaskStatus

    fake = FakeGoogle()
    staff = world.make("staff", scoped_to=[world.props["main"], world.props["cottage"]])
    world.conn.execute(text("update memberships set can_import_calendar=true where id=:m"),
                       {"m": staff.membership_id})
    cid = _connect(world, staff.membership_id)
    _sync(world, cid, fake)
    with account_context(world.account), world.factory() as s:
        task = s.execute(select(Task).where(Task.title == "Paint shed")).scalar_one()
        task.status = TaskStatus.COMPLETED
    world.conn.execute(text("delete from membership_property_scopes where membership_id=:m "
                            "and property_id=:p"),
                       {"m": staff.membership_id, "p": world.props["main"]})
    before = _appts(world)
    _sync(world, cid, fake)  # push removes the events
    r = _sync(world, cid, fake)  # pull sees those cancellations come back
    assert r.pull.deleted == 0 and _appts(world) == before


def test_repeating_google_events_are_skipped_with_a_note(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    fake.phone_add(_cal(fake), "Weekly Main House sweep", _day(),
                   recurrence=["RRULE:FREQ=WEEKLY"])
    r = _sync(world, cid, fake)
    assert r.pull.skipped_recurring == 1 and "Weekly Main House sweep" not in _appts(world)


def test_an_expired_sync_token_resyncs_without_duplicates(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    fake.phone_add(_cal(fake), "Main House window wash", _day())
    _sync(world, cid, fake)
    fake.expire_tokens = True
    _sync(world, cid, fake)
    assert "Main House window wash" in _appts(world)
    from mihomes.models.appointment import Appointment

    with account_context(world.account), world.factory() as s:
        n = s.execute(select(func.count()).select_from(Appointment).where(
            Appointment.title == "Main House window wash")).scalar()
    assert n == 1


def test_a_crash_before_the_cursor_is_saved_imports_nothing_twice(world):
    from mihomes.models.appointment import Appointment

    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    token = world.conn.execute(text("select sync_token from calendar_connections where id=:i"),
                               {"i": cid}).scalar()
    fake.phone_add(_cal(fake), "Main House chimney", _day())
    _sync(world, cid, fake)
    world.conn.execute(text("update calendar_connections set sync_token=:t where id=:i"),
                       {"t": token, "i": cid})  # as if the run died before saving the cursor
    _sync(world, cid, fake)
    with account_context(world.account), world.factory() as s:
        n = s.execute(select(func.count()).select_from(Appointment).where(
            Appointment.title == "Main House chimney")).scalar()
    assert n == 1


def test_editing_an_imported_appointment_in_mihomes_updates_the_same_google_event(world):
    fake = FakeGoogle()
    cid = _synced_owner(world, fake)
    eid = fake.phone_add(_cal(fake), "Main House piano tuning", _day())
    _sync(world, cid, fake)
    world.conn.execute(text("update appointments set title='Piano tuning (confirmed)' "
                            "where title='Main House piano tuning'"))
    _sync(world, cid, fake)
    assert fake.events[(_cal(fake), eid)]["summary"] == "Piano tuning (confirmed)"
    assert sum("iano" in v["summary"] for v in fake.events.values()) == 1, "a second copy"


# ── Settings for the import (2026-11-11) ─────────────────────────────────────

def test_an_owner_grants_and_withdraws_a_staff_members_import(world):
    staff = world.make("staff", scoped_to=[world.props["cottage"]])
    path = f"/settings/members/{staff.membership_id}/calendar-import"
    r = world.owner.post(path, data={"enabled": "on"}, follow_redirects=False)
    assert "calendar_import_on" in r.headers["location"]
    flag = "select can_import_calendar from memberships where id=:m"
    assert world.conn.execute(text(flag), {"m": staff.membership_id}).scalar() is True
    assert "Can add appointments from Google Calendar" in world.owner.get("/settings").text
    world.owner.post(path, data={}, follow_redirects=False)
    assert world.conn.execute(text(flag), {"m": staff.membership_id}).scalar() is False


def test_staff_cannot_grant_themselves_the_import(world):
    staff = world.make("staff", scoped_to=[world.props["cottage"]])
    r = staff.post(f"/settings/members/{staff.membership_id}/calendar-import",
                   data={"enabled": "on"}, follow_redirects=False)
    assert r.status_code == 403
    assert world.conn.execute(text("select can_import_calendar from memberships where id=:m"),
                              {"m": staff.membership_id}).scalar() is False


def test_the_card_explains_which_way_updates_go(world):
    _connect(world, world.owner.membership_id)
    assert 'action="/settings/calendar/default-property"' in \
        world.owner.get("/settings/connections").text
    staff = world.make("staff", scoped_to=[world.props["cottage"]])
    _connect(world, staff.membership_id)
    page = staff.get("/settings/connections").text
    assert "Updates go one way" in page
    assert 'action="/settings/calendar/default-property"' not in page


def test_the_default_property_must_be_one_you_can_see(world):
    staff = world.make("staff", scoped_to=[world.props["cottage"]])
    staff.cookies.set(CSRF_COOKIE, CSRF)
    cid = _connect(world, staff.membership_id)
    bad = staff.post("/settings/calendar/default-property",
                     data={"property_id": str(world.props["main"]), "csrf_token": CSRF},
                     follow_redirects=False)
    assert bad.status_code == 400
    ok = staff.post("/settings/calendar/default-property",
                    data={"property_id": str(world.props["cottage"]), "csrf_token": CSRF},
                    follow_redirects=False)
    assert ok.status_code == 303
    assert world.conn.execute(text("select default_property_id from calendar_connections "
                                   "where id=:i"), {"i": cid}).scalar() == world.props["cottage"]
