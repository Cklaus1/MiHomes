"""Search service — global search across all entity types."""

from sqlalchemy.orm import Session

from mihomes.models.asset import Asset
from mihomes.models.document import Document
from mihomes.models.event import Event, Guest
from mihomes.models.issue import Issue
from mihomes.models.note import Note
from mihomes.models.property import Property
from mihomes.models.staff import Staff, category_for_role
from mihomes.models.task import Task
from mihomes.models.vendor import Vendor
from mihomes.models.work_order import WorkOrder

# Directory category → search result type (all link to /staff/).
_PERSON_TYPE = {
    "Staff": "staff",
    "Resident": "resident",
    "Associate": "associate",
    "Family / Owner": "family",
}


def global_search(session: Session, query: str, *, entity_type: str | None = None) -> list[dict]:
    """Search across all entities for a text match.

    Returns list of {type, id, name, slug}. NULL columns are handled
    safely — ILIKE on NULL returns no match (correct behavior).
    """
    results = []
    q = f"%{query}%"

    if entity_type is None or entity_type == "property":
        for p in session.query(Property).filter(
            Property.name.ilike(q) | Property.address.ilike(q) | Property.features.ilike(q)
        ).all():
            results.append({"type": "property", "id": p.id, "name": p.name, "slug": p.slug})

    if entity_type is None or entity_type == "staff":
        for s in session.query(Staff).filter(Staff.name.ilike(q)).all():
            # The staff table backs the whole Directory; label each result by
            # its derived category so residents/owners aren't shown as "staff".
            results.append({"type": _PERSON_TYPE.get(category_for_role(s.role), "staff"),
                            "id": s.id, "name": s.name, "slug": s.slug})

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

    if entity_type is None or entity_type == "asset":
        for a in session.query(Asset).filter(
            Asset.name.ilike(q) | Asset.make.ilike(q) | Asset.model_name.ilike(q) | Asset.notes.ilike(q)
        ).all():
            results.append({"type": "asset", "id": a.id, "name": a.name, "slug": a.slug})

    if entity_type is None or entity_type == "workorder":
        for w in session.query(WorkOrder).filter(
            WorkOrder.title.ilike(q) | WorkOrder.description.ilike(q) | WorkOrder.completion_notes.ilike(q)
        ).all():
            results.append({"type": "workorder", "id": w.id, "name": w.title, "slug": w.slug})

    if entity_type is None or entity_type == "event":
        for e in session.query(Event).filter(
            Event.title.ilike(q) | Event.description.ilike(q)
        ).all():
            results.append({"type": "event", "id": e.id, "name": e.title, "slug": e.slug})

    if entity_type is None or entity_type == "guest":
        for g in session.query(Guest).filter(
            Guest.name.ilike(q) | Guest.notes.ilike(q)
        ).all():
            results.append({"type": "guest", "id": g.id, "name": g.name, "slug": g.slug})

    if entity_type is None or entity_type == "document":
        for d in session.query(Document).filter(
            Document.title.ilike(q) | Document.notes.ilike(q)
        ).all():
            results.append({"type": "document", "id": d.id, "name": d.title, "slug": d.slug})

    if entity_type is None or entity_type == "note":
        for n in session.query(Note).filter(Note.content.ilike(q)).all():
            results.append({"type": "note", "id": n.id, "name": n.content[:60], "slug": None})

    return results
