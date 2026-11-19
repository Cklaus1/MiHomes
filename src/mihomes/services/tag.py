"""Tag service — polymorphic tagging on any entity."""

from sqlalchemy.orm import Session

from mihomes.models.tag import Tag, TagAssignment
from mihomes.services.note import _get_model_class, _parse_entity_ref
from mihomes.services.slug import resolve_identifier


def create_tag(session: Session, name: str) -> Tag:
    """Get-or-create a tag **within the current account** (F4).

    The lookup is account-scoped for the same reason `ensure_unique_slug` is: this is a
    get-or-create against a column that is now unique *per account*, so an unscoped read
    would hand account B the Tag row belonging to account A — returning another tenant's
    primary key, not merely failing to create. G8.1's ambient read filter would also catch
    this, but a get-or-create must not depend on an ambient filter for correctness.
    """
    from mihomes.tenancy import require_account

    existing = (
        session.query(Tag)
        .filter(Tag.name == name.lower(), Tag.account_id == require_account())
        .first()
    )
    if existing:
        return existing
    tag = Tag(name=name.lower())
    session.add(tag)
    session.flush()
    return tag


def list_tags(session: Session) -> list[Tag]:
    return session.query(Tag).order_by(Tag.name).all()


def apply_tag(session: Session, tag_name: str, entity_refs: list[str]) -> int:
    """Apply a tag to one or more entities. Returns count applied."""
    tag = session.query(Tag).filter(Tag.name == tag_name.lower()).first()
    if tag is None:
        tag = create_tag(session, tag_name)

    count = 0
    for ref in entity_refs:
        entity_type, id_or_slug = _parse_entity_ref(ref)
        model_class = _get_model_class(entity_type)
        if model_class is None:
            continue
        entity = resolve_identifier(session, model_class, id_or_slug)

        # Check if already tagged
        existing = session.query(TagAssignment).filter(
            TagAssignment.tag_id == tag.id,
            TagAssignment.entity_type == entity_type,
            TagAssignment.entity_id == entity.id,
        ).first()
        if existing:
            continue

        assignment = TagAssignment(tag_id=tag.id, entity_type=entity_type, entity_id=entity.id)
        session.add(assignment)
        count += 1

    session.flush()
    return count


def get_tagged_entities(session: Session, tag_name: str) -> list[dict]:
    """Get all entities with a given tag."""
    tag = session.query(Tag).filter(Tag.name == tag_name.lower()).first()
    if tag is None:
        return []
    assignments = session.query(TagAssignment).filter(TagAssignment.tag_id == tag.id).all()
    results = []
    for a in assignments:
        model_class = _get_model_class(a.entity_type)
        if model_class is None:
            continue
        entity = session.get(model_class, a.entity_id)
        if entity:
            name = getattr(entity, "name", None) or getattr(entity, "title", None) or getattr(entity, "company_name", None) or str(entity.id)
            results.append({"type": a.entity_type, "id": a.entity_id, "name": name})
    return results


def remove_tag(session: Session, tag_name: str, entity_ref: str) -> bool:
    """Remove a tag from an entity."""
    tag = session.query(Tag).filter(Tag.name == tag_name.lower()).first()
    if tag is None:
        return False
    entity_type, id_or_slug = _parse_entity_ref(entity_ref)
    model_class = _get_model_class(entity_type)
    if model_class is None:
        return False
    entity = resolve_identifier(session, model_class, id_or_slug)
    assignment = session.query(TagAssignment).filter(
        TagAssignment.tag_id == tag.id,
        TagAssignment.entity_type == entity_type,
        TagAssignment.entity_id == entity.id,
    ).first()
    if assignment:
        session.delete(assignment)
        session.flush()
        return True
    return False
