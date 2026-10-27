"""Conversations page — rendering, empty state, and escaping of text anyone in the group can send."""

from __future__ import annotations

from datetime import datetime, timezone

from mihomes.models.chat_message import ChatMessage


def _add(client, **kw):
    row = dict(channel="telegram", chat_id="-100888", message_id=kw.pop("mid", "-100888:1"),
               direction="in", sender_name="Ana", text="Pool pump is loud", has_media=False,
               sent_at=datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc))
    row.update(kw)
    with client._SessionLocal() as s:
        s.add(ChatMessage(**row))
        s.commit()


def test_empty_state_explains_how_to_link(client):
    r = client.get("/conversations/")
    assert r.status_code == 200
    assert "No conversations yet" in r.text
    assert "/linkchat" in r.text


def test_a_conversation_renders_with_the_bots_reply(client):
    _add(client)
    _add(client, mid="out:1", direction="out", sender_name="MiHomes (Telegram)", text='"Pool pump" logged ✓')
    r = client.get("/conversations/")
    assert r.status_code == 200
    assert "Telegram group" in r.text
    assert "Pool pump is loud" in r.text
    assert "MiHomes (Telegram)" in r.text
    assert 'hx-trigger="every 30s"' in r.text
    assert 'href="/conversations/"' in r.text  # sidebar item, visible to an owner


def test_hostile_text_is_escaped(client):
    _add(client, text='<script>alert(1)</script><img src=x onerror=alert(2)>',
         sender_name='<b onmouseover=alert(3)>Eve</b>')
    for url in ("/conversations/", "/conversations/messages?channel=telegram&chat=-100888"):
        body = client.get(url).text
        assert "<script>alert(1)" not in body
        assert "<img src=x" not in body
        assert "<b onmouseover" not in body
        assert "&lt;script&gt;" in body
