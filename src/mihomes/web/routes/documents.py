"""Documents & playbooks routes."""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.authz.scope import current_role
from mihomes.models.document import DocumentType
from mihomes.services import document as doc_svc
from mihomes.services import property as prop_svc
from mihomes.web.deps import get_db, templates
from mihomes.web.forms import read_document_upload

router = APIRouter()

DOC_TYPE_LABELS = {
    "manual": "Manual",
    "warranty": "Warranty",
    "contract": "Contract",
    "insurance": "Insurance",
    "permit": "Permit",
    "regulation": "Regulation",
    "report": "Report",
    "other": "Other",
}

DOC_TYPE_COLORS = {
    "manual": "blue",
    "warranty": "emerald",
    "contract": "orange",
    "insurance": "sky",
    "permit": "amber",
    "regulation": "red",
    "report": "indigo",
    "other": "gray",
}


def _ctx(db: Session, type_filter: str = "", **kwargs) -> dict:
    dt = DocumentType(type_filter) if type_filter else None
    documents = doc_svc.list_documents(db, document_type=dt)
    expiring = doc_svc.list_expiring(db, days=60)
    expiring_ids = {d.id for d in expiring}

    # The access picker, built only for roles that may actually grant (SPEC-004).
    #
    # **Not merely a display choice.** `grantable_staff` reads the whole people directory, and
    # after U6b a staff member's `PERSONNEL` filter narrows `Staff` to their own row — so for
    # staff this would silently return a one-item list of themselves, which is a confusing thing
    # to render next to a control they cannot use. Gating on the role keeps the query off that
    # path entirely rather than relying on the filter to make it harmless.
    can_grant = current_role.get() in ("owner", "admin")
    grant_map: dict = {}
    grantable: list = []
    if can_grant:
        grantable = doc_svc.grantable_staff(db)
        for doc in documents:
            grant_map[doc.id] = {g.staff_id for g in doc_svc.list_access(db, str(doc.id))}

    return {
        "page": "documents",
        "documents": documents,
        "properties": prop_svc.list_properties(db),
        "doc_types": [(t.value, DOC_TYPE_LABELS.get(t.value, t.value.title())) for t in DocumentType],
        "doc_type_labels": DOC_TYPE_LABELS,
        "doc_type_colors": DOC_TYPE_COLORS,
        "type_filter": type_filter,
        "expiring_ids": expiring_ids,
        "can_grant": can_grant,
        "grantable_staff": grantable,
        "grant_map": grant_map,
        "access_error": None,
        "upload_error": None,
        **kwargs,
    }


@router.get("/")
@declares("inventory.manage", Access.COLLECTION)
def list_documents(request: Request, type: str = "", db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "documents.html", _ctx(db, type_filter=type))


async def _resolve_file_path(file: UploadFile | None, typed: str) -> str:
    """An uploaded file wins over the typed link/path; neither leaves the "—" placeholder.

    An empty file input still arrives as an `UploadFile` with no filename, so that is "no upload",
    not an error. Raises `ValueError` (from `read_document_upload`) for a disallowed or oversized
    file — the route renders it rather than 500ing.
    """
    if file is not None and file.filename:
        return await read_document_upload(file)
    return typed.strip() or "—"


@router.post("/")
@declares("inventory.manage", Access.ITEM)
async def create_document(
    request: Request,
    title: str = Form(...),
    document_type: str = Form(...),
    file_path: str = Form(""),
    notes: str | None = Form(None),
    expires_at: str | None = Form(None),
    property_id: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    try:
        stored_path = await _resolve_file_path(file, file_path)
    except ValueError as exc:
        return templates.TemplateResponse(request, "documents.html", _ctx(db, upload_error=str(exc)))
    doc_svc.create_document(
        db,
        title=title,
        file_path=stored_path,
        document_type=DocumentType(document_type),
        notes=notes or None,
        expires_at=date.fromisoformat(expires_at) if expires_at else None,
        entity_type="property" if property_id else None,
        entity_id=property_id or None,
    )
    return templates.TemplateResponse(request, "documents.html", _ctx(db))


@router.post("/{slug}/edit")
@declares("inventory.manage", Access.ITEM)
async def edit_document(
    request: Request,
    slug: str,
    title: str = Form(...),
    document_type: str = Form(...),
    file_path: str = Form(""),
    notes: str | None = Form(None),
    expires_at: str | None = Form(None),
    property_id: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    try:
        stored_path = await _resolve_file_path(file, file_path)
    except ValueError as exc:
        return templates.TemplateResponse(request, "documents.html", _ctx(db, upload_error=str(exc)))
    doc_svc.update_document(
        db, slug,
        title=title,
        document_type=DocumentType(document_type),
        file_path=stored_path,
        notes=notes or None,
        expires_at=date.fromisoformat(expires_at) if expires_at else None,
        entity_type="property" if property_id else None,
        entity_id=property_id or None,
    )
    return templates.TemplateResponse(request, "documents.html", _ctx(db))


@router.post("/{slug}/delete")
@declares("inventory.manage", Access.ITEM)
def delete_document(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
):
    doc_svc.delete_document(db, slug)
    return templates.TemplateResponse(request, "documents.html", _ctx(db))


# ── Per-person access grants — SPEC-004 ───────────────────────────────────────
#
# `document.grant`, not `inventory.manage`. The latter is `SCOPED` for staff and correctly so —
# a housekeeper may read an appliance manual for a property they cover — but deciding *who else*
# sees a document is the owner's call. Row 7 is split for exactly that reason, on the row-8
# precedent: a three-valued cell cannot say "you may read these rows but not administer who reads
# them".


@router.post("/{slug}/access")
@declares("document.grant", Access.ACCOUNT)
def grant_document_access(
    request: Request,
    slug: str,
    staff_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """Give one staff member access to one document.

    Errors are rendered back onto the page rather than raised: the only two failures are a staff
    id that does not resolve and a staff member with no MiHomes login, and both are things the
    operator can fix from this screen. A 500 would lose the rest of their work.
    """
    import uuid as _uuid

    try:
        doc_svc.grant_access(db, slug, _uuid.UUID(staff_id))
        error = None
    except (ValueError, TypeError) as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request, "documents.html", _ctx(db, access_error=error)
    )


@router.post("/{slug}/access/revoke")
@declares("document.grant", Access.ACCOUNT)
def revoke_document_access(
    request: Request,
    slug: str,
    staff_id: str = Form(...),
    db: Session = Depends(get_db),
):
    import uuid as _uuid

    try:
        doc_svc.revoke_access(db, slug, _uuid.UUID(staff_id))
        error = None
    except (ValueError, TypeError) as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request, "documents.html", _ctx(db, access_error=error)
    )
