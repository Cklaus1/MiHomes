"""Importing and exporting assets and inventory (2026-11-05)."""

import html

from mihomes.models.asset import Asset
from mihomes.models.consumable import Consumable
from mihomes.models.space import Space
from mihomes.services import property as prop_svc


def _home(client):
    with client._SessionLocal() as s:
        return prop_svc.list_properties(s)[0]


def _apply(client, kind, text):
    return html.unescape(client.post("/import/apply", data={"entity_type": kind, "csv_text": text}).text)


def _preview(client, kind, text):
    return html.unescape(client.post("/import/preview", data={"entity_type": kind},
                                     files={"file": ("f.csv", text.encode(), "text/csv")}).text)


def test_assets_create_their_room_and_say_so_first(client):
    home = _home(client)
    csv_text = ("name,property,room,asset_type,make,model,warranty_expires,purchase_price\r\n"
                f"Wine fridge,{home.name},Cellar,appliance,Sub-Zero,UW-24,2027-03-01,\"$2,499.00\"\r\n")
    preview = _preview(client, "asset", csv_text)
    assert "New room “Cellar”" in preview
    with client._SessionLocal() as s:
        assert s.query(Space).filter(Space.name == "Cellar").count() == 0, "a preview creates no room"

    done = _apply(client, "asset", csv_text)
    assert "<strong>1</strong> assets added" in done
    with client._SessionLocal() as s:
        asset = s.query(Asset).filter(Asset.name == "Wine fridge").one()
        assert asset.space.name == "Cellar" and asset.make == "Sub-Zero" and asset.model_name == "UW-24"
        assert str(asset.warranty_expires) == "2027-03-01" and float(asset.purchase_price) == 2499.0
        assert asset.asset_type.value == "appliance"


def test_asset_reupload_updates_and_moves_rooms(client):
    home = _home(client)
    _apply(client, "asset", f"name,property,room,condition\r\nRowing machine,{home.name},Gym,good\r\n")
    page = _apply(client, "asset", f"name,property,condition,notes\r\nRowing machine,{home.name},fair,Belt worn\r\n")
    assert "condition: good → fair" in page and "notes: — → Belt worn" in page
    with client._SessionLocal() as s:
        asset = s.query(Asset).filter(Asset.name == "Rowing machine").one()
        assert asset.condition.value == "fair" and asset.space.name == "Gym", "blank room keeps the room"


def test_same_name_in_different_rooms_is_two_assets(client):
    home = _home(client)
    csv_text = ("name,property,room\r\n"
                f"Smoke detector,{home.name},Kitchen\r\nSmoke detector,{home.name},Hall\r\n")
    assert "<strong>2</strong> assets added" in _apply(client, "asset", csv_text)
    again = _apply(client, "asset", csv_text)
    assert "<strong>0</strong> assets added" in again and "2 unchanged" in again
    with client._SessionLocal() as s:
        rooms = {a.space.name for a in s.query(Asset).filter(Asset.name == "Smoke detector")}
        assert rooms == {"Kitchen", "Hall"}


def test_inventory_status_is_computed_and_recomputed(client):
    home = _home(client)
    _apply(client, "inventory", "name,property,unit,quantity,par_level,low_stock_threshold,category\r\n"
                                f"Paper towels,{home.name},rolls,2,12,4,cleaning\r\n")
    with client._SessionLocal() as s:
        item = s.query(Consumable).filter(Consumable.name == "Paper towels").one()
        assert item.status.value in ("low", "out") and item.unit == "rolls"

    page = _apply(client, "inventory", f"name,property,qty\r\nPaper towels,{home.name},20\r\n")
    assert "quantity: 2 → 20" in page
    with client._SessionLocal() as s:
        item = s.query(Consumable).filter(Consumable.name == "Paper towels").one()
        assert item.quantity_in_stock == 20 and item.status.value == "ok"
        assert item.par_level == 12 and item.category == "cleaning", "blank cells keep values"


def test_bad_values_are_row_errors(client):
    home = _home(client)
    page = _apply(client, "asset", "name,property,asset_type,purchase_price\r\n"
                                   f"Mystery,{home.name},spaceship,\r\nPricey,{home.name},,lots\r\n")
    assert "“spaceship” isn't an asset type" in page
    assert "“lots” isn't a number for purchase_price." in page


def test_export_round_trips_for_both(client):
    home = _home(client)
    _apply(client, "asset", f"name,property,room,make\r\nGenerator,{home.name},Garage,Generac\r\n")
    _apply(client, "inventory", f"name,property,quantity\r\nSalt,{home.name},3\r\n")
    for kind, expect in (("asset", f"Generator,{home.name},Garage,equipment,Generac"),
                         ("inventory", f"Salt,{home.name},,,3")):
        r = client.get(f"/export/{kind}.csv")
        assert r.status_code == 200
        body = r.content.decode("utf-8").lstrip("﻿")
        assert expect in body, body
        again = _apply(client, kind, body)
        assert "unchanged" in again and "<strong>0</strong>" in again
    name = client.get("/export/inventory.csv").headers["content-disposition"]
    assert "mihomes-inventory-items-" in name and " " not in name.split("filename=")[1]


def test_pages_offer_export_and_import_lists_both_types(client):
    assert 'href="/export/asset.csv"' in client.get("/assets/").text
    assert 'href="/export/inventory.csv"' in client.get("/inventory/").text
    page = client.get("/import/?type=inventory").text
    assert "low_stock_threshold" in page and 'href="/import/?type=asset"' in page
