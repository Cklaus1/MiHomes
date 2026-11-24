"""Terms and Privacy: hidden until published, and render safely (2026-11-09; published 2026-11-24)."""

from dataclasses import replace
from datetime import date

from fastapi.testclient import TestClient

from mihomes import legal

PUBLISHED_ON = date(2026, 11, 24)


def test_both_documents_are_published_and_complete():
    for slug in ("terms", "privacy"):
        doc = legal.load(slug)
        assert doc is not None and doc.title and "<h2" in doc.html
        assert doc.effective_date == PUBLISHED_ON
        assert legal.unresolved(slug) == [], "a placeholder is live on the public page"
        assert legal.published(slug) is not None


def test_a_placeholder_or_a_missing_date_keeps_a_document_hidden(monkeypatch):
    monkeypatch.setattr(legal, "unresolved", lambda slug: ["[MINIMUM AGE]"])
    assert legal.published("terms") is None, "placeholders left: must stay hidden"
    monkeypatch.undo()

    real = legal.load
    monkeypatch.setattr(legal, "load", lambda slug: replace(real(slug), effective_date=None))
    assert legal.published("terms") is None, "no effective date: must stay hidden"


def test_the_renderer_escapes_and_only_links_safely():
    html = legal.render("# Title\n\nHi <script>alert(1)</script> **bold**\n\n"
                        "- [ok](https://example.com)\n- [bad](javascript:alert)\n\n1. one\n2. two")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<h2 id="title">Title</h2>' in html and "<strong>bold</strong>" in html
    assert '<a href="https://example.com">ok</a>' in html
    assert "javascript:" not in html and "<li>bad</li>" in html
    assert "<ol><li>one</li><li>two</li></ol>" in html


def _unpublished(monkeypatch):
    real = legal.load
    monkeypatch.setattr(legal, "load", lambda slug: None if real(slug) is None
                        else replace(real(slug), effective_date=None))


def test_web_app_serves_them_signed_out_only_when_published(monkeypatch):
    from mihomes.web.app import create_app

    client = TestClient(create_app(), base_url="http://localhost")
    r = client.get("/legal/privacy")
    assert r.status_code == 200 and "Privacy Policy" in r.text and "Effective 24 November 2026" in r.text
    assert client.get("/legal/terms").status_code == 200
    _unpublished(monkeypatch)
    for path in ("/legal/terms", "/legal/privacy"):
        assert client.get(path).status_code == 404, f"{path} is unpublished"


def test_landing_app_serves_them_too(monkeypatch):
    from mihomes.landing.app import create_landing_app

    client = TestClient(create_landing_app(), base_url="http://localhost")
    assert client.get("/legal/terms").status_code == 200
    assert client.get("/legal/nonsense").status_code == 404
    _unpublished(monkeypatch)
    assert client.get("/legal/terms").status_code == 404
