"""W.9 · M18 — sanitize model-rendered markdown; no inline-JS string injection.

Two fixes:
  1. Every ``marked.parse(...)`` that flows into ``innerHTML`` must be wrapped in
     ``DOMPurify.sanitize(...)`` so model/stored markdown can't inject script.
  2. ``docs_section.html`` must not interpolate a document title into an inline
     ``onclick="previewDoc('...')"`` JS string (a title with a quote/backslash
     breaks out). Use ``data-*`` attributes + a delegated listener instead.
"""

from pathlib import Path

import pytest

from mihomes.services import property as prop_svc

TEMPLATES = Path("src/mihomes/web/templates")


# --- Part 1: DOMPurify wraps marked.parse in the source templates -----------

MARKED_TEMPLATES = ["dashboard.html", "ai.html"]


@pytest.mark.parametrize("name", MARKED_TEMPLATES)
def test_marked_templates_load_dompurify(name):
    src = (TEMPLATES / name).read_text()
    assert "marked.parse" in src, "precondition: template uses marked.parse"
    assert "dompurify" in src.lower() or "DOMPurify" in src, (
        f"{name} renders markdown but never loads DOMPurify"
    )


@pytest.mark.parametrize("name", MARKED_TEMPLATES)
def test_no_unsanitized_marked_parse(name):
    src = (TEMPLATES / name).read_text()
    # Every marked.parse( occurrence must be immediately wrapped by
    # DOMPurify.sanitize( — i.e. the substring "DOMPurify.sanitize(marked.parse("
    # (allowing for whitespace) — and there must be no bare marked.parse assigned
    # straight to innerHTML.
    import re

    for m in re.finditer(r"marked\.parse\(", src):
        prefix = src[max(0, m.start() - 40):m.start()]
        assert "DOMPurify.sanitize(" in prefix, (
            f"{name}: marked.parse at offset {m.start()} not wrapped by DOMPurify.sanitize"
        )


# --- Part 2: docs_section.html uses data-attrs, not inline onclick JS --------


def test_docs_section_has_no_inline_previewdoc_onclick():
    src = (TEMPLATES / "partials" / "docs_section.html").read_text()
    assert "onclick=\"previewDoc(" not in src, (
        "docs_section.html still interpolates title into an inline onclick JS string"
    )


def test_hostile_doc_title_does_not_break_out(client):
    """A document title with quotes must not escape into an inline onclick JS string."""
    from datetime import date

    from mihomes.models.document import DocumentType
    from mihomes.services import contract as contract_svc
    from mihomes.services import document as doc_svc
    from mihomes.services import vendor as vendor_svc

    with client._SessionLocal() as s:
        prop = prop_svc.create_property(s, "Doc Manor")
        vendor = vendor_svc.create_vendor(s, "Doc Vendor", service_categories=["Plumbing"])
        contract = contract_svc.create_contract(
            s, vendor.slug, prop.slug, start_date=date.today()
        )
        s.flush()
        doc_svc.create_document(
            s,
            title="Evil');alert(1);//",
            file_path="/static/uploads/x.png",
            document_type=DocumentType.PHOTO,
            entity_type="contract",
            entity_id=contract.id,
        )
        s.commit()

    # Docs render on the contracts list page.
    resp = client.get("/contracts/")
    assert resp.status_code == 200
    # The raw JS-breakout sequence must not appear inside an onclick handler.
    assert "onclick=\"previewDoc('Evil');alert(1);//" not in resp.text
