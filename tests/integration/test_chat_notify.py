"""SPEC-012 A29, A30 — LISTEN/NOTIFY fan-out and the event-pruning job.

A29 points `REALTIME_DATABASE_URL` at the test database itself (a direct connection, as D17
requires), so it runs everywhere the suite does rather than skipping.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from typer.testing import CliRunner

from mihomes.models.chat_native import ChatConversation
from mihomes.models.membership import Membership
from mihomes.services.chat import conversations as conv
from mihomes.services.chat import posts
from mihomes.services.chat.bus import ChatBus, start_listener
from tests.integration.chat_helpers import app_session, estate

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)


async def test_notify_delivers_once_on_commit(monkeypatch, app_engine, estate):
    """A29 — one commit → exactly one delivery (NOTIFY only, no second in-process publish);
    a rollback → none."""
    dsn = os.environ["TEST_DATABASE_URL"]
    monkeypatch.setenv("REALTIME_DATABASE_URL", dsn)
    # The in-process bus must stay silent in NOTIFY mode, or a single-process app double-delivers.
    local = ChatBus()
    monkeypatch.setattr("mihomes.services.chat.bus.BUS", local)
    remote = ChatBus()   # what the listener re-publishes onto
    e = estate

    received: list[dict] = []
    local_received: list[dict] = []
    sub = remote.subscribe(e.account_id)
    local_sub = local.subscribe(e.account_id)
    listener = asyncio.create_task(start_listener(dsn, bus=remote))
    try:
        await asyncio.sleep(0.5)   # LISTEN established

        def work():
            with app_session(app_engine, e.account_id) as s:
                general = conv.get_or_create_estate(s)
                s.commit()
                posts.create_post(s, s.get(ChatConversation, general.id),
                                  author=s.get(Membership, e.owner), body="rolled back")
                s.rollback()
                p = posts.create_post(s, s.get(ChatConversation, general.id),
                                      author=s.get(Membership, e.owner), body="committed")
                s.commit()
                return str(p.id)
        post_id = await asyncio.to_thread(work)

        loop = asyncio.get_running_loop()
        end = loop.time() + 5
        while loop.time() < end and not any(x.get("p") == post_id for x in received):
            try:
                received.append(await asyncio.wait_for(sub.queue.get(), 0.2))
            except asyncio.TimeoutError:
                pass
        await asyncio.sleep(0.3)
        while not sub.queue.empty():
            received.append(sub.queue.get_nowait())
        while not local_sub.queue.empty():
            local_received.append(local_sub.queue.get_nowait())
    finally:
        listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass
        remote.unsubscribe(sub)
        local.unsubscribe(local_sub)

    created = [x for x in received if x["type"] == "post.created"]
    assert [x["p"] for x in created] == [post_id]       # once; the rollback never notified
    assert "a" not in created[0]                          # the account is routing, not payload
    assert local_received == []                           # no second, in-process delivery


def test_prune_events(_pg_engine, app_engine, estate, account_b):
    """A30 — only rows older than 7 days go, in every account, on the app role."""
    from mihomes import db
    from mihomes.cli.jobs import app as jobs_app
    from tests.integration.chat_helpers import cleanup_estate, seed_estate

    e = estate
    other = seed_estate(_pg_engine, account_b)
    try:
        for est in (e, other):
            with app_session(app_engine, est.account_id) as s:
                general = conv.get_or_create_estate(s)
                s.commit()
                for body in ("old", "new"):
                    posts.create_post(s, s.get(ChatConversation, general.id),
                                      author=s.get(Membership, est.owner), body=body)
                s.commit()
        with _pg_engine.begin() as conn:
            conn.execute(text("UPDATE chat_events SET created_at = now() - interval '8 days' "
                              "WHERE post_id IN (SELECT id FROM chat_posts WHERE body = 'old') "
                              "AND account_id = ANY(:a)"),
                         {"a": [e.account_id, other.account_id]})

        prev = db._engine, db._SessionLocal
        db._engine, db._SessionLocal = app_engine, None
        try:
            result = CliRunner().invoke(jobs_app, ["chat-prune-events"])
        finally:
            db._engine, db._SessionLocal = prev
        assert result.exit_code == 0, result.output
        assert "0 account(s) failed" in result.output

        with _pg_engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT ev.account_id, p.body FROM chat_events ev "
                "JOIN chat_posts p ON p.id = ev.post_id WHERE ev.account_id = ANY(:a)"),
                {"a": [e.account_id, other.account_id]}).all()
        assert sorted((str(a), b) for a, b in rows) == sorted(
            [(str(e.account_id), "new"), (str(other.account_id), "new")])
        with _pg_engine.connect() as conn:   # the posts themselves are untouched
            assert conn.execute(text("SELECT count(*) FROM chat_posts WHERE account_id = ANY(:a)"),
                                {"a": [e.account_id, other.account_id]}).scalar() == 4
    finally:
        app_engine.dispose()
        cleanup_estate(_pg_engine, other)
