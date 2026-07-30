"""W.3 · H31+R3 — app-level exception handlers.

`resolve_identifier` raises `EntityNotFoundError` for unknown ids/slugs and
`AmbiguousIdentifierError` for prefix matches that hit multiple rows. Web routes
call service getters that let these propagate. Without app-level handlers they
surface as uncaught 500s. They should map to 404 / 400 respectively.

`GET /properties/{slug}` is the exercised route (it resolves via the service
getter and has a real detail page).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mihomes.models import Base
from mihomes.services import property as prop_svc
from mihomes.web.app import create_app
from mihomes.web.deps import get_db


@pytest.fixture
def raw_client():
    """TestClient that does NOT re-raise server exceptions, so 500s are observable."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)

    with TestSessionLocal() as s:
        # Two properties whose slugs share the "lake" prefix → ambiguous.
        prop_svc.create_property(s, "Lakeside Cottage")
        prop_svc.create_property(s, "Lakefront Villa")
        s.commit()

    def override_get_db():
        s = TestSessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as c:
        yield c


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
