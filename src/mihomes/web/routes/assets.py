"""Assets & Inventory routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.asset import AssetType, AssetCondition
from mihomes.services import asset as asset_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _ctx(db: Session, asset_type: str | None = None) -> dict:
    all_assets = asset_svc.list_assets(db, active_only=False)
    assets = [a for a in all_assets if a.asset_type.value == asset_type] if asset_type else all_assets
    return {
        "page": "assets",
        "all_assets": all_assets,
        "assets": assets,
        "properties": prop_svc.list_properties(db),
        "asset_types": [t.value for t in AssetType],
        "conditions": [c.value for c in AssetCondition],
        "notes_map": {a.id: note_svc.list_notes(db, f"asset:{a.id}") for a in assets},
        "filter_type": asset_type,
    }


@router.get("/")
def list_assets(request: Request, asset_type: str | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "assets.html", _ctx(db, asset_type))


@router.post("/", response_class=HTMLResponse)
def create_asset(
    request: Request,
    name: str = Form(...),
    asset_type: str = Form(...),
    property_slug: str = Form(...),
    make: str = Form(""),
    model_name: str = Form(""),
    condition: str = Form("good"),
    purchase_price: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    asset_svc.create_asset(
        db,
        name=name,
        asset_type=AssetType(asset_type),
        property_id_or_slug=property_slug,
        make=make or None,
        model_name=model_name or None,
        condition=AssetCondition(condition),
        purchase_price=float(purchase_price) if purchase_price else None,
        notes=notes or None,
    )
    return templates.TemplateResponse(request, "assets.html", _ctx(db))


@router.post("/{slug}/notes", response_class=HTMLResponse)
def add_note(request: Request, slug: str, content: str = Form(...), db: Session = Depends(get_db)):
    note_svc.add_note(db, f"asset:{slug}", content)
    asset = asset_svc.get_asset(db, slug)
    notes = note_svc.list_notes(db, f"asset:{asset.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/assets/{slug}/notes",
        "delete_url_prefix": f"/assets/{slug}/notes",
    })


@router.delete("/{slug}/notes/{note_id}", response_class=HTMLResponse)
def delete_note(request: Request, slug: str, note_id: int, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    asset = asset_svc.get_asset(db, slug)
    notes = note_svc.list_notes(db, f"asset:{asset.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/assets/{slug}/notes",
        "delete_url_prefix": f"/assets/{slug}/notes",
    })


@router.post("/{slug}/edit", response_class=HTMLResponse)
def edit_asset(
    request: Request,
    slug: str,
    name: str = Form(...),
    notes: str = Form(""),
    make: str = Form(""),
    model_name: str = Form(""),
    serial_number: str = Form(""),
    purchase_date: str = Form(""),
    purchase_price: str = Form(""),
    warranty_expires: str = Form(""),
    last_serviced: str = Form(""),
    expected_lifespan_years: str = Form(""),
    replacement_cost_estimate: str = Form(""),
    condition: str = Form(""),
    db: Session = Depends(get_db),
):
    from datetime import date as date_type
    kwargs = dict(name=name, notes=notes or None)
    if make: kwargs["make"] = make
    if model_name: kwargs["model_name"] = model_name
    if serial_number: kwargs["serial_number"] = serial_number
    if purchase_date: kwargs["purchase_date"] = date_type.fromisoformat(purchase_date)
    if purchase_price: kwargs["purchase_price"] = float(purchase_price)
    if warranty_expires: kwargs["warranty_expires"] = date_type.fromisoformat(warranty_expires)
    if last_serviced: kwargs["last_serviced"] = date_type.fromisoformat(last_serviced)
    if expected_lifespan_years: kwargs["expected_lifespan_years"] = float(expected_lifespan_years)
    if replacement_cost_estimate: kwargs["replacement_cost_estimate"] = float(replacement_cost_estimate)
    if condition: kwargs["condition"] = condition  # AssetCondition enum - safe_update handles str or enum
    asset_svc.update_asset(db, slug, **kwargs)
    return templates.TemplateResponse(request, "assets.html", _ctx(db))


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_asset(request: Request, slug: str, db: Session = Depends(get_db)):
    asset_svc.delete_asset(db, slug)
    return templates.TemplateResponse(request, "assets.html", _ctx(db))
