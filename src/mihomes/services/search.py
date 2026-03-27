"""Search service — global search across all entity types."""

from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.models.vendor import Vendor
from mihomes.models.task import Task
from mihomes.models.issue import Issue
from mihomes.models.note import Note


def global_search(session: Session, query: str, *, entity_type: str | None = None) -> list[dict]:
    """Search across all entities for a text match. Returns list of {type, id, name, match_field}."""
    results = []
    q = f"%{query}%"

    if entity_type is None or entity_type == "property":
        for p in session.query(Property).filter(
            Property.name.ilike(q) | Property.address.ilike(q) | Property.features.ilike(q)
        ).all():
            results.append({"type": "property", "id": p.id, "name": p.name, "slug": p.slug})

    if entity_type is None or entity_type == "staff":
        for s in session.query(Staff).filter(Staff.name.ilike(q)).all():
            results.append({"type": "staff", "id": s.id, "name": s.name, "slug": s.slug})

    if entity_type is None or entity_type == "vendor":
        for v in session.query(Vendor).filter(
            Vendor.company_name.ilike(q) | Vendor.contact_name.ilike(q) | Vendor.notes.ilike(q)
        ).all():
            results.append({"type": "vendor", "id": v.id, "name": v.company_name, "slug": v.slug})

    if entity_type is None or entity_type == "task":
        for t in session.query(Task).filter(
            Task.title.ilike(q) | Task.description.ilike(q)
        ).all():
            results.append({"type": "task", "id": t.id, "name": t.title, "slug": t.slug})

    if entity_type is None or entity_type == "issue":
        for i in session.query(Issue).filter(
            Issue.title.ilike(q) | Issue.description.ilike(q) | Issue.resolution_notes.ilike(q)
        ).all():
            results.append({"type": "issue", "id": i.id, "name": i.title, "slug": i.slug})

    if entity_type is None or entity_type == "note":
        for n in session.query(Note).filter(Note.content.ilike(q)).all():
            results.append({"type": "note", "id": n.id, "name": n.content[:60], "slug": None})

    return results
