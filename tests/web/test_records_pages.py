"""Directory & records pages (UI/UX pass, area B): rooms, assets, inventory, vendors, staff.

Each test pins a behaviour the pass changed, against the real Postgres schema — the room-delete
case in particular only fails on a database that enforces foreign keys.
"""

import re
from datetime import date, timedelta

from mihomes.models.asset import AssetType
from mihomes.services import asset as asset_svc
from mihomes.services import book as book_svc
from mihomes.services import issue as issue_svc


def _seed(client, fn):
    with client._SessionLocal() as s:
        out = fn(s)
        s.commit()
        return out


# --- Rooms ------------------------------------------------------------------

def test_deleting_a_room_keeps_its_contents(client):
    """Asset/Book/Issue reference spaces.id with no ON DELETE rule: deleting a non-empty room
    used to raise a foreign-key error. Now the contents are kept and unassigned."""
    def seed(s):
        a = asset_svc.create_asset(s, "Sofa", AssetType.VALUABLE, "test-manor", space_id_or_slug="living-room")
        b = book_svc.create_book(s, "Dune", "test-manor", space_id_or_slug="living-room")
        i = issue_svc.create_issue(s, "Damp patch", "test-manor", space_id_or_slug="living-room")
        return a.id, b.id, i.id
    asset_id, book_id, issue_id = _seed(client, seed)

    page = client.get("/assets/test-manor").text
    assert "Its 1 asset and 1 book will be kept but unassigned from a room." in page

    r = client.post("/assets/test-manor/living-room/delete-room")
    assert r.status_code == 200
    assert "Living Room deleted" in r.headers.get("HX-Trigger", "")

    from mihomes.models.asset import Asset
    from mihomes.models.book import Book
    from mihomes.models.issue import Issue
    from mihomes.models.space import Space
    with client._SessionLocal() as s:
        assert s.query(Space).filter_by(slug="living-room").first() is None
        assert s.get(Asset, asset_id).space_id is None
        assert s.get(Book, book_id).space_id is None
        assert s.get(Issue, issue_id).space_id is None


def test_property_page_links_rooms_and_states_room_contents(client):
    _seed(client, lambda s: asset_svc.create_asset(s, "Lamp", AssetType.EQUIPMENT, "test-manor",
                                                   space_id_or_slug="living-room"))
    html = client.get("/properties/test-manor").text
    assert 'href="/assets/test-manor/living-room"' in html
    assert "Its 1 asset will be kept but unassigned from a room." in html
    assert 'href="/inventory/?property_slug=test-manor"' in html


def test_new_property_form_labels(client):
    html = client.get("/properties/new").text
    assert 'for="new-property-name"' in html and 'id="new-property-name"' in html


def test_property_edit_stays_on_the_property_page(client):
    r = client.post("/properties/test-manor/edit", data={"name": "Test Manor House"})
    assert r.status_code == 200
    assert r.headers["HX-Push-Url"] == "/properties/test-manor-house"
    assert "Property saved" in r.headers.get("HX-Trigger", "")
    assert 'id="edit-property-name"' in r.text  # the detail page, not the list


def test_property_delete_returns_to_list(client):
    r = client.post("/properties/test-manor/delete", data={"confirm": "yes"})
    assert r.status_code == 200
    assert r.headers["HX-Push-Url"] == "/properties/"
    assert "Test Manor deleted" in r.headers.get("HX-Trigger", "")


def test_property_delete_without_confirmation_deletes_nothing(client):
    """The dialog posts confirm=yes; a stray or scripted POST without it must not erase a home."""
    r = client.post("/properties/test-manor/delete")
    assert r.status_code == 400
    assert "nothing was deleted" in r.text
    assert client.get("/properties/test-manor").status_code == 200


def test_property_delete_dialog_warns_and_requires_the_tick(client):
    html = client.get("/properties/test-manor").text
    dialog = html[html.index('id="delete-property-modal"'):]
    assert "Delete Test Manor and all of its data?" in dialog
    assert "cannot be retrieved" in dialog
    assert "Export your data before deleting" in dialog
    # Yes starts disabled and is only enabled by the acknowledgement checkbox.
    assert 'id="delete-property-ack"' in dialog
    assert re.search(r'<button type="submit" data-delete-yes disabled', dialog)
    assert "No, keep it" in dialog
    assert 'name="confirm" value="yes"' in dialog
    # The one-line browser confirm is gone.
    assert 'hx-confirm="Delete Test Manor?' not in html


def test_property_delete_dialog_explains_a_block_instead_of_offering_delete(client):
    _seed(client, lambda s: book_svc.create_book(s, "Dune", "test-manor"))
    html = client.get("/properties/test-manor").text
    dialog = html[html.index('id="delete-property-modal"'):]
    assert "Test Manor can't be deleted yet" in dialog
    assert "1 book" in dialog
    assert "data-delete-yes" not in dialog


# --- Assets -----------------------------------------------------------------

def test_warranty_badge_compares_against_today(client):
    today = date.today()

    def seed(s):
        for name, d in (("Old Fridge", today - timedelta(days=1)),
                        ("Soon Oven", today + timedelta(days=30)),
                        ("New Washer", today + timedelta(days=400))):
            asset_svc.create_asset(s, name, AssetType.APPLIANCE, "test-manor",
                                   space_id_or_slug="living-room", warranty_expires=d)
    _seed(client, seed)
    html = client.get("/assets/test-manor/living-room").text
    # One badge per state in the table row and again in each detail modal.
    assert html.count(">Expired</span>") == 2
    assert html.count(">Expiring</span>") == 2


def test_type_cards_count_the_room_not_the_filter(client):
    def seed(s):
        asset_svc.create_asset(s, "Car", AssetType.VEHICLE, "test-manor", space_id_or_slug="living-room")
        asset_svc.create_asset(s, "Toaster", AssetType.APPLIANCE, "test-manor", space_id_or_slug="living-room")
    _seed(client, seed)
    html = client.get("/assets/test-manor/living-room?asset_type=vehicle").text
    # The Appliances card still shows 1 while Vehicles is the active filter.
    assert ">1</div>\n    <div class=\"text-xs text-gray-500\">Appliances" in html.replace("\r\n", "\n")

    empty = client.get("/assets/test-manor/living-room?asset_type=valuable").text
    assert "No valuable assets here" in empty
    assert "Clear filter" in empty


# --- Inventory --------------------------------------------------------------

def _make_item(client, name="Paper Towels"):
    from mihomes.services.consumable import create_consumable
    return _seed(client, lambda s: create_consumable(s, name, "test-manor", category="Kitchen Pantry").slug)


def test_inventory_saves_redirect_so_refresh_cannot_resubmit(client):
    slug = _make_item(client)
    r = client.post(f"/inventory/{slug}/stock",
                    data={"quantity": "4", "property_slug": "test-manor", "category": "Kitchen Pantry"},
                    follow_redirects=False)
    assert r.status_code == 303
    # Filters survive the redirect, URL-encoded (the category has a space).
    assert r.headers["location"] == "/inventory/?property_slug=test-manor&category=Kitchen+Pantry&saved=stock"
    page = client.get(r.headers["location"]).text
    assert "Stock updated" in page


def test_inventory_price_entry_renders_editable(client):
    slug = _make_item(client)
    r = client.post(f"/inventory/{slug}/price-entries",
                    data={"price": "3.50", "entry_date": "2026-01-02", "quantity": "2"}, follow_redirects=False)
    assert r.status_code == 303
    html = client.get("/inventory/").text
    # UUID entry ids travel in data-* attributes, not unquoted into an onclick call.
    assert "data-entry-edit=" in html and "showEntryEdit(" not in html
    bad = client.post(f"/inventory/{slug}/price-entries", data={"price": "x", "entry_date": "2026-01-02"},
                      follow_redirects=False)
    assert bad.status_code == 400


def test_inventory_unknown_saved_key_shows_nothing(client):
    page = client.get("/inventory/?saved=pwned-banner").text
    assert "pwned-banner" not in page


def test_inventory_add_error_reopens_modal_with_input(client):
    r = client.post("/inventory/", data={
        "name": "Chlorine Tabs", "property_slug": "test-manor", "unit_price": "lots",
    }, follow_redirects=False)
    assert r.status_code == 400
    assert "Unit price must be a number" in r.text
    assert 'value="Chlorine Tabs"' in r.text
    assert "openModal('add-item-modal')" in r.text


def test_inventory_edit_and_delete_item(client):
    slug = _make_item(client)
    r = client.post(f"/inventory/{slug}/edit", data={"name": "Kitchen Roll", "category": "Pantry", "unit": "rolls"},
                    follow_redirects=False)
    assert r.status_code == 303
    from mihomes.models.consumable import Consumable
    with client._SessionLocal() as s:
        item = s.query(Consumable).filter_by(slug=slug).one()
        assert (item.name, item.category, item.unit) == ("Kitchen Roll", "Pantry", "rolls")

    r = client.post(f"/inventory/{slug}/delete", data={}, follow_redirects=False)
    assert r.status_code == 303
    with client._SessionLocal() as s:
        assert s.query(Consumable).filter_by(slug=slug).first() is None


# --- Vendors ----------------------------------------------------------------

def _vendor_estate(client):
    """One active and one inactive vendor, both insured and rated, one with a work order."""
    from mihomes.services import vendor as vendor_svc
    from mihomes.services import work_order as wo_svc

    def seed(s):
        live = vendor_svc.create_vendor(s, "Live Plumbing", insurance_info="Policy LIVE-1",
                                        phone="+1 555 0100", service_categories=["Plumbing"])
        gone = vendor_svc.create_vendor(s, "Gone Roofing", insurance_info="Policy GONE-2",
                                        service_categories=["Roofing"])
        for v in (live, gone):
            vendor_svc.rate_vendor(s, v.slug, quality=4, reliability=5, cost=2, communication=3)
        vendor_svc.update_vendor(s, gone.slug, active=False)
        wo = wo_svc.create_work_order(s, "Fix the boiler", "test-manor", vendor_id_or_slug=live.slug)
        return live.id, gone.id, wo.id
    return _seed(client, seed)


def test_vendor_card_and_modal_shared_by_active_and_inactive(client):
    live_id, gone_id, wo_id = _vendor_estate(client)
    html = client.get("/vendors/").text
    # The inactive copy had drifted and lost Insurance; one partial serves both now.
    assert "Policy LIVE-1" in html and "Policy GONE-2" in html
    assert f'data-record-id="{live_id}"' in html and f'data-record-id="{gone_id}"' in html
    # All four rating dimensions are shown, not just quality and reliability.
    assert "Avg Cost / Value" in html and "Avg Communication" in html
    # Work orders tab links to the record; phone is dialable.
    assert f'href="/work-orders/?open={wo_id}"' in html and "Fix the boiler" in html
    assert 'href="tel:+1 555 0100"' in html
    # No per-page copies of the global modal helpers, no interpolated filter handlers.
    assert "function switchTab" not in html and "function openModal" not in html
    assert "filterByCategory(" not in html


def test_vendor_save_keeps_its_properties(client):
    """prop_ids are UUIDs; the route used to keep only digit strings and so cleared them."""
    from mihomes.services import property as prop_svc
    from mihomes.services import vendor as vendor_svc
    with client._SessionLocal() as s:
        pid = str(prop_svc.list_properties(s)[0].id)
    r = client.post("/vendors/acme-pest/edit", data={"company_name": "Acme Pest", "prop_ids": pid})
    assert r.status_code == 200
    assert "Acme Pest saved" in r.headers.get("HX-Trigger", "")
    with client._SessionLocal() as s:
        assert [str(p) for p in vendor_svc.get_vendor(s, "acme-pest").property_ids] == [pid]


def test_vendor_add_form_takes_contact_rows(client):
    from mihomes.services import vendor as vendor_svc
    r = client.post("/vendors", data={
        "company_name": "Bright Pools",
        "c_name": ["Pat"], "c_role": ["Owner"], "c_phone": ["777"], "c_email": ["pat@x.com"],
    })
    assert r.status_code == 200
    with client._SessionLocal() as s:
        v = vendor_svc.get_vendor(s, "bright-pools")
        assert v.contacts[0]["name"] == "Pat" and v.phone == "777"


def test_staff_page_links_and_has_no_local_helpers(client):
    html = client.get("/staff/").text
    assert 'href="/properties/test-manor"' in html
    assert "function switchTab" not in html and "function openModal" not in html
    assert "staff-modal-" in html and 'data-tab="notes"' in html


def test_library_rows_link_to_the_book(client):
    book_id = _seed(client, lambda s: book_svc.create_book(s, "Dune", "test-manor", space_id_or_slug="living-room").id)
    html = client.get("/library/").text
    assert f'href="/assets/test-manor/living-room?open=book-{book_id}"' in html
