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
    """A document title with quotes must not escape into an inline onclick JS string.

    Rendered through the work-orders page, which uses the same `partials/docs_section.html`.
    (It ran through the contracts page until that page was removed, 2026-10-15.)
    """
    from mihomes.models.document import DocumentType
    from mihomes.services import document as doc_svc
    from mihomes.services import work_order as wo_svc

    with client._SessionLocal() as s:
        prop = prop_svc.create_property(s, "Doc Manor")
        wo = wo_svc.create_work_order(s, "Leaky tap", prop.slug)
        s.flush()
        doc_svc.create_document(
            s,
            title="Evil');alert(1);//",
            file_path="/static/uploads/x.png",
            document_type=DocumentType.PHOTO,
            entity_type="work_order",
            entity_id=wo.id,
        )
        s.commit()

    # Docs render on the work orders list page.
    resp = client.get("/work-orders/")
    assert resp.status_code == 200
    # The raw JS-breakout sequence must not appear inside an onclick handler.
    assert "onclick=\"previewDoc('Evil');alert(1);//" not in resp.text
