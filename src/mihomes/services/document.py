"""Document service — CRUD with polymorphic entity linking, and per-person access grants."""

import uuid
from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.models.document import Document, DocumentType
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.update_helpers import safe_update

# Allowed entity types for polymorphic linking
VALID_ENTITY_TYPES = {
    "property", "asset", "vendor", "work_order", "contract",
    "insurance", "event", "staff", "space",
}


def _validate_entity(entity_type: str | None, entity_id: int | None) -> None:
    """Validate polymorphic entity type/id pair."""
    if entity_type and entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity_type '{entity_type}'. "
            f"Allowed: {sorted(VALID_ENTITY_TYPES)}"
        )
    if entity_type and not entity_id:
        raise ValueError("entity_id is required when entity_type is set")
    if entity_id and not entity_type:
        raise ValueError("entity_type is required when entity_id is set")


def _validate_file_path(file_path: str) -> None:
    """Reject parent-directory traversal segments in the path.

    Checks the path's components for a literal ".." segment rather than a raw
    substring search — the latter both missed segment-only traversal and false-
    flagged legitimate names like "report..final.pdf".
    """
    from pathlib import PurePath
    if ".." in PurePath(file_path).parts:
        raise ValueError(f"Path traversal detected in file path: {file_path}")


def create_document(
    session: Session,
    title: str,
    file_path: str,
    document_type: DocumentType,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    expires_at: date | None = None,
    notes: str | None = None,
    slug: str | None = None,
) -> Document:
    _validate_entity(entity_type, entity_id)
    _validate_file_path(file_path)
    slug = ensure_unique_slug(session, Document, slug or generate_slug(title))
    doc = Document(
        title=title, slug=slug, file_path=file_path, document_type=document_type,
        entity_type=entity_type, entity_id=entity_id,
        expires_at=expires_at, notes=notes,
    )
    session.add(doc)
    session.flush()
    record_change(session, "document", doc.id, "create", snapshot_instance(doc))
    return doc


def list_documents(
    session: Session,
    *,
    document_type: DocumentType | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> list[Document]:
    query = session.query(Document)
    if document_type:
        query = query.filter(Document.document_type == document_type)
    if entity_type:
        query = query.filter(Document.entity_type == entity_type)
    if entity_id:
        query = query.filter(Document.entity_id == entity_id)
    return query.order_by(Document.created_at.desc()).all()


def get_document(session: Session, id_or_slug: str) -> Document:
    return resolve_identifier(session, Document, id_or_slug)


def update_document(session: Session, id_or_slug: str, **kwargs) -> Document:
    doc = resolve_identifier(session, Document, id_or_slug)
    old_snap = snapshot_instance(doc)
    if "title" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Document, generate_slug(kwargs["title"]), exclude_id=doc.id)
    if "entity_type" in kwargs or "entity_id" in kwargs:
        _validate_entity(
            kwargs.get("entity_type", doc.entity_type),
            kwargs.get("entity_id", doc.entity_id),
        )
    safe_update(doc, kwargs)
    session.flush()
    new_snap = snapshot_instance(doc)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "document", doc.id, "update", changes)
    return doc


def delete_document(session: Session, id_or_slug: str) -> str:
    doc = resolve_identifier(session, Document, id_or_slug)
    name = doc.title
    record_change(session, "document", doc.id, "delete", snapshot_instance(doc))
    session.delete(doc)
    session.flush()
    return name


def list_expiring(session: Session, days: int = 30) -> list[Document]:
    """List documents expiring within the given number of days."""
    cutoff = date.today() + timedelta(days=days)
    return session.query(Document).filter(
        Document.expires_at != None,  # noqa: E711
        Document.expires_at <= cutoff,
        Document.expires_at >= date.today(),
    ).order_by(Document.expires_at).all()


# ── Per-person access grants — SPEC-004 ───────────────────────────────────────
#
# The owner (or an admin) decides which staff member sees which document. This replaced D13's
# `staff_visible` boolean: one flag per document meant a ticked document was visible to *every*
# staff member in scope, and an estate has paperwork appropriate for one person and not another.
# `authz/query_scope.py::_document_criteria` is what reads these rows.
#
# **These functions do no permission checking of their own, deliberately.** The route declares
# `document.grant` and the app-level dependency settles it before the endpoint body runs (§9.4
# step 0); a second check here would either duplicate the matrix or drift from it. What *is*
# enforced here is the shape of a usable grant — see `grantable_staff`.


def grantable_staff(session: Session) -> list:
    """Staff who can actually hold a grant: those with a linked MiHomes login.

    **A grant to a staff row with no `user_id` matches nothing, ever.** The criteria resolves a
    request's `current_user` to a staff row through `staff.user_id` (the link SPEC-003 U6a added),
    so a person with no login cannot be the subject of any request — the grant would sit in the
    table looking active and authorise nothing.

    Offering such a person in the picker would therefore be offering a control that silently does
    not work, which is worse than not offering them: the owner would tick a box, see no error, and
    reasonably conclude the person has access. Filtering here means the UI can say *"invite them
    first"* instead.
    """
    from mihomes.models.staff import Staff

    return (
        session.query(Staff)
        .filter(Staff.user_id.is_not(None), Staff.active.is_(True))
        .order_by(Staff.name)
        .all()
    )


def list_access(session: Session, id_or_slug: str) -> list:
    """Every grant on one document, for the owner-facing picker."""
    from mihomes.models.document_access import DocumentAccess

    doc = resolve_identifier(session, Document, id_or_slug)
    return (
        session.query(DocumentAccess)
        .filter(DocumentAccess.document_id == doc.id)
        .all()
    )


def grant_access(session: Session, id_or_slug: str, staff_id: uuid.UUID):
    """Give one staff member access to one document. Idempotent.

    Returning the existing row rather than raising on a repeat: the caller is a checkbox, and
    ticking an already-ticked box is not an error. The unique constraint
    (`account_id`, `document_id`, `staff_id`) is the backstop if a concurrent request races.

    Audited, because *"who gave this person access to this"* is a question worth being able to
    answer after the fact — and the grant row carries `granted_by` as well, since reconstructing
    current state from an event log is a different and worse job than reading it off the row.
    """
    from mihomes.models.document_access import DocumentAccess
    from mihomes.models.staff import Staff

    doc = resolve_identifier(session, Document, id_or_slug)

    member = session.get(Staff, staff_id)
    if member is None:
        # The tenant filter makes this the cross-account case too: another account's staff id
        # resolves to None here rather than to a row, so the message is the same and correct.
        raise ValueError(f"No such staff member: {staff_id}")
    if member.user_id is None:
        raise ValueError(
            f"{member.name} has no MiHomes login, so a grant would never take effect. "
            "Invite them first, then grant access."
        )

    existing = (
        session.query(DocumentAccess)
        .filter(
            DocumentAccess.document_id == doc.id,
            DocumentAccess.staff_id == staff_id,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing

    grant = DocumentAccess(
        document_id=doc.id,
        staff_id=staff_id,
        granted_by=_current_user_or_none(),
    )
    session.add(grant)
    session.flush()
    record_change(
        session, "document", doc.id, "update",
        {"access_granted": {"old": None, "new": member.name}},
    )
    return grant


def revoke_access(session: Session, id_or_slug: str, staff_id: uuid.UUID) -> bool:
    """Remove one grant. Returns whether a row was actually removed.

    Idempotent in the same way `grant_access` is, and for the same reason — unticking an unticked
    box is not an error. The boolean lets a caller distinguish "revoked" from "was not granted"
    without that distinction being an exception.
    """
    from mihomes.models.document_access import DocumentAccess
    from mihomes.models.staff import Staff

    doc = resolve_identifier(session, Document, id_or_slug)
    grant = (
        session.query(DocumentAccess)
        .filter(
            DocumentAccess.document_id == doc.id,
            DocumentAccess.staff_id == staff_id,
        )
        .one_or_none()
    )
    if grant is None:
        return False

    member = session.get(Staff, staff_id)
    session.delete(grant)
    session.flush()
    record_change(
        session, "document", doc.id, "update",
        {"access_revoked": {"old": member.name if member else str(staff_id), "new": None}},
    )
    return True


def _current_user_or_none() -> uuid.UUID | None:
    """The acting user, or None outside a request (the CLI, a background job).

    `current_user` raises `LookupError` when unbound rather than returning None, so the absence
    has to be caught rather than tested for — the same shape `query_scope` uses.
    """
    from mihomes.tenancy.context import current_user

    try:
        return current_user.get()
    except LookupError:
        return None
