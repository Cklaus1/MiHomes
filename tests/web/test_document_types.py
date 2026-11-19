"""Documents page — the "Playbook / SOP" type is gone (playbooks have their own page; 0021)."""

from __future__ import annotations

from mihomes.models.document import DocumentType


def test_sop_is_not_a_document_type():
    assert "sop" not in {t.value for t in DocumentType}


def test_sop_is_not_offered_on_the_page(client):
    body = client.get("/documents/").text
    assert 'href="/documents/?type=sop"' not in body
    assert '<option value="sop"' not in body
    assert "Playbook / SOP" not in body
