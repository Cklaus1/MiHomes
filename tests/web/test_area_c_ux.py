"""UI/UX pass, area C — documents, playbooks, templates, calendar, budget, AI.

Each test renders the page in the state the change is about, so a Jinja error in a branch that
the smoke GETs never reach (an open modal, a crowded calendar day) still fails here.
"""

import io
import re
from datetime import date

from mihomes.web.routes.ai import _MAX_ATTACH_TOTAL_BYTES


def _prop_id(client) -> str:
    body = client.get("/budget/").text
    return re.search(r'<option value="([0-9a-f-]{36})"', body).group(1)


def test_ai_page_renders_with_caps_and_a11y(client):
    body = client.get("/ai/").text
    assert 'role="log"' in body
    assert "const MAX_ATTACH_FILES = 6;" in body
    assert 'for="query-input"' in body


def test_stream_attachment_cap_is_not_an_ai_failure(client):
    resp = client.post(
        "/ai/ask-stream",
        data={"query": "hi"},
        files=[("files", ("big.txt", io.BytesIO(b"x" * (_MAX_ATTACH_TOTAL_BYTES + 1)), "text/plain"))],
    )
    assert resp.status_code == 200
    assert "Total attachment size" in resp.text
    assert "AI request failed" not in resp.text
    assert "[DONE]" in resp.text


def test_documents_upload_error_reopens_modal_with_input(client):
    resp = client.post(
        "/documents/",
        data={"title": "Pool Warranty", "document_type": "warranty", "notes": "keep me"},
        files={"file": ("x.svg", b"<svg/>", "image/svg+xml")},
    )
    assert resp.status_code == 200
    body = resp.text
    assert 'id="add-doc-modal" class="fixed' in body          # rendered open
    assert 'value="Pool Warranty"' in body
    assert "keep me</textarea>" in body


def test_documents_search_and_sort(client):
    for title in ("Boiler Manual", "Roof Permit"):
        client.post("/documents/", data={"title": title, "document_type": "manual", "file_path": "https://x"})
    body = client.get("/documents/?q=boiler&sort=title").text
    assert "Boiler Manual" in body
    assert "Roof Permit" not in body
    assert "No documents match" in client.get("/documents/?q=zzzz").text


def test_documents_edit_error_reopens_that_document_in_edit_mode(client):
    client.post("/documents/", data={"title": "Deed", "document_type": "other", "file_path": "https://x"})
    slug = re.search(r'id="doc-modal-([^"]+)"', client.get("/documents/").text).group(1)
    body = client.post(
        f"/documents/{slug}/edit",
        data={"title": "Deed v2", "document_type": "other", "file_path": "https://x"},
        files={"file": ("x.svg", b"<svg/>", "image/svg+xml")},
    ).text
    assert f'id="doc-modal-{slug}" class="fixed' in body
    assert f'id="doc-edit-{slug}" class="px-6' in body       # edit pane shown, not hidden
    assert 'value="Deed v2"' in body


def test_budget_transaction_date_and_monthly_estimate(client):
    pid = _prop_id(client)
    r = client.post("/budget/transactions", data={
        "property_id": pid, "description": "Pool", "amount": "100", "tx_date": "2026-01-15",
    })
    assert r.status_code == 200
    assert "toast" in r.headers.get("HX-Trigger", "")
    slug = "test-manor"
    client.post("/recurring/", data={
        "name": "Mowing", "amount": "300", "frequency": "custom_weeks", "interval_count": "3",
        "property_slug": slug,
    })
    body = client.get("/budget/?tab=recurring").text
    # 300 every 3 weeks ≈ 300 * (52/12) / 3 = 433
    assert "433" in body
    assert client.get("/budget/?tab=analysis").status_code == 200


def test_calendar_crowded_day_gets_more_link(client):
    pid = _prop_id(client)
    d = date.today().replace(day=10).isoformat()
    for i in range(5):
        client.post("/calendar/appointments/", data={"title": f"Visit {i}", "property_id": pid, "appt_date": d})
    body = client.get("/calendar/").text
    assert f'id="day-modal-{d}"' in body
    assert "+ 2 more" in body
    assert 'aria-label="Previous month"' in body
    assert "grid-cols-7" in body


def test_template_name_with_apostrophe_uses_data_attributes(client):
    client.post("/templates/", data={"name": "Owner's Arrival", "steps": "a\nb\nc\nd\ne"})
    body = client.get("/templates/").text
    assert 'data-run-name="Owner&#39;s Arrival"' in body
    assert "<details" in body


def test_playbook_run_redirects_after_post(client):
    from mihomes.services.playbook import import_markdown

    # Playbooks are account rows now (2026-10-21), so the test seeds its own instead of relying on
    # whatever files happened to be in knowledge/playbooks/.
    with client._SessionLocal() as s:
        import_markdown(s, "season-open", "# Season Open\n\n## Day 1\n- [ ] Air out rooms\n- [ ] Check water\n")
        s.commit()
    slug = "season-open"
    resp = client.post(f"/playbooks/{slug}/run", data={"property_slug": "test-manor"}, follow_redirects=False)
    assert resp.status_code == 303
    assert f"/playbooks/{slug}?ran=" in resp.headers["location"]
    assert re.search(r"\d+ tasks? created", client.get(resp.headers["location"]).text)
    bad = client.post(f"/playbooks/{slug}/run", data={"property_slug": "no-such-place"})
    assert "Couldn&#39;t run this playbook" in bad.text or "Couldn't run this playbook" in bad.text
