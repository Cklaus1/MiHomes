"""Conversation log — what `handle_inbound` records for the Conversations page, and what it must not.

Recording is a side effect of the shared inbound loop, so its first rule is that it can never change
what the bot does: a failed write must not roll back the issue it sits beside (the H27 lesson).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from mihomes.models.chat_message import ChatMessage
from mihomes.services.gateways import conversation_log as log
from mihomes.services.gateways import review_common as rc
from mihomes.services.property import create_property


@pytest.fixture
def prop(session):
    return create_property(session, "Belle Estate")


def _msg(*, jid="-100", mid="1", text_="The gate is stuck", group=True, media=False):
    return {
        "id": f"{jid}:{mid}", "timestamp": "2025-08-29T20:40:00+00:00", "channel": "telegram",
        "jid": jid, "isGroup": group, "sender": "7001", "senderName": "Ana",
        "senderUsername": "", "text": text_, "hasMedia": media, "mediaPath": "/tmp/secret.jpg" if media else None,
        "propertySlug": None,
    }


def _run(session, prop, messages, *, items=None):
    sent: list = []
    adapter = rc.GatewayAdapter(
        label="Telegram", channel="telegram", send=lambda t, x: sent.append((t, x)),
        analyze=lambda *a, **k: {"items": items if items is not None else
                                 [{"category": "issue", "title": "Gate stuck", "severity": "medium"}]},
    )
    out = rc.handle_inbound(session, messages, adapter=adapter, property_slug=prop.slug)
    return out, sent


def _rows(session, **where):
    q = session.query(ChatMessage)
    for k, v in where.items():
        q = q.filter(getattr(ChatMessage, k) == v)
    return q.order_by(ChatMessage.sent_at).all()


def test_a_group_message_and_the_bots_reply_are_recorded(session, prop):
    out, sent = _run(session, prop, [_msg()])
    assert out["logged"] == 1 and sent
    inbound = _rows(session, direction="in")
    outbound = _rows(session, direction="out")
    assert [(r.chat_id, r.sender_name, r.text) for r in inbound] == [("-100", "Ana", "The gate is stuck")]
    assert inbound[0].property_slug == prop.slug
    assert len(outbound) == 1 and "Gate stuck" in outbound[0].text
    assert outbound[0].sender_name == "MiHomes (Telegram)"


def test_private_chats_are_never_recorded(session, prop):
    _run(session, prop, [_msg(jid="777", group=False)])
    assert _rows(session) == []


def test_a_link_code_is_never_recorded(session, prop):
    _run(session, prop, [_msg(text_="/link ABCD-1234")], items=[])
    assert _rows(session, direction="in") == []


def test_media_is_marked_but_its_path_is_not_stored(session, prop):
    _run(session, prop, [_msg(media=True, text_="")], items=[])
    (row,) = _rows(session, direction="in")
    assert row.has_media is True
    assert "/tmp" not in (row.text or "")


def test_redelivery_does_not_duplicate(session, prop):
    _run(session, prop, [_msg()], items=[])
    _run(session, prop, [_msg()], items=[])
    assert len(_rows(session, direction="in")) == 1


def test_a_failed_record_never_loses_the_issue(session, prop, monkeypatch):
    """Force the insert to fail INSIDE the database (an aborted statement), not just in Python."""

    class _Broken:
        def values(self, **kw):
            return self

        def on_conflict_do_nothing(self, **kw):
            return text("SELECT * FROM no_such_table_conversation_log")

    monkeypatch.setattr(log, "pg_insert", lambda table: _Broken())
    from mihomes.models.issue import Issue

    out, sent = _run(session, prop, [_msg()])
    assert out["logged"] == 1
    assert session.query(Issue).filter(Issue.title == "Gate stuck").count() == 1
    assert _rows(session) == []


def test_reading_lists_conversations_newest_first(session, prop):
    _run(session, prop, [_msg(jid="-1", mid="1")], items=[])
    _run(session, prop, [{**_msg(jid="-2", mid="1"), "timestamp": "2025-09-01T10:00:00+00:00"}], items=[])
    convos = log.list_conversations(session)
    assert [c["chat_id"] for c in convos] == ["-2", "-1"]
    assert [m.text for m in log.conversation_messages(session, "telegram", "-1")] == ["The gate is stuck"]


def test_staff_cannot_open_conversations(web_client_as):
    client = web_client_as("staff", scoped_to=[])
    assert client.get("/conversations/").status_code == 403
    assert client.get("/conversations/messages?channel=telegram&chat=-100").status_code == 403
    assert 'href="/conversations/"' not in client.get("/").text  # no sidebar link for staff
