"""Tests for staff PTO service."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.models.staff import Staff, StaffRole
from mihomes.models.staff_pto import PTOStatus, StaffPTORequest
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
    prop = _make_property(session) if not session.query(Property).first() else session.query(Property).first()
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
            approve_pto(session, 9999)


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
            deny_pto(session, 9999)


class TestListPTORequests:
    def test_list_all(self, session):
        staff = _make_staff(session)
        create_pto_request(session, staff.slug, ["2026-05-01"])
        create_pto_request(session, staff.slug, ["2026-06-01"])
        requests = list_pto_requests(session)
        assert len(requests) == 2

    def test_filter_by_staff(self, session):
        prop = session.query(Property).first() or _make_property(session)
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
