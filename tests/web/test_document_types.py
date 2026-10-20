"""Documents page — "Playbook / SOP" is retired (playbooks have their own page).

Retired from the page, not from `DocumentType`: existing rows must still load, display, and survive
an edit without being silently re-typed.
"""

from __future__ import annotations


def test_sop_is_not_offered_as_a_category(client):
    body = client.get("/documents/").text
    assert 'href="/documents/?type=sop"' not in body
    assert '<option value="sop"' not in body
    assert "Playbook / SOP" not in body


def test_an_existing_sop_document_still_shows_and_keeps_its_type_on_edit(client):
    body = client.post(
        "/documents/", data={"title": "Old Pool SOP", "document_type": "sop", "file_path": ""}
    ).text
    assert "Old Pool SOP" in body
    assert "Playbook / SOP" in body  # its chip still renders
    assert '<option value="sop" selected>' in body  # its edit form keeps the type
