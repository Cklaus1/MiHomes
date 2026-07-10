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


def test_search_dropdown_renders_matching_results(client):
    resp = client.get("/search/dropdown", params={"q": "acme"})
    assert resp.status_code == 200
    assert "Acme Pest" in resp.text


def test_search_dropdown_empty_query_returns_prompt(client):
    resp = client.get("/search/dropdown", params={"q": ""})
    assert resp.status_code == 200
    assert "Type to search" in resp.text


def test_search_dropdown_no_matches(client):
    resp = client.get("/search/dropdown", params={"q": "zzz-no-such-thing-zzz"})
    assert resp.status_code == 200
    assert "No results" in resp.text


# --- Recurring expenses (budget page must survive one existing) -----------

def test_create_recurring_expense_then_budget_page_loads(client):
    prop_slug = client.get("/properties/").text  # ensure properties route works first
    from mihomes.services import property as prop_svc
    with client._SessionLocal() as s:
        prop = prop_svc.list_properties(s)[0]
        prop_slug = prop.slug

    resp = client.post("/recurring/", data={
        "name": "Pest Control",
        "amount": "132.25",
        "frequency": "custom_months",
        "property_slug": prop_slug,
        "category": "pest_control",
        "interval_count": "2",
    })
    assert resp.status_code == 200
    assert "Pest Control" in resp.text

    # The real regression: once a recurring expense exists, every budget tab
    # renders its notes_map — this must not 500.
    resp = client.get("/budget/")
    assert resp.status_code == 200
    assert "Pest Control" in resp.text


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


# --- Scan Room (camera → AI → bulk add) ------------------------------------

import base64 as _b64  # noqa: E402

# 1x1 transparent PNG
_PNG_1x1 = _b64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def test_scan_room_returns_review(client, monkeypatch):
    import mihomes.web.routes.assets as assets_routes
    monkeypatch.setattr(assets_routes, "parse_room_scan", lambda db, attachments, room_name=None: [
        {"name": "Leather Sofa", "asset_type": "valuable", "condition": "good", "estimated_value": 1500},
        {"name": "Floor Lamp", "asset_type": "equipment", "condition": "fair", "estimated_value": 80},
    ])
    monkeypatch.setattr(assets_routes, "_save_room_photo", lambda att: "/static/uploads/test.png")

    r = client.post(
        "/assets/scan",
        data={"property_slug": "test-manor", "space_slug": "living-room"},
        files=[("files", ("room.png", _PNG_1x1, "image/png"))],
    )
    assert r.status_code == 200
    assert "Leather Sofa" in r.text and "Floor Lamp" in r.text


def test_scan_confirm_creates_selected_assets(client):
    r = client.post("/assets/scan/confirm", data={
        "property_slug": "test-manor",
        "space_slug": "living-room",
        "photo_path": "/static/uploads/test.png",
        "include": ["0"],  # only the first row
        "name": ["Leather Sofa", "Floor Lamp"],
        "asset_type": ["valuable", "equipment"],
        "condition": ["good", "fair"],
        "value": ["1500", ""],
        "note": ["", ""],
    })
    assert r.status_code == 200

    from mihomes.models.asset import Asset
    from mihomes.models.document import Document
    with client._SessionLocal() as s:
        sofa = s.query(Asset).filter_by(name="Leather Sofa").one()
        assert sofa.space_id is not None          # linked to the room
        assert sofa.purchase_price == 1500.0       # estimate stored as Value
        assert s.query(Asset).filter_by(name="Floor Lamp").first() is None  # excluded row
        assert s.query(Document).filter_by(entity_type="space").count() == 1  # room photo kept


def test_scan_rejects_missing_photo(client):
    r = client.post("/assets/scan", data={"property_slug": "test-manor", "space_slug": "living-room"})
    assert r.status_code == 200  # friendly error, not a 500
    assert "at least one photo" in r.text.lower()
