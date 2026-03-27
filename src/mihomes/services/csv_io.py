"""CSV import/export service — bulk data operations."""

import csv
import io
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from mihomes.services.slug import resolve_identifier

EXPORT_FIELDS = {
    "property": ["id", "slug", "name", "address", "property_type", "status", "climate_zone", "sqft", "currency"],
    "staff": ["id", "slug", "name", "role", "phone", "email", "whatsapp_phone", "active"],
    "vendor": ["id", "slug", "company_name", "contact_name", "phone", "email", "service_categories"],
    "task": ["id", "slug", "title", "property_id", "priority", "status", "due_date", "assignee_id"],
    "issue": ["id", "slug", "title", "property_id", "severity", "status", "description"],
}


def export_csv(session: Session, entity_type: str) -> str:
    """Export entities as CSV string."""
    fields = EXPORT_FIELDS.get(entity_type)
    if not fields:
        raise ValueError(f"Export not supported for: {entity_type}. Supported: {', '.join(EXPORT_FIELDS.keys())}")

    model_class = _get_model(entity_type)
    entities = session.query(model_class).all()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for entity in entities:
        row = {}
        for field in fields:
            val = getattr(entity, field, None)
            if hasattr(val, "value"):  # Enum
                val = val.value
            if isinstance(val, list):
                val = "|".join(str(v) for v in val)
            if isinstance(val, date):
                val = val.isoformat()
            row[field] = val if val is not None else ""
        writer.writerow(row)

    return output.getvalue()


def generate_template(entity_type: str) -> str:
    """Generate an empty CSV template with headers."""
    fields = EXPORT_FIELDS.get(entity_type)
    if not fields:
        raise ValueError(f"Template not supported for: {entity_type}. Supported: {', '.join(EXPORT_FIELDS.keys())}")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    return output.getvalue()


def import_csv(session: Session, entity_type: str, csv_text: str) -> list[dict]:
    """Import entities from CSV. Returns list of created records as dicts."""
    model_class = _get_model(entity_type)
    reader = csv.DictReader(io.StringIO(csv_text))
    created = []
    errors = []

    for row_num, row in enumerate(reader, 2):  # 2 because header is row 1
        try:
            record = _create_from_row(session, entity_type, row)
            created.append(record)
        except (KeyError, ValueError, TypeError) as e:
            errors.append({"row": row_num, "error": str(e), "data": dict(row)})

    session.flush()
    return {"created": created, "errors": errors}


def _create_from_row(session: Session, entity_type: str, row: dict):
    """Create a single entity from a CSV row."""
    # Remove empty strings and id field
    clean = {k: v for k, v in row.items() if v and k != "id"}

    if entity_type == "property":
        from mihomes.services.property import create_property
        from mihomes.models.property import PropertyType
        ptype = PropertyType(clean.get("property_type", "other"))
        return create_property(
            session, clean["name"],
            address=clean.get("address"), property_type=ptype,
            climate_zone=clean.get("climate_zone"), currency=clean.get("currency", "USD"),
            slug=clean.get("slug"),
        )
    elif entity_type == "staff":
        from mihomes.services.staff import create_staff
        from mihomes.models.staff import StaffRole
        role = StaffRole(clean.get("role", "other"))
        return create_staff(
            session, clean["name"], role=role,
            phone=clean.get("phone"), email=clean.get("email"),
            whatsapp_phone=clean.get("whatsapp_phone"), slug=clean.get("slug"),
        )
    elif entity_type == "vendor":
        from mihomes.services.vendor import create_vendor
        cats = clean.get("service_categories", "").split("|") if clean.get("service_categories") else None
        return create_vendor(
            session, clean["company_name"],
            contact_name=clean.get("contact_name"), phone=clean.get("phone"),
            email=clean.get("email"), service_categories=cats, slug=clean.get("slug"),
        )
    elif entity_type == "task":
        from mihomes.services.task import create_task
        from mihomes.models.task import TaskPriority
        prio = TaskPriority(clean.get("priority", "medium"))
        due = date.fromisoformat(clean["due_date"]) if clean.get("due_date") else None
        return create_task(
            session, clean["title"], clean["property_id"],
            priority=prio, due_date=due, slug=clean.get("slug"),
        )
    elif entity_type == "issue":
        from mihomes.services.issue import create_issue
        from mihomes.models.issue import IssueSeverity
        sev = IssueSeverity(clean.get("severity", "medium"))
        return create_issue(
            session, clean["title"], clean["property_id"],
            severity=sev, description=clean.get("description"), slug=clean.get("slug"),
        )
    else:
        raise ValueError(f"Import not supported for: {entity_type}")


def _get_model(entity_type: str):
    from mihomes.models.property import Property
    from mihomes.models.staff import Staff
    from mihomes.models.vendor import Vendor
    from mihomes.models.task import Task
    from mihomes.models.issue import Issue
    return {"property": Property, "staff": Staff, "vendor": Vendor, "task": Task, "issue": Issue}[entity_type]
