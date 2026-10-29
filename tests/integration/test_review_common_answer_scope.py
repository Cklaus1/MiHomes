"""SPEC-013 A5, A8 — membership trust for in-app chat, and a refusal replacing an answer.

The rest of A8 (an adapter without the hook is unchanged) is `test_gateway_answer_scope.py`.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from mihomes.services.gateways import review_common
from mihomes.services.gateways.review_common import GatewayAdapter, is_trusted_sender, trusted_membership
from tests.integration.chat_helpers import app_session, estate

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)


def test_trusted_membership_truth_table(app_engine, estate, _pg_engine, account_b):
    """A5 — owner/admin trusted; staff, revoked, foreign, garbage and unbound are not."""
    from tests.integration.chat_helpers import cleanup_estate, seed_estate

    e = estate
    other = seed_estate(_pg_engine, account_b)
    try:
        with app_session(app_engine, e.account_id) as s:
            assert trusted_membership(s, e.owner, e.account_id)
            assert trusted_membership(s, str(e.admin), e.account_id)
            assert not trusted_membership(s, e.staff_belle, e.account_id)
            assert not trusted_membership(s, e.revoked, e.account_id)
            assert not trusted_membership(s, other.owner, e.account_id)   # another account's owner
            assert not trusted_membership(s, "not-a-uuid", e.account_id)
            assert not trusted_membership(s, uuid.uuid4(), e.account_id)
            assert not trusted_membership(s, e.owner, None)               # no tenant, no trust

            # Through the gateway entry point, with the allowlist bypassed for mihomes.
            msg = {"sender": str(e.owner)}
            assert is_trusted_sender(s, msg, gateway="mihomes", account=e.account_id)
            assert is_trusted_sender(s, msg, gateway="mihomes")           # bound account default
            assert not is_trusted_sender(s, {"sender": str(e.staff_belle)}, gateway="mihomes")
            assert not is_trusted_sender(s, {"sender": ""}, gateway="mihomes")
    finally:
        cleanup_estate(_pg_engine, other)


def test_refusal_replaces_answer(session, monkeypatch):
    """A8 — a hook yielding text sends that text; `ai_response` is never called."""
    calls: list[str] = []
    monkeypatch.setattr(review_common, "ai_response",
                        lambda *a, **k: calls.append("ai") or "SHOULD NOT SEND")
    sent: list[tuple[str, str]] = []

    @contextmanager
    def refuse(_session, _text, _slug, _messages, **_kw):
        yield "Ask that somewhere else."

    adapter = GatewayAdapter(label="test", channel="test", send=lambda t, x: sent.append((t, x)),
                             answer_scope=refuse)
    review_common.dispatch_items(
        session, [{"category": "question", "title": "How much did we spend?",
                   "description": "How much did we spend?"}],
        account=review_common.bound_account(), adapter=adapter, reply_target="room-1",
        messages=[], property_slug=None, resolve_reporter=lambda item: None,
    )
    assert calls == []
    assert [t for _, t in sent] == ["Ask that somewhere else."]


def test_mihomes_channel_writes_no_conversation_log(session):
    """D2/A3 seam — the native channel is never recorded into chat_messages."""
    adapter = GatewayAdapter(label="MiHomes chat", channel="mihomes", send=lambda t, x: None)
    wrapped = review_common._with_conversation_log(
        session, [{"jid": "c1", "isGroup": True, "text": "hello", "sender": "x"}], adapter, None)
    assert wrapped is adapter
    from mihomes.models.chat_message import ChatMessage

    assert session.query(ChatMessage).count() == 0
