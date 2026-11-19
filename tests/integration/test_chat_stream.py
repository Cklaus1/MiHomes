"""SPEC-012 A16–A19 — the event stream, on the app role.

A17–A19 drive `stream_events` directly with `open_session` bound to the non-superuser engine, and
assert **positive** delivery as well as absence: a stream whose visible set came back empty (an
unbound RLS GUC) would "never deliver an invisible event" by delivering nothing.

A16 drives the real ASGI app with its real `get_db` and both middlewares, and reads the pool from
inside `send` the moment the first heartbeat chunk leaves — "while the stream is open",
deterministically, with no sleeps. TestClient cannot do this: it reads the whole body first.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mihomes.models.chat_native import ChatConversation, ChatEvent, ChatPost
from mihomes.models.membership import Membership
from mihomes.services.chat import conversations as conv
from mihomes.services.chat import posts, reactions
from mihomes.services.chat import stream as stream_mod
from mihomes.services.chat.bus import ChatBus
from tests.integration.chat_helpers import app_session, estate, sign_in

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(stream_mod, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(stream_mod, "REFRESH_S", 0.5)
    monkeypatch.setattr(stream_mod, "MAX_LIFETIME_S", 5.0)


def _opener(app_engine):
    @contextmanager
    def open_session():
        with Session(app_engine) as s:
            yield s
    return open_session


def _user(_pg_engine, membership_id):
    with _pg_engine.connect() as c:
        return c.execute(text("SELECT user_id FROM memberships WHERE id = :m"),
                         {"m": membership_id}).scalar_one()


def _groups(app_engine, e):
    with app_session(app_engine, e.account_id) as s:
        ids = {"general": conv.get_or_create_estate(s).id,
               "belle": conv.get_or_create_property(s, e.belle).id,
               "blue": conv.get_or_create_property(s, e.blue).id}
        s.commit()
        return ids


def _post(app_engine, e, conversation_id, author_id, body="hi"):
    """Commit a post — in a worker thread, as a route would, so after_commit publishes off-loop."""
    with app_session(app_engine, e.account_id) as s:
        p = posts.create_post(s, s.get(ChatConversation, conversation_id),
                              author=s.get(Membership, author_id), body=body)
        s.commit()
        return p.id


class Reader:
    """Pulls frames off a running `stream_events` generator in the background."""

    def __init__(self, gen):
        self.gen = gen
        self.frames: list[str] = []
        self.task = asyncio.ensure_future(self._run())

    async def _run(self):
        async for f in self.gen:
            self.frames.append(f)

    def events(self) -> list[dict]:
        out = []
        for f in self.frames:
            if f.startswith(":"):
                continue
            fields = dict(line.split(": ", 1) for line in f.strip().split("\n"))
            out.append({"id": fields.get("id"), "event": fields["event"],
                        **json.loads(fields["data"])})
        return out

    async def wait_for(self, pred, timeout=5.0):
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if pred(self.events()) or self.task.done():
                return
            await asyncio.sleep(0.02)

    async def close(self):
        self.task.cancel()
        try:
            await self.task
        except (asyncio.CancelledError, StopAsyncIteration):
            pass


def _stream(app_engine, _pg_engine, e, membership_id, bus, *, cookie=None, after=None):
    return stream_mod.stream_events(
        account_id=e.account_id, user_id=_user(_pg_engine, membership_id),
        membership_id=membership_id, session_cookie=cookie, after_cursor=after,
        open_session=_opener(app_engine), bus=bus)


async def test_publish_after_commit_only(fast, monkeypatch, _pg_engine, app_engine, estate,
                                         account_b):
    """A17 — a commit in A reaches A's subscriber, never B's; a rollback publishes nothing."""
    from tests.integration.chat_helpers import cleanup_estate, seed_estate

    bus = ChatBus()
    monkeypatch.setattr("mihomes.services.chat.bus.BUS", bus)
    e = estate
    other = seed_estate(_pg_engine, account_b)
    try:
        c = await asyncio.to_thread(_groups, app_engine, e)
        await asyncio.to_thread(_groups, app_engine, other)
        mine = Reader(_stream(app_engine, _pg_engine, e, e.owner, bus))
        theirs = Reader(_stream(app_engine, _pg_engine, other, other.owner, bus))
        await asyncio.sleep(0.3)   # both subscribed

        def rolled_back():
            with app_session(app_engine, e.account_id) as s:
                posts.create_post(s, s.get(ChatConversation, c["general"]),
                                  author=s.get(Membership, e.staff_belle), body="never")
                s.rollback()
        await asyncio.to_thread(rolled_back)
        pid = await asyncio.to_thread(_post, app_engine, e, c["general"], e.staff_belle, "hello")

        await mine.wait_for(lambda ev: any(x.get("p") == str(pid) for x in ev))
        await asyncio.sleep(0.3)
        created = [x for x in mine.events() if x["event"] == "post.created"]
        assert [x["p"] for x in created] == [str(pid)]   # exactly one; the rollback never published
        assert created[0]["id"] is not None
        assert not [x for x in theirs.events() if x["event"] != "revoked"]
        await mine.close()
        await theirs.close()
    finally:
        cleanup_estate(_pg_engine, other)


async def test_stream_filters_and_revokes(fast, monkeypatch, _pg_engine, app_engine, estate):
    """A18 — Belle staff get Belle and General, never Blue; revocation ends the stream."""
    bus = ChatBus()
    monkeypatch.setattr("mihomes.services.chat.bus.BUS", bus)
    e = estate
    c = await asyncio.to_thread(_groups, app_engine, e)
    cookie = await asyncio.to_thread(sign_in, _pg_engine, e.account_id, e.staff_belle)
    r = Reader(_stream(app_engine, _pg_engine, e, e.staff_belle, bus, cookie=cookie))
    await asyncio.sleep(0.3)

    blue = await asyncio.to_thread(_post, app_engine, e, c["blue"], e.owner, "blue secret")
    belle = await asyncio.to_thread(_post, app_engine, e, c["belle"], e.owner, "belle news")
    general = await asyncio.to_thread(_post, app_engine, e, c["general"], e.owner, "all hands")
    # A typing frame for Blue, straight onto the bus, must be filtered too.
    bus.publish(e.account_id, {"type": "typing", "c": str(c["blue"]), "m": str(e.owner),
                               "name": "Owner"})

    await r.wait_for(lambda ev: any(x.get("p") == str(general) for x in ev))
    got = {x.get("p") for x in r.events()}
    assert {str(belle), str(general)} <= got           # positive: the visible set is not empty
    assert str(blue) not in got
    assert all(x.get("c") != str(c["blue"]) for x in r.events())

    with _pg_engine.begin() as conn:
        conn.execute(text("UPDATE memberships SET status = 'revoked' WHERE id = :m"),
                     {"m": e.staff_belle})
    await r.wait_for(lambda ev: any(x["event"] == "revoked" for x in ev), timeout=3)
    assert r.events()[-1]["event"] == "revoked"
    await asyncio.wait_for(r.task, 2)   # the generator returned
    assert bus.subscriber_count() == 0


async def test_catchup_overlap(fast, monkeypatch, _pg_engine, app_engine, estate):
    """A19 — replay after `after`, including a lower cursor committed late inside the overlap."""
    bus = ChatBus()
    monkeypatch.setattr("mihomes.services.chat.bus.BUS", bus)
    e = estate
    c = await asyncio.to_thread(_groups, app_engine, e)
    first = await asyncio.to_thread(_post, app_engine, e, c["general"], e.owner, "one")

    # A slow transaction takes a cursor now and commits after a later event: the classic gap.
    slow = Session(app_engine)
    from mihomes.tenancy.connection import bind_account_guc
    from mihomes.tenancy.context import account_context

    with account_context(e.account_id):
        bind_account_guc(slow, e.account_id)
        posts.create_post(slow, slow.get(ChatConversation, c["belle"]),
                          author=slow.get(Membership, e.owner), body="slow")
        slow.flush()
    fast_post = await asyncio.to_thread(_post, app_engine, e, c["general"], e.owner, "two")

    with app_session(app_engine, e.account_id) as s:
        fast_cursor = s.execute(text("SELECT cursor FROM chat_events WHERE post_id = :p "
                                     "AND type = 'post.created'"), {"p": fast_post}).scalar_one()
    with account_context(e.account_id):
        slow.commit()   # its cursor is LOWER than fast_cursor, and it commits after
    slow.close()

    # The client last saw fast_cursor; the slow event (lower cursor) must still be replayed.
    r = Reader(_stream(app_engine, _pg_engine, e, e.owner, bus, after=fast_cursor))
    await r.wait_for(lambda ev: any(x.get("p") and x["event"] == "post.created" for x in ev), 3)
    await asyncio.sleep(0.3)
    replayed = r.events()
    bodies_seen = {x.get("p") for x in replayed}
    with app_session(app_engine, e.account_id) as s:
        slow_id = s.execute(text("SELECT id FROM chat_posts WHERE body = 'slow'")).scalar_one()
    assert str(slow_id) in bodies_seen
    ids = [x["id"] for x in replayed if x["id"] is not None]
    assert len(ids) == len(set(ids))   # within one stream, no duplicates
    assert str(first) in bodies_seen    # inside the overlap window: re-sent; the client dedupes
    await r.close()


async def test_pruned_anchor_resyncs(fast, monkeypatch, _pg_engine, app_engine, estate):
    """A cursor that no longer exists with newer events after it cannot be replayed: resync."""
    bus = ChatBus()
    e = estate
    c = await asyncio.to_thread(_groups, app_engine, e)
    await asyncio.to_thread(_post, app_engine, e, c["general"], e.owner, "x")
    r = Reader(_stream(app_engine, _pg_engine, e, e.owner, bus, after=1))   # not ours / pruned
    await r.wait_for(lambda ev: bool(ev), 3)
    assert [x["event"] for x in r.events()] == ["resync"]


async def test_stream_holds_no_connection_and_is_not_buffered(monkeypatch, _pg_engine,
                                                              app_engine, estate):
    """A16 — through create_app() with the real get_db and both middlewares."""
    from mihomes import db
    from mihomes.auth.sessions import SESSION_COOKIE
    from mihomes.web.app import create_app

    monkeypatch.setattr(stream_mod, "HEARTBEAT_S", 0.1)
    monkeypatch.setattr(stream_mod, "MAX_LIFETIME_S", 1.0)
    e = estate
    await asyncio.to_thread(_groups, app_engine, e)
    cookie = await asyncio.to_thread(sign_in, _pg_engine, e.account_id, e.staff_belle)

    prev = db._engine, db._SessionLocal
    db._engine, db._SessionLocal = app_engine, None
    try:
        app = create_app()
        pool = app_engine.pool
        baseline = pool.checkedout()
        peak = {"during_gate": 0}
        observed: list[tuple[bytes, int]] = []

        # Sample the pool while the request gate runs, to prove the measurement can see a checkout.
        original_get_session = db.get_session

        @contextmanager
        def watching_get_session(*a, **kw):
            with original_get_session(*a, **kw) as s:
                s.execute(text("SELECT 1"))
                peak["during_gate"] = max(peak["during_gate"], pool.checkedout())
                yield s
        monkeypatch.setattr(db, "get_session", watching_get_session)
        monkeypatch.setattr("mihomes.web.deps.get_session", watching_get_session, raising=False)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
            "scheme": "http", "path": "/chat/stream", "raw_path": b"/chat/stream",
            "query_string": b"", "root_path": "",
            "headers": [(b"host", b"localhost"),
                        (b"cookie", f"{SESSION_COOKIE}={cookie}".encode())],
            "client": ("127.0.0.1", 1234), "server": ("localhost", 80),
        }
        status = {}
        first_chunk = asyncio.Event()

        async def receive():
            await asyncio.sleep(10)   # the client never sends a body or disconnects early
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                status["headers"] = dict(message["headers"])
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if chunk:
                    observed.append((chunk, pool.checkedout()))
                    first_chunk.set()

        run = asyncio.ensure_future(app(scope, receive, send))
        await asyncio.wait_for(first_chunk.wait(), 5)
        assert status["code"] == 200
        assert status["headers"][b"content-type"].startswith(b"text/event-stream")
        chunk, checked_out = observed[0]
        assert chunk.startswith(b": hb")              # arrived as its own chunk: not buffered
        assert checked_out == baseline, (checked_out, baseline)   # the gate's connection is back
        assert peak["during_gate"] > baseline         # ...and the measurement could see one
        await asyncio.wait_for(run, 5)                 # MAX_LIFETIME_S ends it
        assert len(observed) >= 2                      # several heartbeats, each delivered alone
        assert all(c == baseline for _chunk, c in observed)
    finally:
        db._engine, db._SessionLocal = prev
        app_engine.dispose()


def test_event_rows_carry_their_account(app_engine, estate):
    """The cursor is per row and events never cross accounts — a sanity check on emit."""
    e = estate
    c = _groups(app_engine, e)
    pid = _post(app_engine, e, c["general"], e.owner)
    with app_session(app_engine, e.account_id) as s:
        reactions.toggle_reaction(s, s.get(ChatPost, pid),
                                  actor=s.get(Membership, e.staff_belle), emoji="👍")
        s.commit()
        rows = s.query(ChatEvent).filter(ChatEvent.post_id == pid).all()
    assert {r.type for r in rows} == {"post.created", "reaction.changed"}
    assert all(r.account_id == e.account_id for r in rows)
    assert len({r.cursor for r in rows}) == 2
