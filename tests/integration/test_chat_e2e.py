"""SPEC-012 A31 — the exit criterion, end to end on the app role.

Owner posts in General over HTTP → the staff stream receives `post.created` → staff fetch the
partial (visibility re-checked) → staff react over HTTP → the owner stream receives
`reaction.changed`. Every request goes through the real app and real `get_db` on the
non-superuser role; the streams run `stream_events` on the same role.
"""

from __future__ import annotations

import asyncio
import re

from mihomes.services.chat import stream as stream_mod
from mihomes.services.chat.bus import ChatBus
from tests.integration.chat_helpers import app_client, estate
from tests.integration.test_chat_stream import Reader, _stream

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)


async def test_exit_criterion(monkeypatch, _pg_engine, app_engine, app_client, estate):
    monkeypatch.setattr(stream_mod, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(stream_mod, "MAX_LIFETIME_S", 10.0)
    bus = ChatBus()
    monkeypatch.setattr("mihomes.services.chat.bus.BUS", bus)
    e = estate

    owner = await asyncio.to_thread(app_client, e.account_id, e.owner)
    staff = await asyncio.to_thread(app_client, e.account_id, e.staff_belle)
    assert (await asyncio.to_thread(owner.get, "/chat/")).status_code == 200
    page = await asyncio.to_thread(staff.get, "/chat/")
    general = re.search(r'href="/chat/c/([0-9a-f-]{36})"', page.text).group(1)

    staff_stream = Reader(_stream(app_engine, _pg_engine, e, e.staff_belle, bus))
    owner_stream = Reader(_stream(app_engine, _pg_engine, e, e.owner, bus))
    await asyncio.sleep(0.4)

    r = await asyncio.to_thread(owner.post, f"/chat/c/{general}/posts",
                                data={"body": "Gate code changes Friday"})
    assert r.status_code == 200
    post_id = re.search(r'id="post-([0-9a-f-]{36})"', r.text).group(1)

    await staff_stream.wait_for(lambda ev: any(x["event"] == "post.created" and x["p"] == post_id
                                               for x in ev))
    assert any(x["event"] == "post.created" and x["p"] == post_id for x in staff_stream.events())

    partial = await asyncio.to_thread(staff.get, f"/chat/posts/{post_id}")
    assert partial.status_code == 200 and "Gate code changes Friday" in partial.text

    react = await asyncio.to_thread(staff.post, f"/chat/posts/{post_id}/react",
                                    data={"emoji": "✅"})
    assert react.status_code == 200 and "✅" in react.text

    await owner_stream.wait_for(lambda ev: any(x["event"] == "reaction.changed" for x in ev))
    reaction = [x for x in owner_stream.events() if x["event"] == "reaction.changed"]
    assert reaction and reaction[0]["p"] == post_id and reaction[0]["m"] == str(e.staff_belle)

    await staff_stream.close()
    await owner_stream.close()
    app_engine.dispose()
