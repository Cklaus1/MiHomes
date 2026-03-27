"""Slug generation and entity resolution."""

from slugify import slugify
from sqlalchemy.orm import Session


class EntityNotFoundError(Exception):
    """Raised when an entity cannot be found by ID or slug."""

    def __init__(self, entity_type: str, identifier: str):
        self.entity_type = entity_type
        self.identifier = identifier
        super().__init__(f"{entity_type} '{identifier}' not found")


def generate_slug(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    return slugify(name, max_length=80)


def ensure_unique_slug(
    session: Session,
    model_class,
    slug: str,
    exclude_id: int | None = None,
) -> str:
    """Ensure a slug is unique within its table, appending -2, -3, etc. if needed."""
    candidate = slug
    suffix = 2
    while True:
        query = session.query(model_class).filter(model_class.slug == candidate)
        if exclude_id is not None:
            query = query.filter(model_class.id != exclude_id)
        if query.first() is None:
            return candidate
        candidate = f"{slug}-{suffix}"
        suffix += 1


def resolve_identifier(session: Session, model_class, id_or_slug: str):
    """Resolve an ID or slug to an ORM instance. Raises EntityNotFoundError if not found."""
    # Try as integer ID first
    try:
        pk = int(id_or_slug)
        instance = session.get(model_class, pk)
        if instance is not None:
            return instance
    except (ValueError, TypeError):
        pass

    # Try as slug
    instance = (
        session.query(model_class).filter(model_class.slug == id_or_slug).first()
    )
    if instance is not None:
        return instance

    entity_type = _singularize(model_class.__tablename__)
    raise EntityNotFoundError(entity_type, id_or_slug)


_SINGULARS = {
    "properties": "property",
    "staff": "staff",
    "vendors": "vendor",
    "tasks": "task",
    "issues": "issue",
    "spaces": "space",
    "assets": "asset",
    "templates": "template",
    "events": "event",
    "guests": "guest",
    "documents": "document",
    "work_orders": "work order",
    "insurance_policies": "insurance policy",
}


def _singularize(table_name: str) -> str:
    return _SINGULARS.get(table_name, table_name.rstrip("s"))
