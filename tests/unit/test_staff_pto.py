"""Tests for staff PTO service."""

import uuid
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.models.staff import Staff, StaffRole
from mihomes.models.staff_pto import PTOStatus
from mihomes.models.task import Task, TaskPriority, TaskStatus
from mihomes.services.staff_pto import (
    approve_pto,
    create_pto_request,
    deny_pto,
    get_pto_balance,
    list_pto_requests,
    notify_approver,
    notify_staff,
)


def _make_property(session):
    prop = Property(name="Belle Estate", slug="belle-estate", property_type=PropertyType.PRIMARY)
    session.add(prop)
    session.flush()
    return prop


def _make_staff(session, name="Diego Regalado", slug="diego-regalado", whatsapp=None):
    # A property must exist before staff can be attached to one.
    if not session.query(Property).first():
        _make_property(session)
    member = Staff(
        name=name, slug=slug, role=StaffRole.GROUNDSKEEPER,
        whatsapp_phone=whatsapp,
    )
    session.add(member)
    session.flush()
    return member


class TestCreatePTORequest:
    def test_creates_pending_request(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01", "2026-05-02"])
        assert req.id is not None
        assert req.staff_id == staff.id
        assert req.status == PTOStatus.PENDING
        assert req.dates == ["2026-05-01", "2026-05-02"]

    def test_stores_notes(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"], notes="family event")
        assert req.notes == "family event"

    def test_no_coverage_warning_when_no_tasks(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        assert req.coverage_warning is None

    def test_coverage_warning_when_tasks_overlap(self, session):
        prop = _make_property(session)
        staff = Staff(name="Maria", slug="maria", role=StaffRole.HOUSEKEEPER)
        session.add(staff)
        session.flush()
        task = Task(
            title="Clean pool", slug="clean-pool",
            property_id=prop.id, assignee_id=staff.id,
            due_date=date(2026, 5, 1), priority=TaskPriority.MEDIUM,
        )
        session.add(task)
        session.flush()
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        assert req.coverage_warning is not None
        assert "1 task" in req.coverage_warning

    def test_no_warning_for_completed_tasks(self, session):
        prop = _make_property(session)
        staff = Staff(name="Maria", slug="maria", role=StaffRole.HOUSEKEEPER)
        session.add(staff)
        session.flush()
        task = Task(
            title="Done task", slug="done-task",
            property_id=prop.id, assignee_id=staff.id,
            due_date=date(2026, 5, 1), priority=TaskPriority.MEDIUM,
            status=TaskStatus.COMPLETED,
        )
        session.add(task)
        session.flush()
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        assert req.coverage_warning is None


class TestApprovePTO:
    def test_approve_sets_status(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        with patch("mihomes.services.staff_pto._sync_to_calendar"):
            approved = approve_pto(session, req.id, decided_by="admin")
        assert approved.status == PTOStatus.APPROVED
        assert approved.decided_by == "admin"
        assert approved.decided_at is not None

    def test_approve_not_found_raises(self, session):
        with pytest.raises(ValueError, match="not found"):
            approve_pto(session, uuid.uuid4())

    def test_approve_already_decided_raises_and_no_resync(self, session):
        # L8: an already-APPROVED request must not be re-approved — re-mutating
        # it and re-firing calendar sync would double-book the leave.
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        with patch("mihomes.services.staff_pto._sync_to_calendar") as sync:
            approve_pto(session, req.id, decided_by="admin")
            assert sync.call_count == 1
            with pytest.raises(ValueError, match="not pending"):
                approve_pto(session, req.id, decided_by="admin2")
            # guard must fire before any re-sync
            assert sync.call_count == 1
        assert req.decided_by == "admin"


class TestDenyPTO:
    def test_deny_sets_status(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        denied = deny_pto(session, req.id, decided_by="admin")
        assert denied.status == PTOStatus.DENIED
        assert denied.decided_by == "admin"

    def test_deny_appends_reason_to_notes(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"], notes="family event")
        deny_pto(session, req.id, reason="short-staffed")
        assert "Denied: short-staffed" in req.notes

    def test_deny_creates_notes_when_none(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        deny_pto(session, req.id, reason="busy week")
        assert req.notes == "Denied: busy week"

    def test_deny_not_found_raises(self, session):
        with pytest.raises(ValueError, match="not found"):
            deny_pto(session, uuid.uuid4())

    def test_deny_already_decided_raises(self, session):
        # L8: symmetric guard — an already-DENIED request must not be re-decided.
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        deny_pto(session, req.id, reason="busy week")
        with pytest.raises(ValueError, match="not pending"):
            deny_pto(session, req.id, reason="changed mind")


class TestListPTORequests:
    def test_list_all(self, session):
        staff = _make_staff(session)
        create_pto_request(session, staff.slug, ["2026-05-01"])
        create_pto_request(session, staff.slug, ["2026-06-01"])
        requests = list_pto_requests(session)
        assert len(requests) == 2

    def test_filter_by_staff(self, session):
        if not session.query(Property).first():
            _make_property(session)
        s1 = Staff(name="Diego", slug="diego", role=StaffRole.GROUNDSKEEPER)
        s2 = Staff(name="Maria", slug="maria", role=StaffRole.HOUSEKEEPER)
        session.add_all([s1, s2])
        session.flush()
        create_pto_request(session, "diego", ["2026-05-01"])
        create_pto_request(session, "maria", ["2026-05-01"])
        requests = list_pto_requests(session, staff_id_or_slug="diego")
        assert len(requests) == 1
        assert requests[0].staff_id == s1.id

    def test_filter_by_status(self, session):
        staff = _make_staff(session)
        req1 = create_pto_request(session, staff.slug, ["2026-05-01"])
        req2 = create_pto_request(session, staff.slug, ["2026-06-01"])
        with patch("mihomes.services.staff_pto._sync_to_calendar"):
            approve_pto(session, req1.id)
        pending = list_pto_requests(session, status=PTOStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].id == req2.id

    def test_empty_returns_empty_list(self, session):
        assert list_pto_requests(session) == []


class TestGetPTOBalance:
    def test_counts_approved_days(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01", "2026-05-02"])
        with patch("mihomes.services.staff_pto._sync_to_calendar"):
            approve_pto(session, req.id)
        balance = get_pto_balance(session, staff.slug)
        assert balance["approved_days"] == 2
        assert balance["pending_days"] == 0

    def test_counts_pending_days(self, session):
        staff = _make_staff(session)
        create_pto_request(session, staff.slug, ["2026-05-01", "2026-05-02", "2026-05-03"])
        balance = get_pto_balance(session, staff.slug)
        assert balance["pending_days"] == 3
        assert balance["approved_days"] == 0

    def test_only_counts_current_year(self, session):
        staff = _make_staff(session)
        # Mix of this year and last year dates
        create_pto_request(session, staff.slug, ["2026-01-15", "2025-12-31"])
        balance = get_pto_balance(session, staff.slug)
        # Only the 2026 date counts (current year is 2026 per project context)
        assert balance["pending_days"] == 1

    def test_returns_staff_and_year(self, session):
        staff = _make_staff(session)
        balance = get_pto_balance(session, staff.slug)
        assert balance["staff"].id == staff.id
        assert balance["year"] == date.today().year


class TestNotifyApprover:
    def test_returns_false_when_no_phone_configured(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        with patch("mihomes.services.config_service.get_config", return_value=None):
            result = notify_approver(session, req)
        assert result is False

    def test_sends_message_when_phone_configured(self, session):
        staff = _make_staff(session)
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        mock_client = MagicMock()
        with patch("mihomes.services.config_service.get_config", return_value="+17705550100"), \
             patch("mihomes.services.gateways.whatsapp.client.WhatsAppClient", return_value=mock_client):
            result = notify_approver(session, req)
        assert result is True
        mock_client.send_message.assert_called_once()
        call_args = mock_client.send_message.call_args
        assert "+17705550100" in call_args[0]

    def test_includes_coverage_warning_in_message(self, session):
        prop = _make_property(session)
        staff = Staff(name="Maria", slug="maria-staff", role=StaffRole.HOUSEKEEPER)
        session.add(staff)
        session.flush()
        task = Task(
            title="Clean", slug="clean-task", property_id=prop.id,
            assignee_id=staff.id, due_date=date(2026, 5, 1), priority=TaskPriority.MEDIUM,
        )
        session.add(task)
        session.flush()
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        mock_client = MagicMock()
        with patch("mihomes.services.config_service.get_config", return_value="+17705550100"), \
             patch("mihomes.services.gateways.whatsapp.client.WhatsAppClient", return_value=mock_client):
            notify_approver(session, req)
        msg = mock_client.send_message.call_args[0][1]
        assert "⚠️" in msg


class TestNotifyStaff:
    def test_returns_false_when_no_whatsapp(self, session):
        staff = _make_staff(session, whatsapp=None)
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        result = notify_staff(session, req)
        assert result is False

    def test_sends_approval_message(self, session):
        staff = _make_staff(session, whatsapp="+17705550199")
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        req.status = PTOStatus.APPROVED
        mock_client = MagicMock()
        with patch("mihomes.services.gateways.whatsapp.client.WhatsAppClient", return_value=mock_client):
            result = notify_staff(session, req)
        assert result is True
        msg = mock_client.send_message.call_args[0][1]
        assert "approved" in msg.lower()

    def test_sends_denial_message_with_reason(self, session):
        staff = _make_staff(session, whatsapp="+17705550199")
        req = create_pto_request(session, staff.slug, ["2026-05-01"])
        req.status = PTOStatus.DENIED
        req.notes = "Denied: short-staffed"
        mock_client = MagicMock()
        with patch("mihomes.services.gateways.whatsapp.client.WhatsAppClient", return_value=mock_client):
            notify_staff(session, req)
        msg = mock_client.send_message.call_args[0][1]
        assert "denied" in msg.lower()
        assert "short-staffed" in msg


# --------------------------------------------------------------------------- #
# SPEC-006 A21 (F9, Step 8) — notify_staff gains notify_approver's fallback ladder
#
# **Written at module level on purpose**, breaking this file's convention of nesting every
# test in a class. Harness C10: §8 declares the bare node id `test_notify_staff_fallback`,
# a nested name would not resolve under `--collect`, and the pending-set expiry test asserts
# non-resolution — so it could not catch the mistake either.
# --------------------------------------------------------------------------- #
def test_notify_staff_fallback(session):
    """**A21** — on a Telegram-only install, a staff member IS told their PTO was decided.

    F9's bug, stated plainly: `notify_approver` was given a WhatsApp→Telegram ladder under H35
    because a Telegram-only install had a silently dead approval loop. `notify_staff` was left
    WhatsApp-only, so the *other* half stayed dead — PTO was approved and the person who asked
    for it was never told. No error, no failure anybody sees; from their side the request just
    looked unanswered.

    Paired, because "it returns True now" is weak: the assertion is that a **message was
    actually sent, to the configured chat, naming the staff member and the decision**. In a
    group chat an unaddressed "your PTO was approved" is ambiguous between everyone reading it.
    """
    from mihomes.services.config_service import set_config

    staff = _make_staff(session, whatsapp=None)  # Telegram-only install: no phone anywhere
    req = create_pto_request(session, staff.slug, ["2026-05-01"])
    req.status = PTOStatus.APPROVED
    set_config(session, "telegram.pto_approver_id", "-100999")
    session.flush()

    mock_client = MagicMock()
    with patch(
        "mihomes.services.gateways.telegram.responder._get_client",
        return_value=mock_client,
    ):
        result = notify_staff(session, req)

    assert result is True, (
        "a staff member on a Telegram-only install was still not told their PTO was decided"
    )
    assert mock_client.send_message.called, "notify_staff returned True but sent nothing"

    chat_id, msg = mock_client.send_message.call_args[0][:2]
    assert chat_id == "-100999"
    assert staff.name in msg, (
        "the group message must name whose PTO it is — 'your PTO was approved' sent to a "
        "shared chat is ambiguous between everyone in it"
    )
    assert "approved" in msg.lower()


def test_notify_staff_still_prefers_whatsapp_when_a_phone_is_configured(session):
    """The ladder's precedence, unchanged from `notify_approver`'s (H35).

    A configured phone means a WhatsApp install and wins. Without this arm, a ladder that
    always fell through to Telegram would satisfy A21 while breaking every WhatsApp install —
    the regression the fix is most likely to cause.
    """
    from mihomes.services.config_service import set_config

    staff = _make_staff(session, whatsapp="+17705550199")
    req = create_pto_request(session, staff.slug, ["2026-05-01"])
    req.status = PTOStatus.APPROVED
    set_config(session, "telegram.pto_approver_id", "-100999")
    session.flush()

    wa_client, tg_client = MagicMock(), MagicMock()
    with patch(
        "mihomes.services.gateways.whatsapp.client.WhatsAppClient", return_value=wa_client
    ), patch(
        "mihomes.services.gateways.telegram.responder._get_client", return_value=tg_client
    ):
        assert notify_staff(session, req) is True

    assert wa_client.send_message.called
    assert not tg_client.send_message.called, (
        "a WhatsApp install must not also message the Telegram chat — the staff member would "
        "be told twice and the estate's group would see their HR decision"
    )


def test_notify_staff_falls_through_to_telegram_when_whatsapp_raises(session):
    """A WhatsApp install whose bridge is down should still reach a configured chat.

    Deliberately different from `notify_approver`, which returns False on a WhatsApp failure —
    correctly, because the approver has no second address. A staff member does: the estate's
    chat. So the ladder continues rather than stopping.
    """
    from mihomes.services.config_service import set_config

    staff = _make_staff(session, whatsapp="+17705550199")
    req = create_pto_request(session, staff.slug, ["2026-05-01"])
    req.status = PTOStatus.APPROVED
    set_config(session, "telegram.pto_approver_id", "-100999")
    session.flush()

    tg_client = MagicMock()
    with patch(
        "mihomes.services.gateways.whatsapp.client.WhatsAppClient",
        side_effect=RuntimeError("bridge down"),
    ), patch(
        "mihomes.services.gateways.telegram.responder._get_client", return_value=tg_client
    ):
        assert notify_staff(session, req) is True

    assert tg_client.send_message.called


def test_notify_staff_reports_false_when_no_gateway_can_reach_them(session):
    """The honest failure: nobody was told, and the caller is told *that*.

    This is the pre-fix behaviour preserved for the one case where it is correct — no phone and
    no chat configured. It must stay distinguishable from success, because "the staff member
    was not notified" is a condition an operator can act on by configuring a gateway.
    """
    staff = _make_staff(session, whatsapp=None)
    req = create_pto_request(session, staff.slug, ["2026-05-01"])
    req.status = PTOStatus.APPROVED
    session.flush()

    assert notify_staff(session, req) is False
