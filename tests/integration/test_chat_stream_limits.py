"""SPEC-014 A33 — a 7th concurrent stream for one member is 429; closing one frees a slot.

Six streams are held open as real ASGI requests (as in SPEC-012's A16); the seventh goes through
the same app and is refused before any body is streamed.
"""

from __future__ import annotations

import asyncio

from mihomes.services.chat import stream as stream_mod
from mihomes.services.chat import streams
from tests.integration.chat_helpers import app_session, estate, sign_in

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)


async def test_per_membership_stream_cap(monkeypatch, _pg_engine, app_engine, estate):
    from mihomes import db
    from mihomes.auth.sessions import SESSION_COOKIE
    from mihomes.services.chat import conversations as conv
    from mihomes.web.app import create_app

    monkeypatch.setattr(stream_mod, "HEARTBEAT_S", 0.1)
    monkeypatch.setattr(stream_mod, "MAX_LIFETIME_S", 30.0)
    e = estate

    def setup():
        with app_session(app_engine, e.account_id) as s:
            conv.get_or_create_estate(s)
            s.commit()
        return sign_in(_pg_engine, e.account_id, e.staff_belle)

    cookie = await asyncio.to_thread(setup)
    prev = db._engine, db._SessionLocal
    db._engine, db._SessionLocal = app_engine, None
    app = create_app()

    def scope():
        return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "http", "path": "/chat/stream",
                "raw_path": b"/chat/stream", "query_string": b"", "root_path": "",
                "headers": [(b"host", b"localhost"),
                            (b"cookie", f"{SESSION_COOKIE}={cookie}".encode())],
                "client": ("127.0.0.1", 1234), "server": ("localhost", 80)}

    def open_stream():
        started = asyncio.Event()
        disconnect = asyncio.Event()
        status = {}

        async def receive():
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            elif message["type"] == "http.response.body":
                started.set()

        task = asyncio.ensure_future(app(scope(), receive, send))
        return task, started, disconnect, status

    held = []
    try:
        for _ in range(6):
            task, started, disconnect, status = open_stream()
            await asyncio.wait_for(started.wait(), 5)
            assert status["code"] == 200
            held.append((task, disconnect))
        assert streams.open_counts()[0] == 6

        task, started, _d, status = open_stream()
        await asyncio.wait_for(task, 5)                    # refused: returns at once
        assert status["code"] == 429

        held_task, held_disconnect = held.pop()            # close one…
        held_disconnect.set()
        held_task.cancel()
        try:
            await held_task
        except (asyncio.CancelledError, Exception):
            pass
        for _ in range(50):
            if streams.open_counts()[0] == 5:
                break
            await asyncio.sleep(0.05)
        assert streams.open_counts()[0] == 5

        task, started, disconnect, status = open_stream()   # …and a slot is free again
        await asyncio.wait_for(started.wait(), 5)
        assert status["code"] == 200
        held.append((task, disconnect))
    finally:
        for task, disconnect in held:
            disconnect.set()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        db._engine, db._SessionLocal = prev
        app_engine.dispose()
    assert streams.open_counts() == (0, 0)
