"""Note service — polymorphic notes on any entity."""

from sqlalchemy.orm import Session

from mihomes.models.note import Note
from mihomes.services.audit import record_change

# Map entity types to their model classes for validation
ENTITY_TYPE_MAP = {}


def _get_model_class(entity_type: str):
    """Lazy-load entity type map to avoid circular imports."""
    if not ENTITY_TYPE_MAP:
        from mihomes.models.property import Property
        from mihomes.models.staff import Staff
        from mihomes.models.vendor import Vendor
        from mihomes.models.task import Task
        from mihomes.models.issue import Issue
        from mihomes.models.asset import Asset
        from mihomes.models.work_order import WorkOrder
        from mihomes.models.event import Event, Guest
        from mihomes.models.document import Document
        from mihomes.models.contract import Contract
        ENTITY_TYPE_MAP.update({
            "property": Property,
            "staff": Staff,
            "vendor": Vendor,
            "task": Task,
            "issue": Issue,
            "asset": Asset,
            "workorder": WorkOrder,
            "contract": Contract,
            "event": Event,
            "guest": Guest,
            "document": Document,
        })
    return ENTITY_TYPE_MAP.get(entity_type)


def add_note(
    session: Session,
    entity_ref: str,
    content: str,
) -> Note:
    """Add a note. entity_ref format: 'entity_type:id_or_slug' (e.g., 'property:beach-house')."""
    entity_type, id_or_slug = _parse_entity_ref(entity_ref)
    model_class = _get_model_class(entity_type)
    if model_class is None:
        raise ValueError(f"Unknown entity type: {entity_type}")

    from mihomes.services.slug import resolve_identifier
    entity = resolve_identifier(session, model_class, id_or_slug)

    note = Note(entity_type=entity_type, entity_id=entity.id, content=content)
    session.add(note)
    session.flush()
    record_change(session, "note", note.id, "create", {"entity": entity_ref, "content": content})
    return note


def list_notes(session: Session, entity_ref: str) -> list[Note]:
    entity_type, id_or_slug = _parse_entity_ref(entity_ref)
    model_class = _get_model_class(entity_type)
    if model_class is None:
        raise ValueError(f"Unknown entity type: {entity_type}")

    from mihomes.services.slug import resolve_identifier
    entity = resolve_identifier(session, model_class, id_or_slug)

    return session.query(Note).filter(
        Note.entity_type == entity_type,
        Note.entity_id == entity.id,
    ).order_by(Note.created_at.desc()).all()


def update_note(session: Session, note_id: int, content: str) -> Note:
    note = session.get(Note, note_id)
    if note is None:
        raise ValueError(f"Note {note_id} not found")
    note.content = content
    session.flush()
    record_change(session, "note", note.id, "update", {"content": content})
    return note


def delete_note(session: Session, note_id: int) -> None:
    note = session.get(Note, note_id)
    if note is None:
        raise ValueError(f"Note {note_id} not found")
    record_change(session, "note", note.id, "delete", {"content": note.content})
    session.delete(note)
    session.flush()


def _parse_entity_ref(ref: str) -> tuple[str, str]:
    """Parse 'entity_type:id_or_slug' format."""
    if ":" not in ref:
        raise ValueError(f"Invalid entity reference '{ref}'. Use format 'entity_type:id_or_slug' (e.g., 'property:beach-house')")
    parts = ref.split(":", 1)
    return parts[0].lower(), parts[1]
