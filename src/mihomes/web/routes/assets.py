"""Assets & Inventory routes."""

import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.asset import AssetCondition, AssetType
from mihomes.models.book import BookCondition
from mihomes.models.document import DocumentType
from mihomes.services import asset as asset_svc
from mihomes.services import book as book_svc
from mihomes.services import document as doc_svc
from mihomes.services import issue as issue_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services.ai.assessors import parse_room_scan
from mihomes.web.deps import get_db, templates
from mihomes.web.forms import parse_money, read_document_upload, read_image_uploads

router = APIRouter()

_MEDIA_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}


def _save_room_photo(att) -> str:
    """Store a room-scan photo and return its storage key (G11 · A14).

    Was written directly under ``UPLOADS_DIR`` and referenced as ``/uploads/<name>``, which the
    unauthenticated static mount served to anyone. Now goes through the storage provider under a
    tenant-prefixed key, like every other object.
    """
    import base64 as _b64

    from mihomes.web.forms import _store_bytes

    return _store_bytes(
        _b64.b64decode(att.base64_data),
        getattr(att, "filename", None) or "room-scan.png",
        content_type=getattr(att, "content_type", None) or "image/png",
    )


def _ai_scan_error(msg: str) -> str:
    lower = msg.lower()
    if any(k in lower for k in ("not found", "not configured", "no provider", "api key", "authentication")):
        return "AI isn't configured. Set up a Claude API key (mihomes ai setup) to scan rooms."
    return msg


_SPACE_TYPES = ["bedroom", "bathroom", "kitchen", "living", "dining", "office", "entertainment", "recreation", "storage", "garage", "outdoor", "other"]


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
        "space_types": _SPACE_TYPES,
    }


def _spaces_ctx(db: Session, property_slug: str) -> dict:
    prop = prop_svc.get_property(db, property_slug)
    spaces = space_svc.list_spaces(db, property_slug)
    assets = asset_svc.list_assets(db, property_id_or_slug=property_slug, active_only=False)
    space_counts: dict[int, int] = {}
    unassigned = 0
    for a in assets:
        if a.space_id is not None:
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
        "space_types": _SPACE_TYPES,
    }


def _list_ctx(db: Session, property_slug: str, space_slug: str, asset_type: str | None = None) -> dict:
    prop = prop_svc.get_property(db, property_slug)
    all_prop_assets = asset_svc.list_assets(db, property_id_or_slug=property_slug, active_only=False)
    if space_slug == "unassigned":
        space = None
        assets = [a for a in all_prop_assets if a.space_id is None]
    else:
        space = space_svc.get_space(db, space_slug)
        assets = [a for a in all_prop_assets if a.space_id == space.id]
    if asset_type:
        assets = [a for a in assets if a.asset_type.value == asset_type]
    spaces = space_svc.list_spaces(db, property_slug)
    books = book_svc.list_books(db, property_id_or_slug=property_slug,
                                space_id_or_slug=None if space_slug == "unassigned" else (space.slug if space else None))
    return {
        "page": "assets",
        "prop": prop,
        "space": space,
        "space_slug": space_slug,
        "assets": assets,
        "all_assets": all_prop_assets,
        "properties": prop_svc.list_properties(db),
        "spaces": spaces,
        "asset_types": [t.value for t in AssetType],
        "conditions": [c.value for c in AssetCondition],
        "book_conditions": [c.value for c in BookCondition],
        "notes_map": {a.id: note_svc.list_notes(db, f"asset:{a.id}") for a in assets},
        "asset_docs_map": {a.id: doc_svc.list_documents(db, entity_type="asset", entity_id=a.id) for a in assets},
        "filter_type": asset_type,
        "books": books,
        "active_tab": "assets",
        "space_issues": [i for i in issue_svc.list_issues(db, property_id_or_slug=property_slug) if space and i.space_id == space.id] if space else [],
        "space_notes": note_svc.list_notes(db, f"space:{space.id}") if space else [],
    }


# ── Level 1: property selector ────────────────────────────────────────────────

@router.get("/")
def asset_properties(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "assets_properties.html", _properties_ctx(db))


@router.post("/{property_slug}/{space_slug}/edit-room", response_class=HTMLResponse)
def edit_room(
    request: Request,
    property_slug: str,
    space_slug: str,
    name: str = Form(...),
    space_type: str = Form(""),
    db: Session = Depends(get_db),
):
    space_svc.update_space(db, space_slug, name=name, space_type=space_type or None)
    return templates.TemplateResponse(request, "assets_spaces.html", _spaces_ctx(db, property_slug))


@router.post("/{property_slug}/{space_slug}/delete-room", response_class=HTMLResponse)
def delete_room(
    request: Request,
    property_slug: str,
    space_slug: str,
    db: Session = Depends(get_db),
):
    space_svc.delete_space(db, space_slug)
    return templates.TemplateResponse(request, "assets_spaces.html", _spaces_ctx(db, property_slug))


@router.post("/create-room", response_class=HTMLResponse)
def create_room(
    request: Request,
    name: str = Form(...),
    property_slug: str = Form(...),
    space_type: str = Form(""),
    db: Session = Depends(get_db),
):
    space_svc.create_space(db, name, property_slug, space_type=space_type or None)
    return templates.TemplateResponse(request, "assets_spaces.html", _spaces_ctx(db, property_slug))


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


# ── Scan Room: camera → AI vision → bulk add ──────────────────────────────────

def _scan_review_ctx(property_slug: str, space_slug: str, room_name: str,
                     items: list[dict], photo_path: str = "", error: str | None = None) -> dict:
    return {
        "property_slug": property_slug,
        "space_slug": space_slug,
        "room_name": room_name,
        "items": items,
        "photo_path": photo_path,
        "error": error,
        "asset_types": [t.value for t in AssetType],
        "conditions": [c.value for c in AssetCondition],
    }


@router.post("/scan", response_class=HTMLResponse)
async def scan_room(
    request: Request,
    property_slug: str = Form(...),
    space_slug: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    prop = prop_svc.get_property(db, property_slug)
    space = space_svc.get_space(db, space_slug) if space_slug and space_slug != "unassigned" else None
    room_name = space.name if space else prop.name

    items: list[dict] = []
    photo_path = ""
    error = None
    try:
        attachments = await read_image_uploads(files)
        photo_path = _save_room_photo(attachments[0])
        items = parse_room_scan(db, attachments, room_name=room_name)
        if not items:
            error = "No assets detected. Try a clearer or wider shot of the room."
    except ValueError as e:
        error = str(e)  # upload validation / provider guard — already user-facing
    except Exception as e:  # external AI call boundary (mirrors ai.py)
        error = _ai_scan_error(str(e))

    return templates.TemplateResponse(
        request, "partials/asset_scan_review.html",
        _scan_review_ctx(property_slug, space_slug, room_name, items, photo_path, error),
    )


@router.post("/scan/confirm", response_class=HTMLResponse)
def scan_confirm(
    request: Request,
    property_slug: str = Form(...),
    space_slug: str = Form(""),
    photo_path: str = Form(""),
    include: list[str] = Form(default=[]),
    name: list[str] = Form(default=[]),
    asset_type: list[str] = Form(default=[]),
    condition: list[str] = Form(default=[]),
    value: list[str] = Form(default=[]),
    note: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    space_arg = None if (not space_slug or space_slug == "unassigned") else space_slug
    include_set = set(include)
    created = 0
    for i in range(len(name)):
        if str(i) not in include_set or not name[i].strip():
            continue
        try:
            at = AssetType(asset_type[i]) if i < len(asset_type) and asset_type[i] else AssetType.EQUIPMENT
        except ValueError:
            at = AssetType.EQUIPMENT
        try:
            cond = AssetCondition(condition[i]) if i < len(condition) and condition[i] else AssetCondition.GOOD
        except ValueError:
            cond = AssetCondition.GOOD
        try:
            val = parse_money(value[i]) if i < len(value) else None
        except ValueError:
            val = None
        asset_svc.create_asset(
            db,
            name=name[i].strip(),
            asset_type=at,
            property_id_or_slug=property_slug,
            space_id_or_slug=space_arg,
            condition=cond,
            purchase_price=val,
            notes=(note[i].strip() or None) if i < len(note) else None,
        )
        created += 1

    # Keep one room reference photo, linked to the room (or property if unassigned).
    if photo_path and created:
        if space_arg:
            space = space_svc.get_space(db, space_arg)
            ent_type, ent_id = "space", space.id
        else:
            ent_type, ent_id = "property", prop_svc.get_property(db, property_slug).id
        doc_svc.create_document(
            db,
            title=f"Room scan — {ent_type} {ent_id}",
            file_path=photo_path,
            document_type=DocumentType.OTHER,
            entity_type=ent_type,
            entity_id=ent_id,
        )

    return templates.TemplateResponse(request, "assets.html", _list_ctx(db, property_slug, space_slug or "unassigned"))


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
def delete_note(request: Request, slug: str, note_id: uuid.UUID, db: Session = Depends(get_db)):
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


@router.post("/{slug}/price-entries", response_class=HTMLResponse)
def add_price_entry(
    request: Request,
    slug: str,
    price: str = Form(...),
    entry_date: str = Form(...),
    quantity: str = Form("1"),
    entry_type: str = Form("purchase"),
    note: str = Form(""),
    from_property: str = Form(""),
    from_space: str = Form(""),
    db: Session = Depends(get_db),
):
    from datetime import date as date_type
    asset_svc.add_price_entry(
        db,
        slug,
        float(price),
        date_type.fromisoformat(entry_date),
        quantity=float(quantity) if quantity else 1.0,
        entry_type=entry_type,
        note=note or None,
    )
    if from_property and from_space:
        return templates.TemplateResponse(request, "assets.html", _list_ctx(db, from_property, from_space))
    if from_property:
        return templates.TemplateResponse(request, "assets_spaces.html", _spaces_ctx(db, from_property))
    return templates.TemplateResponse(request, "assets_properties.html", _properties_ctx(db))


@router.post("/{slug}/documents", response_class=HTMLResponse)
async def add_asset_document(
    request: Request,
    slug: str,
    title: str = Form(...),
    doc_type: str = Form("photo"),
    file: UploadFile = File(...),
    from_property: str = Form(""),
    from_space: str = Form(""),
    db: Session = Depends(get_db),
):
    asset = asset_svc.get_asset(db, slug)
    ctx = {
        "post_url": f"/assets/{slug}/documents",
        "delete_url_prefix": f"/assets/{slug}/documents",
    }
    try:
        file_path = await read_document_upload(file)
    except ValueError as e:
        ctx["docs"] = doc_svc.list_documents(db, entity_type="asset", entity_id=asset.id)
        ctx["error"] = str(e)
        return templates.TemplateResponse(request, "partials/docs_section.html", ctx)
    doc_svc.create_document(
        db, title=title, file_path=file_path,
        document_type=DocumentType(doc_type),
        entity_type="asset", entity_id=asset.id,
    )
    ctx["docs"] = doc_svc.list_documents(db, entity_type="asset", entity_id=asset.id)
    return templates.TemplateResponse(request, "partials/docs_section.html", ctx)


@router.delete("/{slug}/documents/{doc_id}", response_class=HTMLResponse)
def delete_asset_document(
    request: Request,
    slug: str,
    doc_id: str,
    db: Session = Depends(get_db),
):
    doc_svc.delete_document(db, doc_id)
    asset = asset_svc.get_asset(db, slug)
    docs = doc_svc.list_documents(db, entity_type="asset", entity_id=asset.id)
    return templates.TemplateResponse(request, "partials/docs_section.html", {
        "docs": docs,
        "post_url": f"/assets/{slug}/documents",
        "delete_url_prefix": f"/assets/{slug}/documents",
    })


@router.post("/{property_slug}/{space_slug}/notes", response_class=HTMLResponse)
def add_space_note(
    request: Request,
    property_slug: str,
    space_slug: str,
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    space = space_svc.get_space(db, space_slug)
    note_svc.add_note(db, f"space:{space.id}", content)
    notes = note_svc.list_notes(db, f"space:{space.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/assets/{property_slug}/{space_slug}/notes",
        "delete_url_prefix": f"/assets/{property_slug}/{space_slug}/notes",
    })


@router.delete("/{property_slug}/{space_slug}/notes/{note_id}", response_class=HTMLResponse)
def delete_space_note(
    request: Request,
    property_slug: str,
    space_slug: str,
    note_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    note_svc.delete_note(db, note_id)
    space = space_svc.get_space(db, space_slug)
    notes = note_svc.list_notes(db, f"space:{space.id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/assets/{property_slug}/{space_slug}/notes",
        "delete_url_prefix": f"/assets/{property_slug}/{space_slug}/notes",
    })


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
