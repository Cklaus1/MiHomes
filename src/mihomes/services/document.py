"""Document service — CRUD with polymorphic entity linking."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.models.document import Document, DocumentType
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.update_helpers import safe_update
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier

# Allowed entity types for polymorphic linking
VALID_ENTITY_TYPES = {
    "property", "asset", "vendor", "work_order", "contract",
    "insurance", "event", "staff",
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
    """Validate file path doesn't contain traversal attacks."""
    from pathlib import Path
    normalized = str(Path(file_path).resolve())
    if ".." in file_path:
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
