"""Landing app skeleton — boot, /healthz, and the route allowlist (SPEC-001 A11, A17).

**A11 is the structural invariant of Phase 0 and §7-N1 says do not delete its test.**
`src/mihomes/web/app.py` mounts 22 routers with no authentication of any kind over
live estate data — properties, staff, financials, documents. `web/server.py`
defaults the bind host to 127.0.0.1 precisely because of that. Exposing any of it
publicly is a data breach, not a bug, so the landing app shares the stack and
mounts nothing of it (D1).
"""

import pytest
from fastapi.testclient import TestClient

from mihomes.landing import create_landing_app

# The complete allowlist from §7-N1. Anything outside it must 404.
ALLOWED_PATHS = {
    "/",
    "/waitlist",
    "/waitlist/confirm",
    "/auth/google/start",
    "/auth/google/callback",
    "/healthz",
}

# A sample of the single-user product's routes. None may be reachable.
FORBIDDEN_PATHS = [
    "/properties",
    "/tasks",
    "/issues",
    "/staff",
    "/vendors",
    "/budget",
    "/assets",
    "/work-orders",
    "/ai",
    "/calendar",
    "/documents",
    "/contracts",
]


@pytest.fixture
def client():
    return TestClient(create_landing_app(), raise_server_exceptions=False)


def test_app_boots(client):
    """The factory must produce a working app without touching the DB at import."""
    assert client.app is not None


def test_healthz(client):
    """A17 — /healthz returns 200 with the DB reachable. No auth, no PII."""
    response = client.get("/healthz")
    assert response.status_code == 200

    body = response.json()
    assert body.get("status") == "ok"
    # No PII and no internals: this endpoint is public and unauthenticated.
    assert "database_url" not in body
    assert not any("password" in str(v).lower() for v in body.values())


def test_existing_routes_are_404(client):
    """A11 — the single-user app is not reachable. DO NOT DELETE (§7-N1).

    A 404 is required, not a redirect and not a 401: anything that acknowledges
    the route confirms the surface exists.
    """
    for path in FORBIDDEN_PATHS:
        response = client.get(path)
        assert response.status_code == 404, (
            f"{path} returned {response.status_code}, not 404 — the single-user app "
            "must not be mounted in the landing app (§7-N1)"
        )


def test_no_single_user_router_is_mounted(client):
    """Belt-and-braces for A11: inspect the route table, not just sampled requests.

    FORBIDDEN_PATHS is a sample; a new router added later would slip past it. This
    asserts the allowlist positively, so mounting anything extra fails here even if
    nobody thought to add its path above.
    """
    mounted = set()
    for route in client.app.routes:
        path = getattr(route, "path", None)
        if path and not path.startswith("/static"):
            mounted.add(path)

    unexpected = mounted - ALLOWED_PATHS
    assert not unexpected, (
        f"routes outside the §7-N1 allowlist are mounted: {sorted(unexpected)}"
    )


def test_static_is_served(client):
    """The hero SVG and inlined CSS come from /static (N5: no bundler, no CDN)."""
    response = client.get("/static/hero.svg")
    assert response.status_code == 200
    assert "svg" in response.headers.get("content-type", "").lower()


def test_healthz_reports_unhealthy_when_the_database_is_gone(monkeypatch):
    """A liveness check that always returns 200 is not a liveness check.

    Fly restarts on a failing healthcheck, so this has to actually fail when the
    DB is unreachable — otherwise a broken deploy stays in rotation.
    """
    import mihomes.landing.routes as routes_mod

    def broken_check():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(routes_mod, "check_database", broken_check)
    client = TestClient(create_landing_app(), raise_server_exceptions=False)

    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json().get("status") != "ok"
