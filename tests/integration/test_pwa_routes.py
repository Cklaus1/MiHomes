"""SPEC-014 A24 — the service worker and manifest are public, at the root, with the right headers."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _client():
    from mihomes.web.app import create_app

    return TestClient(create_app(), base_url="http://localhost")


def test_sw_js_scope_header():
    r = _client().get("/sw.js")        # no cookie: unauthenticated on purpose
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["service-worker-allowed"] == "/"
    assert r.headers["cache-control"] == "no-cache"
    assert "addEventListener(\"push\"" in r.text
    assert "addEventListener(\"fetch\"" not in r.text and "caches." not in r.text   # N4


def test_manifest_public():
    r = _client().get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    data = json.loads(r.text)
    assert data["start_url"] == "/chat/" and data["display"] == "standalone"
    for icon in data["icons"]:
        assert _client().get(icon["src"]).status_code == 200
