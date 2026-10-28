"""Gateway AI answers are narrowed to their audience (Telegram/WhatsApp groups and DMs).

Before this, background dispatch bound no role, so `_denied_categories()` denied nothing and a
group question was answered as an unrestricted estate manager — finance, contracts, staff records,
every property — into a room with staff and unlinked members in it. These tests record the role
and property scope that are bound at the moment the AI is called.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.authz.scope import current_property_scope, current_role
from mihomes.models.membership import Membership
from mihomes.models.telegram_link import TelegramLink
from mihomes.models.user import User
from mihomes.services.gateways import review_common as rc
from mihomes.services.gateways.answer_scope import answer_scope_for
from mihomes.services.gateways.telegram.financial_guard import DM_OFFER, STAFF_REFUSAL
from mihomes.services.property import create_property

OWNER_TG, STAFF_TG, UNLINKED_TG = 61001, 61002, 61003


@pytest.fixture
def estate(session, account_a):
    belle = create_property(session, "Belle Estate")
    other = create_property(session, "Lake House")
    for tg, role in ((OWNER_TG, "owner"), (STAFF_TG, "staff")):
        user = User(id=uuid.uuid4(), google_sub=f"sub-{uuid.uuid4().hex[:10]}",
                    email=f"as-{uuid.uuid4().hex[:8]}@example.com")
        session.add(user)
        session.flush()
        m = Membership(id=uuid.uuid4(), account_id=account_a, user_id=user.id, role=role,
                       status="active")
        session.add(m)
        session.flush()
        session.add(TelegramLink(id=uuid.uuid4(), account_id=account_a, membership_id=m.id,
                                 telegram_user_id=tg))
        if role == "staff":
            from mihomes.models.membership import MembershipPropertyScope
            session.add(MembershipPropertyScope(id=uuid.uuid4(), account_id=account_a,
                                                membership_id=m.id, property_id=belle.id))
    session.flush()
    return belle, other


def _msg(sender: int, *, group: bool, text: str = "q") -> dict:
    jid = "-100500" if group else str(sender)
    return {"id": f"{jid}:{uuid.uuid4().hex[:6]}", "timestamp": "2026-10-28T10:00:00+00:00",
            "channel": "telegram", "jid": jid, "isGroup": group, "sender": str(sender),
            "senderName": "X", "senderUsername": "", "text": text, "hasMedia": False,
            "mediaPath": None, "propertySlug": None}


@pytest.fixture
def ai_spy(monkeypatch):
    """Record the role/scope bound when the AI answers; return a canned answer."""
    calls: list[tuple] = []

    def _fake(session, prompt, role, property_slug):
        calls.append((current_role.get(), current_property_scope.get(), role))
        return "an answer"

    monkeypatch.setattr(rc, "ai_response", _fake)
    return calls


def _ask(session, belle, message, question, *, items=None):
    sent: list = []
    adapter = rc.GatewayAdapter(
        label="Telegram", channel="telegram", send=lambda t, x: sent.append(x),
        analyze=lambda *a, **k: {"items": items or [
            {"category": "question", "title": question, "description": question}]},
        answer_scope=answer_scope_for("telegram"),
    )
    rc.handle_inbound(session, [message], adapter=adapter, property_slug=belle.slug)
    return sent


@pytest.mark.parametrize("sender", [OWNER_TG, STAFF_TG, UNLINKED_TG])
def test_group_answers_run_as_staff_scoped_to_the_chats_property(session, estate, ai_spy, sender):
    belle, _other = estate
    sent = _ask(session, belle, _msg(sender, group=True), "When is the pool serviced?")
    assert sent == ["an answer"]
    (role, scope, _ai_role), = ai_spy
    assert role == "staff", "a group answer must not run unrestricted — staff may read it"
    assert scope == frozenset({belle.id}), "only the chat's own property, never the whole estate"


def test_a_money_question_in_a_group_is_redirected_for_an_owner(session, estate, ai_spy):
    belle, _ = estate
    sent = _ask(session, belle, _msg(OWNER_TG, group=True), "How much did we spend on the pool?")
    assert sent == [DM_OFFER]
    assert ai_spy == [], "the AI must not even be asked"


@pytest.mark.parametrize("sender", [STAFF_TG, UNLINKED_TG])
def test_a_money_question_is_refused_for_staff_and_unlinked(session, estate, ai_spy, sender):
    belle, _ = estate
    for group in (True, False):
        sent = _ask(session, belle, _msg(sender, group=group), "What's the monthly budget?")
        assert sent == [STAFF_REFUSAL]
    assert ai_spy == []


def test_an_owner_dm_is_unrestricted(session, estate, ai_spy):
    belle, _ = estate
    sent = _ask(session, belle, _msg(OWNER_TG, group=False), "How much did we spend on the pool?")
    assert sent == ["an answer"]
    assert ai_spy[0][:2] == (None, None)


def test_a_staff_dm_runs_as_staff_with_their_own_properties(session, estate, ai_spy):
    belle, _ = estate
    _ask(session, belle, _msg(STAFF_TG, group=False), "When is the pool serviced?")
    assert ai_spy[0][:2] == ("staff", frozenset({belle.id}))


def test_an_unplaceable_dm_sender_gets_no_properties(session, estate, ai_spy):
    belle, _ = estate
    _ask(session, belle, _msg(UNLINKED_TG, group=False), "When is the pool serviced?")
    assert ai_spy[0][:2] == ("staff", frozenset())


def test_one_staff_sender_in_a_batch_restricts_the_whole_batch(session, estate, ai_spy):
    belle, _ = estate
    sent: list = []
    adapter = rc.GatewayAdapter(
        label="Telegram", channel="telegram", send=lambda t, x: sent.append(x),
        analyze=lambda *a, **k: {"items": [{"category": "question", "title": "budget?",
                                            "description": "What's the budget?"}]},
        answer_scope=answer_scope_for("telegram"),
    )
    rc.handle_inbound(session, [_msg(OWNER_TG, group=False), _msg(STAFF_TG, group=False)],
                      adapter=adapter, property_slug=belle.slug)
    assert STAFF_REFUSAL in sent


def test_the_expert_note_on_an_issue_is_scoped_but_not_money_gated(session, estate, monkeypatch):
    belle, _ = estate
    seen: list = []
    monkeypatch.setattr(rc, "issue_expert_reply",
                        lambda s, title, desc, prop: seen.append((current_role.get(),
                                                                  current_property_scope.get()))
                        or "Check the breaker.")
    sent = _ask(session, belle, _msg(STAFF_TG, group=True, text="x"), "",
                items=[{"category": "issue", "title": "Pool heater costs too much to run",
                        "severity": "medium"}])
    assert any("Check the breaker." in s for s in sent), sent
    assert seen == [("staff", frozenset({belle.id}))]


def test_restriction_is_released_after_the_answer(session, estate, ai_spy):
    belle, _ = estate
    _ask(session, belle, _msg(STAFF_TG, group=True), "When is the pool serviced?")
    assert current_role.get() is None and current_property_scope.get() is None


def test_adapters_without_the_hook_are_unchanged(session, estate, ai_spy):
    belle, _ = estate
    sent: list = []
    adapter = rc.GatewayAdapter(
        label="X", send=lambda t, x: sent.append(x),
        analyze=lambda *a, **k: {"items": [{"category": "question", "title": "budget?",
                                            "description": "What's the budget?"}]},
    )
    rc.handle_inbound(session, [_msg(STAFF_TG, group=True)], adapter=adapter,
                      property_slug=belle.slug)
    assert sent == ["an answer"] and ai_spy[0][:2] == (None, None)
