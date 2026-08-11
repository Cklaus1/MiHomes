"""Shared fixtures for web-layer tests.

Was an isolated in-memory SQLite engine. SPEC-002 Step 15 makes the suite
Postgres-only, and G2 made `account_id` NOT NULL on 40 tables — so this conftest
needed the same treatment as the root one. The spec's Fixtures paragraph does not
mention this second conftest at all; it surfaced as 123 errors, every one
`LookupError: <ContextVar name='current_account'>`, which is the fail-closed
behaviour working exactly as intended.

The pattern is unchanged in shape: the app's only DB entry point is the `get_db`
dependency, so overriding it fully isolates these tests. What changed is that the
override now yields an **account-scoped** session, and the seed data is created
inside an account context.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from mihomes.models.staff import StaffRole
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import staff as staff_svc
from mihomes.services import vendor as vendor_svc
from mihomes.tenancy import account_context
from mihomes.web.app import create_app
from mihomes.web.deps import get_db


@pytest.fixture
def client(_pg_engine, account_a):
    """TestClient over Postgres, seeded with minimal estate data for one account.

    Reuses the root conftest's session-scoped engine and `account_a` rather than
    building its own: one schema, one place that knows how to make an account.
    """
    connection = _pg_engine.connect()
    transaction = connection.begin()
    TestSessionLocal = sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint"
    )

    # Every request and every seed write happens inside this account.
    with account_context(account_a):
        with TestSessionLocal() as s:
            prop = prop_svc.create_property(s, "Test Manor")
            space_svc.create_space(s, "Living Room", prop.slug)
            staff_svc.create_staff(
                s, "Marcia Staff", role=StaffRole.HOUSEKEEPER,
                property_id_or_slug=prop.slug,
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

    # Outer rollback discards everything, including the seed, so the shared schema is
    # never mutated between tests.
    transaction.rollback()
    connection.close()
