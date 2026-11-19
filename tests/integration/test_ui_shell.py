"""The app shell's UX contract (UI/UX audit 2026-10-21).

What these pin, each one a defect the audit found live or in code:

- An HTMX request from an expired session got a JSON 401 that htmx 2 discards, so every click
  silently did nothing. It now also carries `HX-Redirect` to sign-in, back to the page the user
  was on.
- Search results were `<li onclick>` rows (no keyboard, no middle-click) that went to the list
  page rather than the record, and the query was not URL-encoded in links.
- After resolving an alert, the header count and the sidebar badge stayed stale.
- Plan & billing is owner-only, so only owners see the link to it.
"""

from __future__ import annotations

import uuid

from mihomes.models.alert import Alert, AlertSeverity
from mihomes.services import property as prop_svc
from mihomes.services import task as task_svc


def test_expired_session_htmx_gets_redirect_to_sign_in(web_client_as):
    client = web_client_as("owner")
    client.cookies.clear()
    resp = client.post(
        "/tasks/",
        headers={"HX-Request": "true", "HX-Current-URL": "http://localhost/tasks/?overdue=true"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    # Still JSON for any non-browser caller; the header is what htmx follows.
    assert resp.json()["detail"]
    assert resp.headers["HX-Redirect"] == "/login?next=%2Ftasks%2F%3Foverdue%3Dtrue"


def test_expired_session_browser_navigation_is_unchanged(web_client_as):
    client = web_client_as("owner")
    client.cookies.clear()
    resp = client.get("/tasks/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "HX-Redirect" not in resp.headers


def test_shell_loads_shared_script_once_from_head_and_has_favicon(web_client_as):
    html = web_client_as("owner").get("/tasks/").text
    head = html.split("</head>")[0]
    assert '<script src="/static/app.js"></script>' in head
    assert 'rel="icon"' in head
    # The inline drawer/search script that re-ran on every body swap is gone.
    assert "function previewDoc" not in html


def test_nav_marks_current_page_and_hides_billing_from_non_owners(web_client_as):
    owner_html = web_client_as("owner").get("/tasks/").text
    assert 'aria-current="page"' in owner_html
    assert "Plan &amp; billing" in owner_html or "Plan & billing" in owner_html

    admin_html = web_client_as("admin").get("/tasks/").text
    assert "Plan &amp; billing" not in admin_html and "Plan & billing" not in admin_html


def test_search_results_are_links_to_the_record(web_client_as):
    marker = f"Gutter{uuid.uuid4().hex[:6]}"
    made = {}

    def _seed(s):
        prop = prop_svc.create_property(s, f"Search Manor {marker}")
        made["task"] = task_svc.create_task(s, f"Clean {marker}", str(prop.id))
        s.flush()
        made["task_id"] = made["task"].id

    client = web_client_as("owner")
    web_client_as.seed(_seed)

    page = client.get(f"/search/?q={marker}").text
    assert f'href="/tasks/?open={made["task_id"]}"' in page
    assert "onclick=\"window.location" not in page

    dropdown = client.get(f"/search/dropdown?q={marker}").text
    assert f'href="/tasks/?open={made["task_id"]}"' in dropdown


def test_search_links_encode_the_query(web_client_as):
    page = web_client_as("owner").get("/search/?q=a%26b").text
    assert "q=a%26b&amp;type=" in page or "q=a%26b&type=" in page


def test_alert_action_refreshes_count_and_badge(web_client_as):
    made = {}

    def _seed(s):
        alert = Alert(alert_type="overdue_task", message="Filter overdue",
                      severity=AlertSeverity.HIGH)
        s.add(alert)
        s.flush()
        made["id"] = alert.id

    client = web_client_as("owner")
    web_client_as.seed(_seed)

    page = client.get("/alerts/").text
    assert 'id="alert-count"' in page and "Acknowledge" in page and "Ack\n" not in page

    resp = client.post(f"/alerts/{made['id']}/resolve", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert 'hx-swap-oob="true"' in resp.text and 'id="alert-count"' in resp.text
    trigger = resp.headers["HX-Trigger"]
    assert "alerts-changed" in trigger and "Alert resolved" in trigger
