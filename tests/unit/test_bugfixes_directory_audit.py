"""Regression tests for the 2026-06 bug-fix batch (audit findings)."""

from datetime import date, timedelta

import pytest

from mihomes.models.issue import Issue, IssueStatus
from mihomes.models.property import Property, PropertyType
from mihomes.models.staff import Staff, StaffRole
from mihomes.models.staff_pto import PTOStatus, StaffPTORequest
from mihomes.models.task import Task, TaskPriority, TaskStatus
from mihomes.models.work_order import WorkOrder, WorkOrderStatus


@pytest.fixture
def db(session):
    """Ensure FK enforcement is active for these tests (conftest sets the pragma)."""
    return session


# --- #2: deleting a person no longer crashes on dependent rows -------------

def test_delete_staff_clears_all_references(db):
    prop = Property(name="Belle", slug="belle", property_type=PropertyType.PRIMARY)
    db.add(prop)
    db.flush()
    m = Staff(name="Diego", slug="diego", role=StaffRole.GROUNDSKEEPER)
    db.add(m)
    db.flush()

    # Every kind of reference to staff.id
    db.add(Task(title="Mow", slug="mow", property_id=prop.id, assignee_id=m.id,
                priority=TaskPriority.MEDIUM, status=TaskStatus.PENDING))
    db.add(WorkOrder(title="Fix gate", slug="fix-gate", property_id=prop.id,
                     assignee_id=m.id, status=WorkOrderStatus.DRAFT))
    db.add(Issue(title="Leak", slug="leak", property_id=prop.id,
                 status=IssueStatus.REPORTED, reported_by_id=m.id, resolved_by_id=m.id))
    db.add(StaffPTORequest(staff_id=m.id, dates=["2026-07-01"], status=PTOStatus.PENDING))
    db.flush()

    from mihomes.services import staff as staff_svc
    # Previously raised IntegrityError because the PTO row's NOT NULL staff_id
    # was left dangling.
    staff_svc.delete_staff(db, "diego")
    db.flush()

    assert db.query(Staff).filter_by(slug="diego").first() is None
    assert db.query(StaffPTORequest).filter_by(staff_id=m.id).count() == 0
    assert db.query(Task).filter_by(slug="mow").one().assignee_id is None
    assert db.query(WorkOrder).filter_by(slug="fix-gate").one().assignee_id is None
    iss = db.query(Issue).filter_by(slug="leak").one()
    assert iss.reported_by_id is None and iss.resolved_by_id is None


# --- #5: the AI query_staff tool returns employees only --------------------

def test_query_staff_tool_excludes_non_staff(db):
    db.add(Staff(name="Marcia", slug="marcia", role=StaffRole.HOUSEKEEPER))
    db.add(Staff(name="Rita", slug="rita", role=StaffRole.RESIDENT))
    db.flush()

    from mihomes.services.ai.tools import _query_staff
    out = _query_staff(db, {})
    assert "Marcia" in out
    assert "Rita" not in out


# --- #6: search labels residents/owners by category ------------------------

def test_search_labels_people_by_category(db):
    db.add(Staff(name="Marcia", slug="marcia", role=StaffRole.HOUSEKEEPER))
    db.add(Staff(name="Rita", slug="rita", role=StaffRole.RESIDENT))
    db.add(Staff(name="Owner Pat", slug="owner-pat", role=StaffRole.OWNER))
    db.flush()

    from mihomes.services.search import global_search
    by_name = {r["name"]: r["type"] for r in global_search(db, "a")}
    assert by_name.get("Marcia") == "staff"
    assert by_name.get("Rita") == "resident"
    assert by_name.get("Owner Pat") == "family"


# --- #8: daily digest includes a 'due this week' section -------------------

def test_digest_due_this_week(db):
    prop = Property(name="Belle", slug="belle", property_type=PropertyType.PRIMARY)
    db.add(prop)
    db.flush()
    db.add(Task(title="Service HVAC", slug="svc-hvac", property_id=prop.id,
                priority=TaskPriority.MEDIUM, status=TaskStatus.PENDING,
                due_date=date.today() + timedelta(days=3)))
    db.flush()

    from mihomes.services.automation import format_digest_brief, generate_daily_digest
    digest = generate_daily_digest(db)
    assert any(t["title"] == "Service HVAC" for t in digest["due_this_week"])
    assert "DUE THIS WEEK" in format_digest_brief(digest)


# --- #4: path-traversal validation (no DB) ---------------------------------

def test_validate_file_path_traversal():
    from mihomes.services.document import _validate_file_path

    with pytest.raises(ValueError):
        _validate_file_path("../../etc/passwd")
    with pytest.raises(ValueError):
        _validate_file_path("docs/../../secret")
    # No false positive on a filename that merely contains ".."
    _validate_file_path("reports/quarterly..final.pdf")


# --- #7: tolerant money parsing (no DB) ------------------------------------

def test_parse_money():
    from mihomes.web.forms import parse_money

    assert parse_money("$1,200.50", "Amount") == 1200.50
    assert parse_money("  42 ") == 42.0
    assert parse_money("") is None
    assert parse_money(None) is None
    with pytest.raises(ValueError) as exc:
        parse_money("abc", "Amount")
    assert "Amount" in str(exc.value)
