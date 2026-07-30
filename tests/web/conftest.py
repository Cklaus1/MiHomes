"""Shared fixtures for web-layer hardening tests (G-Web).

Mirrors the isolated in-memory TestClient pattern from
`tests/integration/test_web_smoke.py`: the app's only DB entry point is the
`get_db` dependency, so overriding it fully isolates these tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mihomes.models import Base
from mihomes.models.staff import StaffRole
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import staff as staff_svc
from mihomes.services import vendor as vendor_svc
from mihomes.web.app import create_app
from mihomes.web.deps import get_db


@pytest.fixture
def client():
    """TestClient bound to a fresh in-memory DB seeded with minimal estate data."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)

    with TestSessionLocal() as s:
        prop = prop_svc.create_property(s, "Test Manor")
        space_svc.create_space(s, "Living Room", prop.slug)
        staff_svc.create_staff(
            s, "Marcia Staff", role=StaffRole.HOUSEKEEPER, property_id_or_slug=prop.slug
        )
        staff_svc.create_staff(s, "Rita Resident", role=StaffRole.RESIDENT)
        vendor_svc.create_vendor(s, "Acme Pest", service_categories=["Pest Control"])
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
    # Loopback base_url so the H30 Host guard accepts requests by default.
    with TestClient(app, base_url="http://localhost") as c:
        c._SessionLocal = TestSessionLocal  # exposed for assertions if needed
        yield c
