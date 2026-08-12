"""W.3 · H31+R3 — app-level exception handlers.

`resolve_identifier` raises `EntityNotFoundError` for unknown ids/slugs and
`AmbiguousIdentifierError` for prefix matches that hit multiple rows. Web routes
call service getters that let these propagate. Without app-level handlers they
surface as uncaught 500s. They should map to 404 / 400 respectively.

`GET /properties/{slug}` is the exercised route (it resolves via the service
getter and has a real detail page).
"""

import pytest

from mihomes.services import property as prop_svc


@pytest.fixture
def raw_client(web_client_factory):
    """TestClient that does NOT re-raise server exceptions, so 500s are observable.

    Its own seed rather than `seed_estate`: these tests need two properties whose
    slugs share a prefix, which is the whole point of the ambiguity case.
    """

    def seed(s):
        # Two properties whose slugs share the "lake" prefix → ambiguous.
        prop_svc.create_property(s, "Lakeside Cottage")
        prop_svc.create_property(s, "Lakefront Villa")

    return web_client_factory(seed, raise_server_exceptions=False)


def test_unknown_slug_returns_404(raw_client):
    resp = raw_client.get("/properties/does-not-exist")
    assert resp.status_code == 404


def test_ambiguous_slug_returns_400(raw_client):
    # "lake" prefix-matches both seeded properties → AmbiguousIdentifierError.
    resp = raw_client.get("/properties/lake")
    assert resp.status_code == 400


def test_known_slug_still_ok(raw_client):
    resp = raw_client.get("/properties/lakeside-cottage")
    assert resp.status_code == 200
