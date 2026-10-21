"""Daily-workflow UI fixes: tasks, issues, work orders and the dashboard.

Covers the behaviour the templates now depend on: assignment, filters surviving a save,
`?open=`/`?new=` deep links, the issue → work order hand-off, lifecycle actions reopening the
record, urgency sort, the board's Start button, success toasts and the Free-plan due-date field.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from mihomes.models.task import Task, TaskStatus
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.services import issue as issue_svc
from mihomes.services import staff as staff_svc
from mihomes.services import task as task_svc
from mihomes.services import work_order as wo_svc


def _staff_id(client):
    with client._SessionLocal() as s:
        return str(next(m.id for m in staff_svc.list_staff(s) if m.name == "Marcia Staff"))


def _prop_id(client):
    from mihomes.services import property as prop_svc
    with client._SessionLocal() as s:
        return str(prop_svc.list_properties(s)[0].id)


def _toast(resp):
    return resp.headers.get("HX-Trigger", "")


# --- Pages render, including the one-shot deep links ------------------------------------

@pytest.mark.parametrize("path", [
    "/", "/tasks/?new=1", "/issues/?new=1", "/work-orders/?new=1",
    "/tasks/?sort=due", "/tasks/?sort=priority", "/tasks/?sort=newest", "/tasks/?sort=oldest",
    "/tasks/?sort=bogus", "/issues/?severity=high&status=reported&tab=history",
    "/issues/?severity=nonsense",
])
def test_pages_render(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, resp.text[:500]


def test_new_param_asks_the_page_to_open_the_add_modal(client):
    assert 'data-open-new="1"' in client.get("/tasks/?new=1").text
    assert 'data-open-new="1"' not in client.get("/tasks/").text


# --- Tasks ---------------------------------------------------------------------------------

def test_task_create_and_edit_carry_the_assignee(client):
    pid, sid = _prop_id(client), _staff_id(client)
    resp = client.post("/tasks/", data={"title": "Clean gutters", "property_id": pid,
                                        "assignee_id": sid})
    assert resp.status_code == 200
    assert "Task added" in _toast(resp)
    with client._SessionLocal() as s:
        task = s.query(Task).filter_by(title="Clean gutters").one()
        assert str(task.assignee_id) == sid
        slug = task.slug

    # The form's "Unassigned" option unassigns; a post without the field leaves it alone.
    client.post(f"/tasks/{slug}/edit", data={"title": "Clean gutters", "priority": "medium"})
    with client._SessionLocal() as s:
        assert str(s.query(Task).filter_by(slug=slug).one().assignee_id) == sid
    client.post(f"/tasks/{slug}/edit", data={"title": "Clean gutters", "priority": "medium",
                                             "assignee_id": "none"})
    with client._SessionLocal() as s:
        assert s.query(Task).filter_by(slug=slug).one().assignee_id is None


def test_task_open_param_and_add_form_assignee_select(client):
    pid = _prop_id(client)
    with client._SessionLocal() as s:
        task = task_svc.create_task(s, "Check smoke alarms", pid)
        s.commit()
        tid = str(task.id)
    html = client.get(f"/tasks/?open={tid}").text
    assert f'data-open-id="{tid}"' in html
    assert 'name="assignee_id"' in html and "Unassigned" in html
    # Property is a deliberate choice: placeholder selected, nothing preselected without a filter.
    assert 'value="" disabled selected>Select property' in html


def test_board_start_moves_to_in_progress(client):
    pid = _prop_id(client)
    with client._SessionLocal() as s:
        slug = task_svc.create_task(s, "Service boiler", pid).slug
        s.commit()
    resp = client.post(f"/tasks/{slug}/start")
    assert resp.status_code == 200
    assert "Task started" in _toast(resp)
    with client._SessionLocal() as s:
        assert s.query(Task).filter_by(slug=slug).one().status == TaskStatus.IN_PROGRESS
    # Not twice: only a to-do task can be started.
    assert client.post(f"/tasks/{slug}/start").status_code == 400


def test_urgency_is_the_default_sort(client):
    pid = _prop_id(client)
    today = date.today()
    with client._SessionLocal() as s:
        task_svc.create_task(s, "Undated job", pid)
        task_svc.create_task(s, "Due next month", pid, due_date=today + timedelta(days=30))
        task_svc.create_task(s, "Due tomorrow", pid, due_date=today + timedelta(days=1))
        task_svc.create_task(s, "Already late", pid, due_date=today - timedelta(days=3))
        s.commit()
    html = client.get("/tasks/").text
    view = html[html.index('id="view-list"'):]
    order = [view.index(t) for t in ("Already late", "Due tomorrow", "Due next month", "Undated job")]
    assert order == sorted(order)


def test_a_save_keeps_the_page_filters(client):
    pid = _prop_id(client)
    with client._SessionLocal() as s:
        slug = task_svc.create_task(s, "Filtered task", pid).slug
        s.commit()
    resp = client.post(
        f"/tasks/{slug}/delete",
        headers={"HX-Request": "true",
                 "HX-Current-URL": f"http://testserver/tasks/?property_id={pid}&sort=due&open=x"},
    )
    assert resp.status_code == 200
    # The property filter is still selected and the sort still applied; `open` was not carried.
    assert f'value="{pid}" selected' in resp.text
    assert 'value="due" selected' in resp.text
    assert "data-open-id" not in resp.text


# --- Issues ----------------------------------------------------------------------------------

def test_issue_modal_links_to_its_work_orders_and_offers_one(client):
    pid = _prop_id(client)
    with client._SessionLocal() as s:
        issue = issue_svc.create_issue(s, "Leaking tap", pid)
        wo = wo_svc.create_work_order(s, "Fix tap", pid)
        wo_svc.update_work_order(s, wo.slug, issue_id=issue.id)
        s.commit()
        iid, wid = str(issue.id), str(wo.id)
    html = client.get(f"/issues/?open={iid}").text
    assert f'href="/work-orders/?open={wid}"' in html
    assert f'href="/work-orders/?new=1&issue={iid}"' in html
    assert f'data-open-id="{iid}"' in html


def test_issue_filters_and_toasts(client):
    pid = _prop_id(client)
    with client._SessionLocal() as s:
        issue_svc.create_issue(s, "Cracked tile", pid, severity=issue_svc.IssueSeverity.LOW)
        issue_svc.create_issue(s, "Gas smell", pid, severity=issue_svc.IssueSeverity.CRITICAL)
        s.commit()
    html = client.get("/issues/?severity=critical").text
    assert "Gas smell" in html and "Cracked tile" not in html

    resp = client.post("/issues/", data={"title": "Broken gate", "property_id": pid},
                       headers={"HX-Request": "true",
                                "HX-Current-URL": "http://testserver/issues/?severity=critical"})
    assert "Issue reported" in _toast(resp)
    assert 'value="critical" selected' in resp.text


def test_resolving_twice_is_a_message_not_a_crash(client):
    pid = _prop_id(client)
    with client._SessionLocal() as s:
        issue = issue_svc.create_issue(s, "Loose rail", pid)
        issue_svc.update_issue(s, issue.slug, status=issue_svc.IssueStatus.VERIFIED)
        s.commit()
        slug = issue.slug
    resp = client.post(f"/issues/{slug}/resolve")
    assert resp.status_code == 400
    assert "already verified" in resp.text


# --- Work orders -----------------------------------------------------------------------------

def test_create_work_order_from_an_issue_is_prefilled(client):
    pid = _prop_id(client)
    with client._SessionLocal() as s:
        issue = issue_svc.create_issue(s, "Damp wall", pid)
        resolved = issue_svc.create_issue(s, "Old fixed thing", pid)
        issue_svc.resolve_issue(s, resolved.slug)
        s.commit()
        iid = str(issue.id)
    html = client.get(f"/work-orders/?new=1&issue={iid}").text
    assert 'data-open-new="1"' in html
    assert 'value="Damp wall"' in html
    assert f'<option value="{iid}" selected>' in html
    # Resolved issues are not offered for linking.
    assert "Old fixed thing" not in html


def test_lifecycle_keeps_the_record_open(client):
    pid = _prop_id(client)
    with client._SessionLocal() as s:
        wo = wo_svc.create_work_order(s, "Paint fence", pid, estimated_cost=100)
        s.commit()
        slug, wid = wo.slug, str(wo.id)
    resp = client.post(f"/work-orders/{slug}/approve",
                       headers={"HX-Request": "true",
                                "HX-Current-URL": "http://testserver/work-orders/?status=draft"})
    assert "Work order approved" in _toast(resp)
    # Filtered to Draft, the approved WO's row is gone — but its modal is rendered and reopened.
    assert f'data-open-id="{wid}"' in resp.text
    assert f'id="wo-modal-{wid}"' in resp.text


def test_assigning_an_approved_work_order_moves_it_to_assigned(client):
    pid, sid = _prop_id(client), _staff_id(client)
    with client._SessionLocal() as s:
        wo = wo_svc.create_work_order(s, "Trim hedge", pid)
        wo_svc.approve(s, wo.slug)
        s.commit()
        slug, wid = wo.slug, wo.id
    resp = client.post(f"/work-orders/{slug}/edit",
                       data={"title": "Trim hedge", "status": "approved", "assignee_id": sid})
    assert resp.status_code == 200
    with client._SessionLocal() as s:
        wo = s.get(WorkOrder, wid)
        assert str(wo.assignee_id) == sid
        assert wo.status == WorkOrderStatus.ASSIGNED


def test_work_order_create_with_assignee(client):
    sid = _staff_id(client)
    from mihomes.services import property as prop_svc
    with client._SessionLocal() as s:
        pslug = prop_svc.list_properties(s)[0].slug
    resp = client.post("/work-orders/", data={"title": "Fix door", "property_slug": pslug,
                                              "assignee_id": sid})
    assert "Work order created" in _toast(resp)
    with client._SessionLocal() as s:
        assert str(s.query(WorkOrder).filter_by(title="Fix door").one().assignee_id) == sid


def test_due_date_is_disabled_with_a_pro_badge_on_free(client, account_a):
    html = client.get("/work-orders/").text
    assert "Scheduling is included in Pro" not in html
    with client._SessionLocal() as s:
        s.execute(text("UPDATE accounts SET plan = 'free' WHERE id = :id"), {"id": account_a})
        s.commit()
    html = client.get("/work-orders/").text
    assert "Scheduling is included in Pro" in html
    assert 'name="due_date" value="" disabled' in html


# --- Dashboard -------------------------------------------------------------------------------

def test_dashboard_rows_link_to_records(client):
    pid = _prop_id(client)
    today = date.today()
    with client._SessionLocal() as s:
        task = task_svc.create_task(s, "Late chore", pid, due_date=today - timedelta(days=2))
        issue = issue_svc.create_issue(s, "Burst pipe", pid, severity=issue_svc.IssueSeverity.CRITICAL)
        wo = wo_svc.create_work_order(s, "Replace pipe", pid)
        wo_svc.approve(s, wo.slug)
        wo_svc.transition_status(s, wo.slug, WorkOrderStatus.IN_PROGRESS)
        for i in range(12):
            task_svc.create_task(s, f"Soon {i}", pid, due_date=today + timedelta(days=1))
        s.commit()
        tid, iid, wid = task.id, issue.id, wo.id
    html = client.get("/").text
    assert f'href="/tasks/?open={tid}"' in html
    assert f'href="/issues/?open={iid}"' in html
    assert f'href="/work-orders/?open={wid}"' in html
    assert 'href="/issues/?new=1"' in html and 'href="/tasks/?new=1"' in html
    # Due This Week shows the real count, not the 10-row cap.
    assert ">12<" in html.replace(" ", "").replace("\n", "")
