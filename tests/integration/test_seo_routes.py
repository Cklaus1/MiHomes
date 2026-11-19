"""robots.txt, sitemap.xml, Google site verification and the marketing page's meta tags."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    from mihomes.web.app import create_app

    monkeypatch.setenv("MIHOMES_BASE_URL", "https://mihomes.ai")
    return TestClient(create_app(), base_url="http://localhost")


def test_robots_admits_public_pages_and_blocks_the_rest(client):
    r = client.get("/robots.txt")            # no cookie: crawlers carry none
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    assert "Allow: /$" in lines and "Allow: /legal/" in lines
    assert "Disallow: /" in lines
    assert "Sitemap: https://mihomes.ai/sitemap.xml" in lines


def test_sitemap_lists_homepage_and_only_published_legal_pages(client, monkeypatch):
    from mihomes import legal

    monkeypatch.setattr(legal, "published", lambda slug: object() if slug == "terms" else None)
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<loc>https://mihomes.ai/</loc>" in r.text
    assert "<loc>https://mihomes.ai/legal/terms</loc>" in r.text
    assert "/legal/privacy" not in r.text


def test_verification_file_served_only_when_configured(client, monkeypatch):
    assert client.get("/google1a2b3c4d5e6f7a8b.html").status_code == 404   # unset: 404

    monkeypatch.setenv("MIHOMES_GOOGLE_SITE_VERIFICATION", "google1a2b3c4d5e6f7a8b.html")
    r = client.get("/google1a2b3c4d5e6f7a8b.html")
    assert r.status_code == 200
    assert r.text == "google-site-verification: google1a2b3c4d5e6f7a8b.html"

    # Any other name must 404 — its content is derivable, so answering would let anyone verify.
    assert client.get("/googledeadbeef.html").status_code == 404


def test_verification_env_accepts_bare_token_and_lists():
    import os

    from mihomes.web.routes import seo

    os.environ[seo.VERIFICATION_ENV] = " google111.html , 222 ,google../x.html"
    try:
        assert seo.verification_tokens() == {"111", "222"}
    finally:
        del os.environ[seo.VERIFICATION_ENV]


def test_marketing_page_has_description_and_preview_tags(client):
    body = client.get("/", headers={"accept": "text/html"}).text
    assert '<meta name="description" content="The AI-first command center' in body
    assert '<meta property="og:title" content="MiHomes — every home, under control">' in body
    assert '<meta property="og:description" content="The AI-first command center' in body
