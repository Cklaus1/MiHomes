"""Assets & Inventory routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.asset import AssetType, AssetCondition
from mihomes.services import asset as asset_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _properties_ctx(db: Session) -> dict:
    properties = prop_svc.list_properties(db)
    all_assets = asset_svc.list_assets(db, active_only=False)
    counts = {}
    for a in all_assets:
        counts[a.property_id] = counts.get(a.property_id, 0) + 1
    return {
        "page": "assets",
        "properties": properties,
        "asset_counts": counts,
    }


def _spaces_ctx(db: Session, property_slug: str) -> dict:
    prop = prop_svc.get_property(db, property_slug)
    spaces = space_svc.list_spaces(db, property_slug)
    assets = asset_svc.list_assets(db, property_id_or_slug=property_slug, active_only=False)
    space_counts: dict[int, int] = {}
    unassigned = 0
    for a in assets:
        if a.space_id:
            space_counts[a.space_id] = space_counts.get(a.space_id, 0) + 1
        else:
            unassigned += 1
    return {
        "page": "assets",
        "prop": prop,
        "spaces": spaces,
        "space_counts": space_counts,
        "unassigned_count": unassigned,
        "total": len(assets),
    }


def _list_ctx(db: Session, property_slug: str, space_slug: str, asset_type: str | None = None) -> dict:
    prop = prop_svc.get_property(db, property_slug)
    all_prop_assets = asset_svc.list_assets(db, property_id_or_slug=property_slug, active_only=False)
    if space_slug == "unassigned":
        space = None
        assets = [a for a in all_prop_assets if not a.space_id]
    else:
        space = space_svc.get_space(db, space_slug)
        assets = [a for a in all_prop_assets if a.space_id == space.id]
    if asset_type:
        assets = [a for a in assets if a.asset_type.value == asset_type]
    return {
        "page": "assets",
        "prop": prop,
        "space": space,
        "space_slug": space_slug,
        "assets": assets,
        "all_assets": all_prop_assets,
        "properties": prop_svc.list_properties(db),
        "spaces": space_svc.list_spaces(db, property_slug),
        "asset_types": [t.value for t in AssetType],
        "conditions": [c.value for c in AssetCondition],
        "notes_map": {a.id: note_svc.list_notes(db, f"asset:{a.id}") for a in assets},
        "filter_type": asset_type,
    }


# ── Level 1: property selector ────────────────────────────────────────────────

@router.get("/")
def asset_properties(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "assets_properties.html", _properties_ctx(db))


# ── Level 2: space selector ───────────────────────────────────────────────────

@router.get("/{property_slug}")
def asset_spaces(request: Request, property_slug: str, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "assets_spaces.html", _spaces_ctx(db, property_slug))


# ── Level 3: asset list ───────────────────────────────────────────────────────

@router.get("/{property_slug}/{space_slug}")
def asset_list(request: Request, property_slug: str, space_slug: str,
               asset_type: str | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "assets.html", _list_ctx(db, property_slug, space_slug, asset_type))


# ── Mutations ─────────────────────────────────────────────────────────────────

@router.post("/", response_class=HTMLResponse)
def create_asset(
    request: Request,
    name: str = Form(...),
    asset_type: str = Form(...),
    property_slug: str = Form(...),
    space_slug: str = Form(""),
    make: str = Form(""),
    model_name: str = Form(""),
    condition: str = Form("good"),
    purchase_price: str = Form(""),
    notes: str = Form(""),
    from_property: str = Form(""),
    from_space: str = Form(""),
    db: Session = Depends(get_db),
):
    asset_svc.create_asset(
        db,
        name=name,
        asset_type=AssetType(asset_type),
        property_id_or_slug=property_slug,
        space_id_or_slug=space_slug or None,
        make=make or None,
        model_name=model_name or None,
        condition=AssetCondition(condition),
        purchase_price=float(purchase_price) if purchase_price else None,
        notes=notes or None,
    )
    if from_property and from_space:
        return templates.TemplateResponse(request, "assets.html", _list_ctx(db, from_property, from_space))
    if from_property:
        return templates.TemplateResponse(request, "assets_spaces.html", _spaces_ctx(db, from_property))
    return templates.TemplateResponse(request, "assets_properties.html", _properties_ctx(db))


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
    from_property: str = Form(""),
    from_space: str = Form(""),
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
    if condition: kwargs["condition"] = condition
    asset_svc.update_asset(db, slug, **kwargs)
    if from_property and from_space:
        return templates.TemplateResponse(request, "assets.html", _list_ctx(db, from_property, from_space))
    if from_property:
        return templates.TemplateResponse(request, "assets_spaces.html", _spaces_ctx(db, from_property))
    return templates.TemplateResponse(request, "assets_properties.html", _properties_ctx(db))


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_asset(
    request: Request,
    slug: str,
    from_property: str = Form(""),
    from_space: str = Form(""),
    db: Session = Depends(get_db),
):
    asset_svc.delete_asset(db, slug)
    if from_property and from_space:
        return templates.TemplateResponse(request, "assets.html", _list_ctx(db, from_property, from_space))
    if from_property:
        return templates.TemplateResponse(request, "assets_spaces.html", _spaces_ctx(db, from_property))
    return templates.TemplateResponse(request, "assets_properties.html", _properties_ctx(db))
