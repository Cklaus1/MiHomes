"""Work order service regression tests — H22 (cost validation before mutating),
H23 (issue↔WO link convergence)."""

import uuid

import pytest

from mihomes.models.issue import Issue, IssueStatus
from mihomes.models.property import Property, PropertyType
from mihomes.models.work_order import WorkOrderStatus
from mihomes.services.work_order import (
    approve,
    complete,
    create_work_order,
    list_work_orders_by_issue,
    verify,
)

# Placeholder ids for polymorphic entity_type/entity_id pairs. Distinct
# constants because several tests rely on two ids being DIFFERENT (filter by
# one, assert the other is excluded) — a single shared UUID would make those
# tests pass for the wrong reason. Were integers before SPEC-002 D2.
_ENTITY_999 = uuid.uuid4()


@pytest.fixture
def prop(session):
    p = Property(name="WO House", slug="wo-house", property_type=PropertyType.PRIMARY)
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def issue(session, prop):
    i = Issue(title="Leaky roof", slug="leaky-roof", property_id=prop.id,
              status=IssueStatus.REPORTED)
    session.add(i)
    session.flush()
    return i


class TestIssueLinkConverged:
    """H23 — the issue↔WO link converges on `issue_id`. A WO created with an
    `issue:<id>` source (the CLI path) must populate `issue_id` so that both
    `list_work_orders_by_issue` finds it AND `verify()` advances the issue."""

    def test_source_issue_populates_issue_id(self, session, prop, issue):
        wo = create_work_order(session, "Fix roof", str(prop.id),
                               source_type="issue", source_id=issue.id)
        session.flush()
        assert wo.issue_id == issue.id

    def test_list_by_issue_finds_source_created_wo(self, session, prop, issue):
        wo = create_work_order(session, "Fix roof", str(prop.id),
                               source_type="issue", source_id=issue.id)
        session.flush()
        found = list_work_orders_by_issue(session, issue.id)
        assert wo.id in [w.id for w in found]

    def test_verify_advances_issue_for_source_created_wo(self, session, prop, issue):
        wo = create_work_order(session, "Fix roof", str(prop.id),
                               source_type="issue", source_id=issue.id,
                               estimated_cost=100.0)
        approve(session, str(wo.id))
        complete(session, str(wo.id))
        verify(session, str(wo.id))
        session.refresh(issue)
        assert issue.status == IssueStatus.VERIFIED

    def test_task_source_does_not_set_issue_id(self, session, prop):
        wo = create_work_order(session, "Do task", str(prop.id),
                               source_type="task", source_id=_ENTITY_999)
        session.flush()
        assert wo.issue_id is None


class TestCompleteRequiresCost:
    def test_complete_requires_cost(self, session, prop):
        """H22 — completing a work order with neither estimated nor actual cost
        must raise BEFORE any mutation. Previously status/completed_at were set,
        then the raise left the WO wedged in COMPLETED with no transaction."""
        wo = create_work_order(session, "No cost job", str(prop.id))
        approve(session, str(wo.id))
        session.flush()

        with pytest.raises(ValueError):
            complete(session, str(wo.id), actual_cost=None)

        session.refresh(wo)
        # The failed complete must not have advanced the status.
        assert wo.status != WorkOrderStatus.COMPLETED
        assert wo.completed_at is None

    def test_complete_with_actual_cost_succeeds(self, session, prop):
        wo = create_work_order(session, "Costed job", str(prop.id))
        approve(session, str(wo.id))
        session.flush()
        complete(session, str(wo.id), actual_cost=250.0)
        session.refresh(wo)
        assert wo.status == WorkOrderStatus.COMPLETED
        assert wo.actual_cost == 250.0

    def test_complete_with_estimate_only_succeeds(self, session, prop):
        wo = create_work_order(session, "Estimated job", str(prop.id),
                               estimated_cost=99.0)
        approve(session, str(wo.id))
        session.flush()
        complete(session, str(wo.id))
        session.refresh(wo)
        assert wo.status == WorkOrderStatus.COMPLETED
