"""G-R2 · shared gateway review core.

These lock the behavior of `services/gateways/review_common.py` — the single
canonical implementation both the Telegram and WhatsApp responders delegate to.
Every guarantee here corresponds to a defect that was live in the duplicated
responders before the extraction (H25/H26/H27/H36/M24/L12/L13).
"""

from datetime import date
from types import SimpleNamespace

import pytest

from mihomes.services.gateways import review_common as rc
from mihomes.services.property import create_property
from mihomes.services.staff import create_staff
from mihomes.services.vendor import create_vendor


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def prop(session):
    return create_property(session, "Belle Estate")


@pytest.fixture
def capture():
    """A gateway adapter that records every (chat_id, text) send."""
    sent: list[tuple[str, str]] = []
    adapter = rc.GatewayAdapter(label="Test", send=lambda cid, text: sent.append((cid, text)))
    return adapter, sent


def _dispatch(session, items, adapter, prop_slug, messages=None):
    return rc.dispatch_items(
        session,
        items,
        adapter=adapter,
        reply_target="chat-1",
        messages=messages or [],
        property_slug=prop_slug,
        resolve_reporter=lambda item: None,
    )


# --------------------------------------------------------------------------- #
# H36 / M38 — Vendor is resolved by company_name, never the nonexistent .name
# --------------------------------------------------------------------------- #
def test_resolve_vendor_uses_company_name(session):
    v = create_vendor(session, "Orkin Pest Control")
    session.flush()
    found = rc.resolve_vendor(session, "orkin")
    assert found is not None
    assert found.id == v.id


def test_resolve_vendor_none_for_blank(session):
    assert rc.resolve_vendor(session, None) is None
    assert rc.resolve_vendor(session, "") is None


def test_resolve_vendor_escapes_like_wildcards(session):
    # A stray % in the query must not match everything.
    create_vendor(session, "Precise Plumbing")
    assert rc.resolve_vendor(session, "%") is None


# --------------------------------------------------------------------------- #
# L12 — parse_event_date handles ISO tz-aware strings, not just naive strptime
# --------------------------------------------------------------------------- #
def test_parse_event_date_plain_date():
    assert rc.parse_event_date("2026-07-29") == date(2026, 7, 29)


def test_parse_event_date_tz_aware_iso():
    # The old strptime table had no %z format — this returned None and silently
    # dropped the appointment/expense date.
    assert rc.parse_event_date("2026-07-29T14:30:00+00:00") == date(2026, 7, 29)


def test_parse_event_date_zulu_microseconds():
    assert rc.parse_event_date("2026-07-29T14:30:00.123456Z") == date(2026, 7, 29)


def test_parse_event_date_garbage_and_none():
    assert rc.parse_event_date(None) is None
    assert rc.parse_event_date("not a date") is None


# --------------------------------------------------------------------------- #
# H26 — uploads dir resolves to the real package directory for BOTH gateways
# --------------------------------------------------------------------------- #
def test_uploads_dir_is_real_package_path():
    p = rc.uploads_dir()
    assert p.parts[-3:] == ("web", "static", "uploads")
    # Anchored inside the installed mihomes package, not a phantom src/web.
    assert "mihomes" in p.parts
    # It exists (helper creates it) and is a directory.
    assert p.is_dir()


# --------------------------------------------------------------------------- #
# M24 — the shared schema is the SUPERSET; every rich category is dispatchable
# --------------------------------------------------------------------------- #
def test_schema_enum_is_superset():
    enum = rc.REVIEW_SCHEMA["properties"]["items"]["items"]["properties"]["category"]["enum"]
    required = {
        "issue", "task", "task_completion", "supply_need", "vendor_activity",
        "question", "pto_request", "book_addition", "asset_addition",
        "issue_resolution", "work_order_request", "appointment_request",
        "expense_log", "note_addition", "informational",
    }
    assert required <= set(enum)


def test_dispatch_work_order_request_creates_wo(session, prop, capture):
    # On the old WhatsApp responder this category was dropped by the
    # `if category not in ("issue","task","vendor_activity")` guard.
    adapter, sent = capture
    create_vendor(session, "RoofCo")
    result = _dispatch(
        session,
        [{"category": "work_order_request", "title": "Roof leak repair",
          "property_slug": prop.slug, "vendor_name": "RoofCo"}],
        adapter, prop.slug,
    )
    from mihomes.models.work_order import WorkOrder
    wo = session.query(WorkOrder).filter(WorkOrder.title == "Roof leak repair").first()
    assert wo is not None
    assert result["logged"] == 1
    assert any("Roof leak repair" in text for _, text in sent)


def test_dispatch_expense_log_records_transaction(session, prop, capture):
    adapter, sent = capture
    result = _dispatch(
        session,
        [{"category": "expense_log", "title": "Pool chemicals",
          "property_slug": prop.slug, "amount": 200.0,
          "expense_category": "maintenance"}],
        adapter, prop.slug,
    )
    from mihomes.models.budget import Transaction
    tx = session.query(Transaction).filter(Transaction.description == "Pool chemicals").first()
    assert tx is not None
    assert result["logged"] == 1


def test_dispatch_task_creates_task(session, prop, capture):
    adapter, sent = capture
    result = _dispatch(
        session,
        [{"category": "task", "title": "Clean the pool", "property_slug": prop.slug}],
        adapter, prop.slug,
    )
    from mihomes.models.task import Task
    assert session.query(Task).filter(Task.title == "Clean the pool").first() is not None
    assert result["logged"] == 1
    assert any("Clean the pool" in text for _, text in sent)


def test_dispatch_supply_need_updates_stock(session, prop, capture):
    adapter, sent = capture
    result = _dispatch(
        session,
        [{"category": "supply_need", "title": "Paper towels",
          "property_slug": prop.slug, "quantity_to_order": 3}],
        adapter, prop.slug,
    )
    from mihomes.models.consumable import Consumable
    assert session.query(Consumable).filter(Consumable.name == "Paper towels").first() is not None
    assert result["logged"] == 1


def test_dispatch_unknown_category_skipped_not_crashed(session, prop, capture):
    adapter, sent = capture
    result = _dispatch(
        session,
        [{"category": "informational", "title": "hello team"}],
        adapter, prop.slug,
    )
    assert result["logged"] == 0
    assert sent == []


# --------------------------------------------------------------------------- #
# H27 — ai_response rolls back the session on provider failure
# --------------------------------------------------------------------------- #
def test_ai_response_rolls_back_on_failure(session, monkeypatch):
    calls = {"rollback": 0}
    real_rollback = session.rollback

    def _tracked_rollback():
        calls["rollback"] += 1
        return real_rollback()

    monkeypatch.setattr(session, "rollback", _tracked_rollback)

    def _boom(*a, **k):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("mihomes.services.ai.orchestrator.ask", _boom)
    out = rc.ai_response(session, "any prompt", role="maintenance", property_slug=None)
    assert out is None
    assert calls["rollback"] == 1


def test_ai_response_strips_markdown_and_handles_no_response(session, monkeypatch):
    monkeypatch.setattr(
        "mihomes.services.ai.orchestrator.ask",
        lambda *a, **k: SimpleNamespace(text="NO_RESPONSE"),
    )
    assert rc.ai_response(session, "q", role="estate_manager", property_slug=None) is None

    monkeypatch.setattr(
        "mihomes.services.ai.orchestrator.ask",
        lambda *a, **k: SimpleNamespace(text="**Shut off** the main valve."),
    )
    out = rc.ai_response(session, "q", role="maintenance", property_slug=None)
    assert out == "Shut off the main valve."


# --------------------------------------------------------------------------- #
# H25 — WhatsApp approver phone is derived from `sender` (bridge omits senderPhone)
# --------------------------------------------------------------------------- #
def test_sender_phone_from_bridge_sender_field():
    assert rc.sender_phone({"sender": "15551234567@s.whatsapp.net"}) == "15551234567"


def test_sender_phone_falls_back_to_senderPhone():
    assert rc.sender_phone({"senderPhone": "+1 555-123-4567"}) == "15551234567"


def test_sender_phone_empty():
    assert rc.sender_phone({}) == ""


# --------------------------------------------------------------------------- #
# Approval routing — remaining messages are returned; APPROVE handled
# --------------------------------------------------------------------------- #
def test_handle_approval_approves_and_filters(session, prop, capture):
    adapter, sent = capture
    staff = create_staff(session, "Maria Lopez")
    session.flush()
    from mihomes.services.staff_pto import create_pto_request
    req = create_pto_request(session, str(staff.id), ["2026-08-01"])
    session.flush()

    messages = [
        {"jid": "chat-1", "text": f"APPROVE {req.id}", "sender": "boss@x"},
        {"jid": "chat-1", "text": "unrelated chatter", "sender": "other@x"},
    ]
    remaining = rc.handle_approval_messages(
        session, messages, adapter=adapter, is_approver=lambda m: m["sender"] == "boss@x",
    )
    assert len(remaining) == 1
    assert remaining[0]["text"] == "unrelated chatter"
    from mihomes.models.staff_pto import PTOStatus, StaffPTORequest
    refreshed = session.get(StaffPTORequest, req.id)
    assert refreshed.status == PTOStatus.APPROVED
