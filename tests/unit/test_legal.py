"""Terms and Privacy: drafts stay hidden until published, and render safely (2026-11-09)."""

from datetime import date

from fastapi.testclient import TestClient

from mihomes import legal


def test_the_drafts_load_and_are_not_published():
    for slug in ("terms", "privacy"):
        doc = legal.load(slug)
        assert doc is not None and doc.title and "<h2" in doc.html
        assert doc.effective_date is None
        assert legal.published(slug) is None
        assert "DRAFT for counsel review" in legal.unresolved(slug)


def test_a_date_on_an_unfinished_draft_still_does_not_publish(monkeypatch):
    real = legal.load

    def dated(slug):
        doc = real(slug)
        return legal.LegalDocument(doc.slug, doc.title, date(2027, 1, 1), doc.version, doc.html)

    monkeypatch.setattr(legal, "load", dated)
    assert legal.published("terms") is None, "placeholders left: must stay hidden"
    monkeypatch.setattr(legal, "unresolved", lambda slug: [])
    assert legal.published("terms").effective_date == date(2027, 1, 1)


def test_the_renderer_escapes_and_only_links_safely():
    html = legal.render("# Title\n\nHi <script>alert(1)</script> **bold**\n\n"
                        "- [ok](https://example.com)\n- [bad](javascript:alert)\n\n1. one\n2. two")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<h2 id="title">Title</h2>' in html and "<strong>bold</strong>" in html
    assert '<a href="https://example.com">ok</a>' in html
    assert "javascript:" not in html and "<li>bad</li>" in html
    assert "<ol><li>one</li><li>two</li></ol>" in html


def _published(monkeypatch):
    real = legal.load

    def dated(slug):
        doc = real(slug)
        return None if doc is None else legal.LegalDocument(
            doc.slug, doc.title, date(2027, 1, 1), doc.version, doc.html)

    monkeypatch.setattr(legal, "load", dated)
    monkeypatch.setattr(legal, "unresolved", lambda slug: [])


def test_web_app_serves_them_signed_out_only_when_published(monkeypatch):
    from mihomes.web.app import create_app

    client = TestClient(create_app(), base_url="http://localhost")
    for path in ("/legal/terms", "/legal/privacy"):
        assert client.get(path).status_code == 404, f"{path} is unpublished"
    _published(monkeypatch)
    r = client.get("/legal/privacy")
    assert r.status_code == 200 and "Privacy Policy" in r.text and "Effective 1 January 2027" in r.text
    assert client.get("/legal/terms").status_code == 200


def test_landing_app_serves_them_too(monkeypatch):
    from mihomes.landing.app import create_landing_app

    client = TestClient(create_landing_app(), base_url="http://localhost")
    assert client.get("/legal/terms").status_code == 404
    _published(monkeypatch)
    assert client.get("/legal/terms").status_code == 200
    assert client.get("/legal/nonsense").status_code == 404
