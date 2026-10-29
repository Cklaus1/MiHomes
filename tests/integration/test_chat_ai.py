"""SPEC-013 A1–A4, A6, A7, A9–A14, A29 — the AI in group chats.

`dispatch` runs exactly as in production: its own `get_session()` sessions, on the **app role**
(`mihomes.db` pinned to `app_engine`), inside `account_context`. Analysis is stubbed at
`review_common.analyze_messages` (by attribute, as the bridge calls it); A12 instead stubs the
*provider*, so the real context assembly runs under whatever restriction the bridge bound.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from mihomes.models.chat_native import ChatConversation
from mihomes.models.membership import Membership
from mihomes.services.chat import ai_bridge, posts
from mihomes.services.chat import conversations as conv
from mihomes.services.gateways import review_common
from mihomes.services.gateways.telegram.financial_guard import STAFF_REFUSAL
from tests.integration.chat_helpers import app_client, app_session, estate

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)


@pytest.fixture
def wired(app_engine, monkeypatch):
    """The background path's `get_session()` on the app role; bus events captured."""
    from mihomes import db

    prev = db._engine, db._SessionLocal
    db._engine, db._SessionLocal = app_engine, None
    published: list[tuple] = []
    monkeypatch.setattr(ai_bridge.BUS, "publish", lambda a, ev: published.append((str(a), ev)))
    try:
        yield published
    finally:
        db._engine, db._SessionLocal = prev
        app_engine.dispose()


@pytest.fixture
def analysis(monkeypatch):
    """Set `analysis.items` to what the model 'extracts'; `analysis.calls` counts runs."""
    class A:
        items: list[dict] = []
        calls = 0
        fail = False

    def fake(session, messages, property_name=None, property_slug=None):
        A.calls += 1
        if A.fail:
            raise RuntimeError("model down")
        return {"items": [dict(i) for i in A.items]}

    monkeypatch.setattr(review_common, "analyze_messages", fake)
    return A


def _rooms(app_engine, e) -> dict[str, uuid.UUID]:
    with app_session(app_engine, e.account_id) as s:
        ids = {"general": conv.get_or_create_estate(s).id,
               "belle": conv.get_or_create_property(s, e.belle).id,
               "blue": conv.get_or_create_property(s, e.blue).id,
               "dm": conv.get_or_create_dm(s, s.get(Membership, e.owner), e.staff_belle).id}
        s.commit()
        return ids


def _post(app_engine, e, conversation_id, author_id, body) -> uuid.UUID:
    with app_session(app_engine, e.account_id) as s:
        p = posts.create_post(s, s.get(ChatConversation, conversation_id),
                              author=s.get(Membership, author_id), body=body)
        s.commit()
        return p.id


def _dispatch(e, conversation_id, post_id):
    ai_bridge.dispatch(e.account_id, conversation_id, post_id)


def _bot_posts(_pg_engine, conversation_id) -> list[str]:
    with _pg_engine.connect() as c:
        return list(c.execute(text("SELECT body FROM chat_posts WHERE conversation_id = :c "
                                   "AND author_kind = 'bot' ORDER BY seq"),
                              {"c": conversation_id}).scalars())


def _count(_pg_engine, table, account_id) -> int:
    with _pg_engine.connect() as c:
        return c.execute(text(f"SELECT count(*) FROM {table} WHERE account_id = :a"),  # noqa: S608
                         {"a": account_id}).scalar()


@pytest.fixture(autouse=True)
def _no_expert_note(monkeypatch):
    monkeypatch.setattr(review_common, "issue_expert_reply", lambda *a, **k: None)


def test_dm_never_dispatched(wired, analysis, app_engine, _pg_engine, estate):
    """A1"""
    e = estate
    rooms = _rooms(app_engine, e)
    analysis.items = [{"category": "issue", "title": "Leak"}]
    pid = _post(app_engine, e, rooms["dm"], e.owner, "the boiler is leaking")
    _dispatch(e, rooms["dm"], pid)
    assert analysis.calls == 0
    assert _bot_posts(_pg_engine, rooms["dm"]) == []


def test_issue_logged_and_bot_replies_in_same_conversation(wired, analysis, app_engine,
                                                           _pg_engine, estate):
    """A2 + A3 — one issue at Belle, the bot's confirmation in Belle's chat, no chat_messages."""
    e = estate
    rooms = _rooms(app_engine, e)
    analysis.items = [{"category": "issue", "title": "Boiler leaking"}]
    issues_before = _count(_pg_engine, "issues", e.account_id)
    pid = _post(app_engine, e, rooms["belle"], e.owner, "the boiler is leaking")
    _dispatch(e, rooms["belle"], pid)
    assert _count(_pg_engine, "issues", e.account_id) == issues_before + 1
    with _pg_engine.connect() as c:
        prop = c.execute(text("SELECT property_id FROM issues WHERE account_id = :a "
                              "ORDER BY created_at DESC LIMIT 1"), {"a": e.account_id}).scalar()
        bot = c.execute(text("SELECT author_membership_id, body FROM chat_posts "
                             "WHERE conversation_id = :c AND author_kind = 'bot'"),
                        {"c": rooms["belle"]}).one()
    assert prop == e.belle
    assert bot.author_membership_id is None and bot.body == '"Boiler leaking" logged ✓'
    assert _count(_pg_engine, "chat_messages", e.account_id) == 0          # A3


def test_staff_sender_untrusted_no_expense(wired, analysis, app_engine, _pg_engine, estate):
    """A4 — a staff sender's expense is not logged; the owner's is."""
    e = estate
    rooms = _rooms(app_engine, e)
    analysis.items = [{"category": "expense_log", "title": "Pool chemicals", "amount": 42.5,
                       "description": "Pool chemicals"}]
    before = _count(_pg_engine, "transactions", e.account_id)
    _dispatch(e, rooms["belle"], _post(app_engine, e, rooms["belle"], e.staff_belle, "spent 42.50"))
    assert _count(_pg_engine, "transactions", e.account_id) == before
    _dispatch(e, rooms["belle"], _post(app_engine, e, rooms["belle"], e.owner, "spent 42.50"))
    assert _count(_pg_engine, "transactions", e.account_id) == before + 1


def test_general_ambiguous_property_asks_and_logs_nothing(wired, analysis, app_engine,
                                                          _pg_engine, estate):
    """A7 — two properties, none named: ask (never naming one), log nothing; chit-chat: silence."""
    e = estate
    rooms = _rooms(app_engine, e)
    before = _count(_pg_engine, "issues", e.account_id)
    analysis.items = [{"category": "issue", "title": "Gate stuck"}]
    _dispatch(e, rooms["general"], _post(app_engine, e, rooms["general"], e.owner, "gate is stuck"))
    assert _bot_posts(_pg_engine, rooms["general"]) == [ai_bridge.ASK_WHICH_PROPERTY]
    assert _count(_pg_engine, "issues", e.account_id) == before
    assert "Belle" not in ai_bridge.ASK_WHICH_PROPERTY and "Blue" not in ai_bridge.ASK_WHICH_PROPERTY

    analysis.items = [{"category": "informational", "title": "hi"}]
    _dispatch(e, rooms["general"], _post(app_engine, e, rooms["general"], e.owner, "good morning"))
    assert _bot_posts(_pg_engine, rooms["general"]) == [ai_bridge.ASK_WHICH_PROPERTY]

    # Naming a property routes it there.
    analysis.items = [{"category": "issue", "title": "Gate stuck at Blue"}]
    _dispatch(e, rooms["general"],
              _post(app_engine, e, rooms["general"], e.owner, "the gate at Blue is stuck"))
    assert _count(_pg_engine, "issues", e.account_id) == before + 1


def test_staff_in_general_routes_to_their_only_property(wired, analysis, app_engine,
                                                         _pg_engine, estate):
    """D4 — a staff author scoped only to Belle files General posts at Belle, even naming Blue."""
    e = estate
    rooms = _rooms(app_engine, e)
    analysis.items = [{"category": "issue", "title": "Leak"}]
    _dispatch(e, rooms["general"],
              _post(app_engine, e, rooms["general"], e.staff_belle, "Blue has a leak"))
    with _pg_engine.connect() as c:
        prop = c.execute(text("SELECT property_id FROM issues WHERE account_id = :a "
                              "ORDER BY created_at DESC LIMIT 1"), {"a": e.account_id}).scalar()
    assert prop == e.belle


def test_approval_bypasses_property_question(wired, analysis, app_engine, _pg_engine, estate,
                                             monkeypatch):
    """A6 — `APPROVE <uuid>` from an admin in General is consumed as an approval."""
    from mihomes.services import staff_pto

    e = estate
    rooms = _rooms(app_engine, e)
    approved: list[str] = []
    monkeypatch.setattr(staff_pto, "approve_pto",
                        lambda s, rid, **k: approved.append(rid) or (_ for _ in ()).throw(
                            LookupError("no such request")))
    rid = str(uuid.uuid4())
    _dispatch(e, rooms["general"], _post(app_engine, e, rooms["general"], e.admin, f"APPROVE {rid}"))
    assert approved == [rid]
    assert analysis.calls == 0
    bots = _bot_posts(_pg_engine, rooms["general"])
    assert bots == [f"Could not approve #{rid} — please try again or log manually."]


def test_loose_approve_text_not_hijacked(wired, analysis, app_engine, _pg_engine, estate,
                                         monkeypatch):
    """A29 — "Approve the paint quote" is analysed normally; approve_pto is never called."""
    from mihomes.services import staff_pto

    e = estate
    rooms = _rooms(app_engine, e)
    monkeypatch.setattr(staff_pto, "approve_pto", lambda *a, **k: pytest.fail("hijacked"))
    analysis.items = [{"category": "task", "title": "Approve the paint quote"}]
    _dispatch(e, rooms["belle"],
              _post(app_engine, e, rooms["belle"], e.owner, "Approve the paint quote"))
    assert analysis.calls == 1
    bots = _bot_posts(_pg_engine, rooms["belle"])
    assert bots == ['"Approve the paint quote" logged ✓']


def test_redispatch_processed_once(wired, analysis, app_engine, estate):
    """A9 — the same post twice is analysed once, even if the first attempt raised."""
    e = estate
    rooms = _rooms(app_engine, e)
    analysis.fail = True
    pid = _post(app_engine, e, rooms["belle"], e.owner, "the fence is down")
    _dispatch(e, rooms["belle"], pid)
    analysis.fail = False
    _dispatch(e, rooms["belle"], pid)
    assert analysis.calls == 1


def test_route_schedules_once_after_commit(wired, analysis, app_client, app_engine, _pg_engine,
                                           estate, monkeypatch):
    """A10 — one dispatch per created post, none for a DM or a client_token replay; the post is
    committed when the task runs (it reads it on another connection)."""
    e = estate
    rooms = _rooms(app_engine, e)
    seen: list[tuple] = []

    def fake_dispatch(account_id, conversation_id, post_id):
        with _pg_engine.connect() as c:   # a different connection: only committed rows
            committed = c.execute(text("SELECT count(*) FROM chat_posts WHERE id = :p"),
                                  {"p": post_id}).scalar()
        seen.append((conversation_id, post_id, committed))

    monkeypatch.setattr(ai_bridge, "dispatch", fake_dispatch)
    owner = app_client(e.account_id, e.owner)
    token = str(uuid.uuid4())
    for _ in range(2):   # a double-submit
        r = owner.post(f"/chat/c/{rooms['belle']}/posts",
                       data={"body": "the boiler is leaking", "client_token": token})
        assert r.status_code == 200
    owner.post(f"/chat/c/{rooms['dm']}/posts", data={"body": "private"})
    assert len(seen) == 1 and seen[0][0] == rooms["belle"] and seen[0][2] == 1


def test_finance_question_refused_in_staff_room(wired, analysis, app_engine, _pg_engine, estate,
                                                monkeypatch):
    """A11 — money question in a room with staff: refused; ai_response never called."""
    e = estate
    rooms = _rooms(app_engine, e)
    monkeypatch.setattr(review_common, "ai_response", lambda *a, **k: pytest.fail("answered"))
    q = "How much have we spent on the pool this year?"
    analysis.items = [{"category": "question", "title": q, "description": q}]
    _dispatch(e, rooms["belle"], _post(app_engine, e, rooms["belle"], e.owner, q))
    _dispatch(e, rooms["belle"], _post(app_engine, e, rooms["belle"], e.staff_belle, q))
    assert _bot_posts(_pg_engine, rooms["belle"]) == [ai_bridge.FINANCE_IN_SHARED_ROOM,
                                                     STAFF_REFUSAL]


def test_answer_bound_to_audience(wired, analysis, app_engine, _pg_engine, estate, monkeypatch):
    """A12 — General asking about Blue while a reader sees only Belle → OUTSIDE_AUDIENCE. In
    Belle's chat the real provider path runs (asserted) and its context holds no budget section
    and no Blue rows."""
    from mihomes.services.ai import orchestrator

    e = estate
    rooms = _rooms(app_engine, e)
    with app_session(app_engine, e.account_id) as s:   # an open issue at each property
        from mihomes.services.issue import create_issue

        belle_slug = s.execute(text("SELECT slug FROM properties WHERE id = :p"),
                               {"p": e.belle}).scalar()
        blue_slug = s.execute(text("SELECT slug FROM properties WHERE id = :p"),
                              {"p": e.blue}).scalar()
        create_issue(s, "Belle roof drip", belle_slug)
        create_issue(s, "Blue window cracked", blue_slug)
        s.commit()

    contexts: list[str] = []

    class FakeProvider:
        def complete(self, system_prompt, query, context_data=None, attachments=None):
            contexts.append(str(context_data))
            return "The roof drip is the open item."

    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(orchestrator, "get_provider", lambda *a, **k: FakeProvider())

    q = "What maintenance is open at Blue?"
    analysis.items = [{"category": "question", "title": q, "description": q}]
    _dispatch(e, rooms["general"], _post(app_engine, e, rooms["general"], e.owner, q))
    assert _bot_posts(_pg_engine, rooms["general"]) == [ai_bridge.OUTSIDE_AUDIENCE]
    assert contexts == []

    q = "What maintenance is open here?"
    analysis.items = [{"category": "question", "title": q, "description": q}]
    _dispatch(e, rooms["belle"], _post(app_engine, e, rooms["belle"], e.owner, q))
    assert len(contexts) == 1, "the provider was never called"
    ctx = contexts[0]
    assert "Belle roof drip" in ctx
    assert "Blue window cracked" not in ctx and "Budget Status" not in ctx
    assert _bot_posts(_pg_engine, rooms["belle"]) == ["The roof drip is the open item."]


def test_item_property_pinned_to_chat(wired, analysis, app_engine, _pg_engine, estate):
    """A13 — an item tagged with Blue is still filed at Belle from Belle's chat."""
    e = estate
    rooms = _rooms(app_engine, e)
    with _pg_engine.connect() as c:
        blue_slug = c.execute(text("SELECT slug FROM properties WHERE id = :p"),
                              {"p": e.blue}).scalar()
    analysis.items = [{"category": "issue", "title": "Leak", "property_slug": blue_slug}]
    _dispatch(e, rooms["belle"], _post(app_engine, e, rooms["belle"], e.owner, "leak"))
    with _pg_engine.connect() as c:
        prop = c.execute(text("SELECT property_id FROM issues WHERE account_id = :a "
                              "ORDER BY created_at DESC LIMIT 1"), {"a": e.account_id}).scalar()
    assert prop == e.belle


def test_thinking_always_cleared(wired, analysis, app_engine, _pg_engine, estate):
    """A14 — thinking then done; done also on a crash, an already-claimed id and a revoked author."""
    e = estate
    rooms = _rooms(app_engine, e)
    published = wired

    def types_for(pid):
        return [ev["type"] for _a, ev in published if ev.get("p") == str(pid)
                and ev["type"].startswith("ai.")]

    analysis.items = [{"category": "informational", "title": "hi"}]
    ok = _post(app_engine, e, rooms["belle"], e.owner, "hello")
    _dispatch(e, rooms["belle"], ok)
    assert types_for(ok) == ["ai.thinking", "ai.done"]

    analysis.fail = True
    crash = _post(app_engine, e, rooms["belle"], e.owner, "boom")
    _dispatch(e, rooms["belle"], crash)
    assert types_for(crash)[-1] == "ai.done"
    analysis.fail = False

    published.clear()
    _dispatch(e, rooms["belle"], ok)                       # already claimed
    assert types_for(ok) == ["ai.done"]

    revoked_post = _post(app_engine, e, rooms["belle"], e.staff_belle, "still here?")
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE memberships SET status = 'revoked' WHERE id = :m"),
                  {"m": e.staff_belle})
    _dispatch(e, rooms["belle"], revoked_post)
    assert types_for(revoked_post) == ["ai.done"]
    assert all(a == str(e.account_id) for a, _ev in published)
