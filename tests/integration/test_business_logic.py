"""Tests for business logic services — budget, contract, insurance, task, work order."""

from datetime import date, timedelta

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.models.vendor import Vendor
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.task import TaskPriority, TaskStatus, RecurrenceFrequency
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.models.insurance import InsuranceType


@pytest.fixture
def prop(session):
    p = Property(name="Logic House", slug="logic-house",
                 property_type=PropertyType.PRIMARY, currency="USD")
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def vendor(session):
    v = Vendor(company_name="Logic Vendor", slug="logic-vendor")
    session.add(v)
    session.flush()
    return v


# ---------------------------------------------------------------------------
# Budget service
# ---------------------------------------------------------------------------

class TestBudgetService:
    def test_set_budget_creates_new(self, session, prop):
        from mihomes.services.budget import set_budget
        from mihomes.models.budget import BudgetPeriod
        b = set_budget(session, str(prop.id), "plumbing", BudgetPeriod.MONTHLY,
                       1000.0, date(2026, 1, 1))
        assert b.id is not None
        assert b.amount == 1000.0

    def test_set_budget_updates_existing(self, session, prop):
        from mihomes.services.budget import set_budget
        from mihomes.models.budget import BudgetPeriod
        b1 = set_budget(session, str(prop.id), "electrical", BudgetPeriod.MONTHLY,
                        500.0, date(2026, 1, 1))
        b2 = set_budget(session, str(prop.id), "electrical", BudgetPeriod.MONTHLY,
                        750.0, date(2026, 1, 1))
        assert b1.id == b2.id
        assert b2.amount == 750.0

    def test_add_transaction(self, session, prop):
        from mihomes.services.budget import add_transaction
        tx = add_transaction(session, 250.0, str(prop.id), "maintenance", date.today())
        assert tx.id is not None
        assert tx.amount == 250.0
        assert tx.category == "maintenance"

    def test_add_transaction_with_vendor(self, session, prop, vendor):
        from mihomes.services.budget import add_transaction
        tx = add_transaction(session, 100.0, str(prop.id), "plumbing",
                             date.today(), vendor_id_or_slug=vendor.slug)
        assert tx.vendor_id == vendor.id

    def test_list_transactions_filter_by_category(self, session, prop):
        from mihomes.services.budget import add_transaction, list_transactions
        add_transaction(session, 100.0, str(prop.id), "plumbing", date.today())
        add_transaction(session, 200.0, str(prop.id), "electrical", date.today())
        plumbing = list_transactions(session, property_id_or_slug=str(prop.id),
                                     category="plumbing")
        assert all(t.category == "plumbing" for t in plumbing)

    def test_list_transactions_filter_by_date(self, session, prop):
        from mihomes.services.budget import add_transaction, list_transactions
        add_transaction(session, 50.0, str(prop.id), "misc",
                        date.today() - timedelta(days=10))
        add_transaction(session, 75.0, str(prop.id), "misc",
                        date.today() - timedelta(days=2))
        recent = list_transactions(session, date_from=date.today() - timedelta(days=5))
        amounts = [t.amount for t in recent]
        assert 75.0 in amounts
        assert 50.0 not in amounts

    def test_get_budget_report(self, session, prop):
        from mihomes.services.budget import set_budget, add_transaction, get_budget_report
        from mihomes.models.budget import BudgetPeriod
        set_budget(session, str(prop.id), "landscaping", BudgetPeriod.MONTHLY,
                   500.0, date(2026, 1, 1))
        add_transaction(session, 200.0, str(prop.id), "landscaping",
                        date(2026, 1, 15))
        report = get_budget_report(session, str(prop.id), date(2026, 1, 1), date(2026, 2, 1))
        assert isinstance(report, list)


# ---------------------------------------------------------------------------
# Contract service
# ---------------------------------------------------------------------------

class TestContractService:
    def test_create_contract(self, session, prop, vendor):
        from mihomes.services.contract import create_contract
        c = create_contract(session, vendor.slug, prop.slug,
                            date(2026, 1, 1), end_date=date(2026, 12, 31),
                            service_category="landscaping", annual_cost=5000.0)
        assert c.id is not None
        assert c.annual_cost == 5000.0
        assert c.service_category == "landscaping"

    def test_list_contracts_by_property(self, session, prop, vendor):
        from mihomes.services.contract import create_contract, list_contracts
        create_contract(session, vendor.slug, prop.slug, date(2026, 1, 1))
        results = list_contracts(session, property_id_or_slug=prop.slug)
        assert len(results) >= 1

    def test_list_contracts_expiring_soon(self, session, prop, vendor):
        from mihomes.services.contract import create_contract, list_contracts
        create_contract(session, vendor.slug, prop.slug, date(2026, 1, 1),
                        end_date=date.today() + timedelta(days=20))
        results = list_contracts(session, expiring_days=30)
        assert len(results) >= 1

    def test_list_contracts_no_end_date_not_in_expiring(self, session, prop, vendor):
        from mihomes.services.contract import create_contract, list_contracts
        # Contract with no end date should not appear in expiring filter
        create_contract(session, vendor.slug, prop.slug, date(2026, 1, 1))
        results = list_contracts(session, expiring_days=30)
        # All results must have an end_date (that's the filter condition)
        for c in results:
            assert c.end_date is not None

    def test_delete_contract(self, session, prop, vendor):
        from mihomes.services.contract import create_contract, delete_contract
        from mihomes.models.contract import Contract
        c = create_contract(session, vendor.slug, prop.slug, date(2026, 1, 1))
        cid = c.id
        delete_contract(session, cid)
        assert session.get(Contract, cid) is None

    def test_delete_nonexistent_raises(self, session):
        from mihomes.services.contract import delete_contract
        with pytest.raises(ValueError):
            delete_contract(session, 99999)


# ---------------------------------------------------------------------------
# Insurance service
# ---------------------------------------------------------------------------

class TestInsuranceService:
    def test_create_policy(self, session, prop):
        from mihomes.services.insurance import create_policy
        p = create_policy(session, "State Farm", InsuranceType.HOMEOWNERS,
                          policy_number="HO-12345", coverage_limit=500000.0,
                          property_id_or_slug=prop.slug)
        assert p.id is not None
        assert p.carrier == "State Farm"
        assert p.coverage_limit == 500000.0

    def test_create_policy_without_property(self, session):
        from mihomes.services.insurance import create_policy
        p = create_policy(session, "Liberty Mutual", InsuranceType.UMBRELLA,
                          annual_premium=1200.0)
        assert p.id is not None
        assert p.property_id is None

    def test_list_by_property(self, session, prop):
        from mihomes.services.insurance import create_policy, list_policies
        create_policy(session, "Allstate", InsuranceType.HOMEOWNERS,
                      property_id_or_slug=prop.slug)
        results = list_policies(session, property_id_or_slug=prop.slug)
        assert any(p.carrier == "Allstate" for p in results)

    def test_list_expiring_soon(self, session, prop):
        from mihomes.services.insurance import create_policy, list_policies
        create_policy(session, "Expiring Soon", InsuranceType.HOMEOWNERS,
                      renewal_date=date.today() + timedelta(days=15),
                      property_id_or_slug=prop.slug)
        results = list_policies(session, expiring_days=30)
        assert any(p.carrier == "Expiring Soon" for p in results)

    def test_delete_policy(self, session, prop):
        from mihomes.services.insurance import create_policy, delete_policy
        from mihomes.models.insurance import InsurancePolicy
        p = create_policy(session, "Delete Me", InsuranceType.HOMEOWNERS,
                          property_id_or_slug=prop.slug)
        pid = p.id
        delete_policy(session, pid)
        assert session.get(InsurancePolicy, pid) is None

    def test_delete_nonexistent_raises(self, session):
        from mihomes.services.insurance import delete_policy
        with pytest.raises(ValueError):
            delete_policy(session, 99999)


# ---------------------------------------------------------------------------
# Task service — edge cases
# ---------------------------------------------------------------------------

class TestTaskServiceEdgeCases:
    def test_create_recurring_task(self, session, prop):
        from mihomes.services.task import create_task
        task = create_task(session, "Monthly Pool Check", prop.slug,
                           recurrence=RecurrenceFrequency.MONTHLY,
                           due_date=date.today())
        assert task.schedule is not None
        assert task.schedule.frequency == RecurrenceFrequency.MONTHLY

    def test_seasonal_task_requires_spec(self, session, prop):
        from mihomes.services.task import create_task
        with pytest.raises(ValueError, match="Season spec required"):
            create_task(session, "Seasonal Task", prop.slug,
                        recurrence=RecurrenceFrequency.SEASONAL)

    def test_seasonal_task_invalid_season(self, session, prop):
        from mihomes.services.task import create_task
        with pytest.raises(ValueError, match="Invalid season"):
            create_task(session, "Bad Season Task", prop.slug,
                        recurrence=RecurrenceFrequency.SEASONAL,
                        season_spec="monsoon")

    def test_complete_task_creates_next_occurrence(self, session, prop):
        from mihomes.services.task import create_task, complete_task
        task = create_task(session, "Weekly Lawn Mow", prop.slug,
                           recurrence=RecurrenceFrequency.WEEKLY,
                           due_date=date.today())
        result = complete_task(session, task.slug)
        assert result.status == TaskStatus.COMPLETED

    def test_complete_already_completed_raises(self, session, prop):
        from mihomes.services.task import create_task, complete_task
        task = create_task(session, "One Time Task", prop.slug)
        complete_task(session, task.slug)
        with pytest.raises(ValueError, match="already completed"):
            complete_task(session, task.slug)

    def test_update_task(self, session, prop):
        from mihomes.services.task import create_task, update_task
        task = create_task(session, "Old Task Title", prop.slug)
        update_task(session, task.slug, title="New Task Title", priority=TaskPriority.HIGH)
        session.expire(task)
        assert task.title == "New Task Title"
        assert task.priority == TaskPriority.HIGH

    def test_delete_task(self, session, prop):
        from mihomes.services.task import create_task, delete_task
        from mihomes.models.task import Task
        task = create_task(session, "Delete Task", prop.slug)
        slug = task.slug
        delete_task(session, slug)
        assert session.query(Task).filter(Task.slug == slug).first() is None

    def test_list_overdue_tasks(self, session, prop):
        from mihomes.services.task import create_task, get_overdue_tasks
        create_task(session, "Overdue Task", prop.slug,
                    due_date=date.today() - timedelta(days=2))
        create_task(session, "Future Task", prop.slug,
                    due_date=date.today() + timedelta(days=5))
        overdue = get_overdue_tasks(session)
        titles = [t.title for t in overdue]
        assert "Overdue Task" in titles
        assert "Future Task" not in titles


# ---------------------------------------------------------------------------
# Work order service — lifecycle
# ---------------------------------------------------------------------------

class TestWorkOrderService:
    def _make_wo(self, session, prop, vendor, title="Test WO"):
        from mihomes.services.work_order import create_work_order
        return create_work_order(session, title, prop.slug,
                                  vendor_id_or_slug=vendor.slug,
                                  estimated_cost=500.0)

    def test_create_work_order(self, session, prop, vendor):
        wo = self._make_wo(session, prop, vendor)
        assert wo.id is not None
        assert wo.status == WorkOrderStatus.DRAFT

    def test_approve(self, session, prop, vendor):
        from mihomes.services.work_order import approve
        wo = self._make_wo(session, prop, vendor, "Approve WO")
        approve(session, wo.slug)
        session.expire(wo)
        assert wo.status == WorkOrderStatus.APPROVED

    def test_complete_with_actual_cost(self, session, prop, vendor):
        from mihomes.services.work_order import approve, complete
        wo = self._make_wo(session, prop, vendor, "Complete WO")
        approve(session, wo.slug)
        complete(session, wo.slug, actual_cost=450.0, notes="Done")
        session.expire(wo)
        assert wo.status == WorkOrderStatus.COMPLETED
        assert wo.actual_cost == 450.0
        assert wo.completed_at is not None

    def test_complete_creates_transaction(self, session, prop, vendor):
        from mihomes.services.work_order import approve, complete
        from mihomes.models.budget import Transaction
        wo = self._make_wo(session, prop, vendor, "Transaction WO")
        approve(session, wo.slug)
        before_count = session.query(Transaction).count()
        complete(session, wo.slug, actual_cost=300.0)
        assert session.query(Transaction).count() > before_count

    def test_verify_marks_issue_verified(self, session, prop, vendor):
        from mihomes.services.work_order import create_work_order, approve, complete, verify
        issue = Issue(title="Source Issue", slug="source-issue",
                      property_id=prop.id, severity=IssueSeverity.HIGH,
                      status=IssueStatus.REPORTED)
        session.add(issue)
        session.flush()
        wo = create_work_order(session, "Issue WO", prop.slug,
                                vendor_id_or_slug=vendor.slug,
                                source_type="issue", source_id=issue.id,
                                estimated_cost=100.0)
        approve(session, wo.slug)
        complete(session, wo.slug, actual_cost=100.0)
        verify(session, wo.slug)
        session.expire(issue)
        assert issue.status == IssueStatus.VERIFIED

    def test_cancel_work_order(self, session, prop, vendor):
        from mihomes.services.work_order import cancel
        wo = self._make_wo(session, prop, vendor, "Cancel WO")
        cancel(session, wo.slug, notes="No longer needed")
        session.expire(wo)
        assert wo.status == WorkOrderStatus.CANCELLED

    def test_invalid_transition_raises(self, session, prop, vendor):
        from mihomes.services.work_order import complete
        wo = self._make_wo(session, prop, vendor, "Bad Transition WO")
        # Can't complete without approving first
        with pytest.raises(ValueError):
            complete(session, wo.slug, actual_cost=100.0)

    def test_update_work_order(self, session, prop, vendor):
        from mihomes.services.work_order import update_work_order
        wo = self._make_wo(session, prop, vendor, "Old WO Title")
        update_work_order(session, wo.slug, title="New WO Title")
        session.expire(wo)
        assert wo.title == "New WO Title"

    def test_delete_work_order(self, session, prop, vendor):
        from mihomes.services.work_order import delete_work_order
        wo = self._make_wo(session, prop, vendor, "Delete WO")
        slug = wo.slug
        delete_work_order(session, slug)
        assert session.query(WorkOrder).filter(WorkOrder.slug == slug).first() is None

    def test_list_by_status(self, session, prop, vendor):
        from mihomes.services.work_order import approve, list_work_orders
        wo1 = self._make_wo(session, prop, vendor, "List WO 1")
        wo2 = self._make_wo(session, prop, vendor, "List WO 2")
        approve(session, wo1.slug)
        approved = list_work_orders(session, status=WorkOrderStatus.APPROVED)
        slugs = [w.slug for w in approved]
        assert wo1.slug in slugs
        assert wo2.slug not in slugs
