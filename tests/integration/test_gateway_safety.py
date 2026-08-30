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
        account=rc.bound_account(),
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
        account=rc.bound_account(),
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
        account=rc.bound_account(),
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


# --------------------------------------------------------------------------- #
# SPEC-006 A12 (D8) — trust is resolved WITHIN an account
# --------------------------------------------------------------------------- #
def test_trust_is_account_scoped(session, account_a, account_b, prop):
    """**A12** — `is_trusted_sender` never matches staff from another account.

    The staff-match branch ran `session.query(Staff).filter(whatsapp_phone is not None)` with
    **no account filter**, and trust here is the gate on *money and PTO* actions (M27).

    **Measured honestly: on a tenancy-scoped session this test passes with or without the
    explicit filter** — probed directly, an unfiltered `session.query(Staff)` bound to account
    A returns `[]` when the only matching staff member lives in B, because `query_scope`
    already applies the tenant criteria. So this test does *not*, on its own, prove the filter
    added in G4 is load-bearing.

    It is kept, and the filter is kept, for the case the session cannot cover: an **unscoped**
    session, which is exactly the state the webhook edge is in before `resolve_sender` has run
    (§5.1's carve-out). There the ORM listener applies nothing, and without the explicit filter
    a phone registered in B is trusted in A. Defence in depth, stated as such rather than
    claimed as the primary mechanism.

    **Written at module level deliberately**, despite §8 declaring a bare node id and this
    file's own flat convention agreeing: harness C10 records that a nested name would not
    resolve under `--collect`, and the pending-set expiry test cannot catch that.

    Paired, because "B's staff are not trusted in A" is vacuously true if nobody is ever
    trusted: the same phone is registered to a staff member in A and asserted trusted there.
    """
    from mihomes.services.staff import create_staff
    from mihomes.tenancy.context import account_context

    phone = "+15557654321"
    message = {"sender": "15557654321@s.whatsapp.net"}

    # A staff member with this phone exists in account B, and ONLY in B.
    with account_context(account_b):
        create_staff(session, "B Housekeeper", role="housekeeper", whatsapp_phone=phone)
        session.flush()

    # --- negative: B's staff member is not trusted in A ---------------------------------
    assert (
        rc.is_trusted_sender(session, message, gateway="whatsapp", account=account_a)
        is False
    ), (
        "a staff phone registered in account B was trusted in account A — that sender could "
        "log expenses and file PTO against an estate they have nothing to do with (D8)"
    )

    # --- positive: the same phone, registered in A, IS trusted there ---------------------
    # Without this the assertion above passes for a function that trusts nobody at all.
    create_staff(session, "A Housekeeper", role="housekeeper", whatsapp_phone=phone)
    session.flush()
    assert (
        rc.is_trusted_sender(session, message, gateway="whatsapp", account=account_a)
        is True
    ), "a staff member in the caller's own account must still be trusted"
