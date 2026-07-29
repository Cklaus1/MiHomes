"""R2.12 · gateway safety — approval feedback, per-chat replies, sender allowlist.

Three defects, all previously live in BOTH duplicated responders:

* M25 — an APPROVE/DENY that raised was swallowed with only a log line, so the
  approver got silence and assumed it worked.
* M26 — a batch spanning two chats of the same property replied everything to the
  FIRST chat's jid; the second chat never heard back.
* M27 — money/PTO/resolution items were auto-created from ANY group member (no
  allowlist), and raw exception text was echoed into the group.
"""

import pytest

from mihomes.services.gateways import review_common as rc
from mihomes.services.property import create_property
from mihomes.services.staff import create_staff


@pytest.fixture
def prop(session):
    return create_property(session, "Belle Estate")


@pytest.fixture
def capture():
    sent: list[tuple[str, str]] = []
    adapter = rc.GatewayAdapter(label="Test", send=lambda cid, text: sent.append((cid, text)))
    return adapter, sent


# --------------------------------------------------------------------------- #
# M25 — approval failures must tell the approver, and must not leak raw errors
# --------------------------------------------------------------------------- #
def test_approval_failure_notifies_approver(session, capture, monkeypatch):
    adapter, sent = capture

    def boom(*a, **k):
        raise RuntimeError("db exploded: secret internal detail")

    monkeypatch.setattr("mihomes.services.staff_pto.approve_pto", boom)

    remaining = rc.handle_approval_messages(
        session,
        [{"text": "APPROVE 7", "jid": "chat-A", "sender": "boss"}],
        adapter=adapter,
        is_approver=lambda m: True,
    )
    assert remaining == [], "an approval command is always consumed"
    assert len(sent) == 1, "approver must get feedback on failure"
    target, text = sent[0]
    assert target == "chat-A"
    assert "7" in text  # references the request id
    assert "secret internal detail" not in text, "raw exception must not leak"


# --------------------------------------------------------------------------- #
# M26 — replies are grouped by originating chat, never dumped on the first jid
# --------------------------------------------------------------------------- #
def test_group_by_target_splits_chats():
    messages = [
        {"jid": "chat-A", "text": "one"},
        {"jid": "chat-B", "text": "two"},
        {"jid": "chat-A", "text": "three"},
        {"text": "no jid"},  # dropped — nowhere to reply
    ]
    groups = rc.group_by_target(messages)
    assert set(groups) == {"chat-A", "chat-B"}
    assert [m["text"] for m in groups["chat-A"]] == ["one", "three"]
    assert [m["text"] for m in groups["chat-B"]] == ["two"]


# --------------------------------------------------------------------------- #
# M27 — sensitive categories require a trusted sender; errors stay generic
# --------------------------------------------------------------------------- #
def test_untrusted_sender_cannot_log_expense(session, capture, prop):
    adapter, sent = capture
    items = [{"category": "expense_log", "title": "Cash", "amount": 5000, "property_slug": prop.slug}]
    result = rc.dispatch_items(
        session, items,
        adapter=adapter,
        reply_target="chat-A",
        messages=[],
        property_slug=prop.slug,
        resolve_reporter=lambda item: None,
        sender_trusted=False,
    )
    assert result["logged"] == 0, "untrusted sender must not create money records"


def test_trusted_sender_can_log_expense(session, capture, prop):
    adapter, sent = capture
    items = [{"category": "expense_log", "title": "Repair", "amount": 42.0, "property_slug": prop.slug}]
    result = rc.dispatch_items(
        session, items,
        adapter=adapter,
        reply_target="chat-A",
        messages=[],
        property_slug=prop.slug,
        resolve_reporter=lambda item: None,
        sender_trusted=True,
    )
    assert result["logged"] == 1, "trusted sender logs the expense as before"


def test_non_sensitive_category_unaffected_by_trust(session, capture, prop):
    """A plain issue is not gated — any resident can report a broken boiler."""
    adapter, sent = capture
    items = [{"category": "issue", "title": "Boiler leaking", "severity": "high", "property_slug": prop.slug}]
    result = rc.dispatch_items(
        session, items,
        adapter=adapter,
        reply_target="chat-A",
        messages=[],
        property_slug=prop.slug,
        resolve_reporter=lambda item: None,
        sender_trusted=False,
    )
    assert result["logged"] == 1, "non-sensitive categories are never gated"


def test_is_trusted_sender_matches_staff(session, prop):
    """A staff member's phone is trusted even with no explicit allowlist configured."""
    create_staff(session, "Maria Gomez", role="housekeeper", whatsapp_phone="+15551234567")
    session.flush()
    trusted = rc.is_trusted_sender(
        session, {"sender": "15551234567@s.whatsapp.net"}, gateway="whatsapp"
    )
    assert trusted is True


def test_is_trusted_sender_rejects_stranger(session, prop):
    assert rc.is_trusted_sender(
        session, {"sender": "19998887777@s.whatsapp.net"}, gateway="whatsapp"
    ) is False
