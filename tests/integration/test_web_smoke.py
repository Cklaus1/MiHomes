"""Web smoke tests — FastAPI TestClient against an isolated in-memory DB.

These cover the web/HTMX layer that the 742 service/CLI tests don't touch:
every page renders, key inline-edit / filter endpoints round-trip, and the
Directory category logic behaves. The app's only DB entry point is the
`get_db` dependency (no route calls `get_session` directly), so overriding it
fully isolates these tests from the real database.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mihomes.models import Base
from mihomes.models.staff import StaffRole
from mihomes.services import property as prop_svc
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
    with TestClient(app) as c:
        c._SessionLocal = TestSessionLocal  # exposed for assertions if needed
        yield c


# --- Every page renders ----------------------------------------------------

PAGES = [
    "/",
    "/properties/",
    "/tasks/",
    "/issues/",
    "/staff/",
    "/vendors/",
    "/budget/",
    "/alerts/",
    "/assets/",
    "/work-orders/",
    "/contracts/",
    "/recurring/",
    "/templates/",
    "/documents/",
    "/library/",  # books UI lives here; /books/ is an action endpoint, not a page
    "/playbooks/",
    "/inventory/",
]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders_200(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"
    assert "<html" in resp.text.lower()


def test_search_page_renders(client):
    resp = client.get("/search/", params={"q": "manor"})
    assert resp.status_code == 200


# --- Directory (formerly Staff) --------------------------------------------

def test_directory_lists_all_people(client):
    html = client.get("/staff/").text
    assert "Directory" in html
    assert "Marcia Staff" in html
    assert "Rita Resident" in html


def test_directory_category_data_attributes(client):
    html = client.get("/staff/").text
    # Cards carry the category derived from role; pills exist for filtering.
    assert 'data-category="Staff"' in html
    assert 'data-category="Resident"' in html
    assert 'data-cat="Resident"' in html


def test_directory_create_resident_appears(client):
    client.post("/staff/", data={"name": "Nora New", "role": "resident"})
    html = client.get("/staff/").text
    assert "Nora New" in html
    # Resident pill count is now 2 (Rita + Nora).
    m = re.search(r'data-cat="Resident"[^>]*>\s*Resident\s*<span[^>]*>\s*(\d+)', html)
    assert m and int(m.group(1)) == 2


def test_directory_edit_assigns_property(client):
    """Regression: editing a person must let you assign a property (was non-functional)."""
    # Discover Rita's edit form and the property checkbox id.
    html = client.get("/staff/").text
    block = re.search(
        r'hx-post="/staff/(rita-resident)/edit".*?</form>', html, re.S
    ).group(0)
    prop_id = re.search(r'name="property_ids" value="(\d+)"', block).group(1)
    assert "checked" not in re.search(
        r'name="property_ids" value="%s"[^>]*>' % prop_id, block
    ).group(0)

    # Assign the property via the edit endpoint.
    resp = client.post(
        "/staff/rita-resident/edit",
        data={"name": "Rita Resident", "role": "resident", "active": "1", "property_ids": prop_id},
    )
    assert resp.status_code == 200

    # The property checkbox is now checked in Rita's edit form.
    html2 = client.get("/staff/").text
    block2 = re.search(
        r'hx-post="/staff/rita-resident/edit".*?</form>', html2, re.S
    ).group(0)
    checkbox = re.search(
        r'name="property_ids" value="%s"[^>]*?/>' % prop_id, block2, re.S
    ).group(0)
    assert "checked" in checkbox


def test_directory_edit_unassigns_property(client):
    html = client.get("/staff/").text
    prop_id = re.search(r'name="property_ids" value="(\d+)"', html).group(1)
    # Assign then clear.
    client.post(
        "/staff/rita-resident/edit",
        data={"name": "Rita Resident", "role": "resident", "active": "1", "property_ids": prop_id},
    )
    client.post(
        "/staff/rita-resident/edit",
        data={"name": "Rita Resident", "role": "resident", "active": "1"},
    )
    html2 = client.get("/staff/").text
    block = re.search(
        r'hx-post="/staff/rita-resident/edit".*?</form>', html2, re.S
    ).group(0)
    checkbox = re.search(
        r'name="property_ids" value="%s"[^>]*?/>' % prop_id, block, re.S
    ).group(0)
    assert "checked" not in checkbox


# --- Employee-only surfaces exclude non-staff ------------------------------

def test_tasks_assignee_excludes_non_staff(client):
    """Task assignee dropdown should show staff but not residents/associates."""
    html = client.get("/tasks/").text
    assert "Marcia Staff" in html
    assert "Rita Resident" not in html


# --- Vendors ---------------------------------------------------------------

def test_vendor_filter_data_contract(client):
    """The category filter relies on card data-categories matching pill data-cat."""
    html = client.get("/vendors/").text
    assert "Acme Pest" in html
    assert 'data-cat="Pest Control"' in html
    assert 'data-categories="Pest Control"' in html


def test_vendor_create_appears(client):
    client.post(
        "/vendors",
        data={"company_name": "Bright Pools", "service_cats": "Pool"},
    )
    html = client.get("/vendors/").text
    assert "Bright Pools" in html
