"""Slug generation and entity resolution."""

import uuid

from slugify import slugify
from sqlalchemy.orm import Session

from mihomes.services.query_helpers import escape_like


class EntityNotFoundError(ValueError):
    """Raised when an entity cannot be found by ID or slug."""

    def __init__(self, entity_type: str, identifier: str):
        self.entity_type = entity_type
        self.identifier = identifier
        super().__init__(f"{entity_type} '{identifier}' not found")


def generate_slug(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    slug = slugify(name, max_length=80)
    if not slug:
        # Name was all special characters — generate a fallback
        import hashlib
        slug = "item-" + hashlib.md5(name.encode()).hexdigest()[:8]
    return slug


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


class AmbiguousIdentifierError(ValueError):
    """Raised when a partial slug matches multiple entities.

    Subclasses ``ValueError`` (like ``EntityNotFoundError``) so that an
    ``except ValueError`` in any route catches an ambiguous prefix uniformly
    with a not-found id, instead of letting it escape as a 500 (M40).
    """

    def __init__(self, entity_type: str, identifier: str, matches: list):
        self.entity_type = entity_type
        self.identifier = identifier
        self.matches = matches
        slugs = ", ".join(m.slug for m in matches[:5])
        super().__init__(f"'{identifier}' is ambiguous — matches: {slugs}")


def resolve_identifier(session: Session, model_class, id_or_slug: str):
    """Resolve an ID or slug to an ORM instance. Raises EntityNotFoundError if not found.

    Supports:
    - UUID primary key (SPEC-002 D2)
    - Exact slug match
    - Prefix slug match (unambiguous only)
    """
    # Try as a UUID primary key first.
    #
    # This was `int(id_or_slug)` before SPEC-002 G6.1 converted the primary keys to
    # UUIDv7. A UUID string raises ValueError there, so every lookup silently fell
    # through to slug matching and failed with EntityNotFoundError — the id path was
    # simply gone. Nothing in the spec flags this; it surfaced as 24 test failures
    # once the fixtures could insert rows at all.
    #
    # `int` is no longer accepted deliberately: no table has an integer PK now, so
    # accepting one would only mask a caller still passing a stale id.
    try:
        pk = uuid.UUID(str(id_or_slug))
    except (ValueError, TypeError, AttributeError):
        pass
    else:
        instance = session.get(model_class, pk)
        if instance is not None:
            return instance

    # Try as exact slug
    instance = (
        session.query(model_class).filter(model_class.slug == id_or_slug).first()
    )
    if instance is not None:
        return instance

    # Try as prefix match (e.g., "a-j-land" → "a-j-landscaping-tree-service-llc").
    # M10: escape LIKE wildcards so a %/_ in the identifier can't broaden the
    # prefix into an unintended (possibly ambiguous) match.
    matches = (
        session.query(model_class)
        .filter(model_class.slug.like(f"{escape_like(id_or_slug)}%", escape="\\"))
        .all()
    )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        entity_type = _singularize(model_class.__tablename__)
        raise AmbiguousIdentifierError(entity_type, id_or_slug, matches)

    entity_type = _singularize(model_class.__tablename__)
    raise EntityNotFoundError(entity_type, id_or_slug)


_SINGULARS = {
    "properties": "property",
    "staff": "staff",
    "vendors": "vendor",
    "tasks": "task",
    "issues": "issue",
    "spaces": "space",
    "zones": "zone",
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
