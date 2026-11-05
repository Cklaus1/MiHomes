"""CSV import/export service — bulk data operations."""

import csv
import io
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, datetime

from sqlalchemy.orm import Session

from mihomes.services.slug import resolve_identifier

EXPORT_FIELDS = {
    "property": ["id", "slug", "name", "address", "property_type", "status", "climate_zone", "sqft", "currency"],
    "staff": ["id", "slug", "name", "role", "phone", "email", "whatsapp_phone", "active"],
    "vendor": ["id", "slug", "company_name", "contact_name", "phone", "email", "service_categories"],
    "task": [
        "id", "slug", "title", "property_id", "priority", "status", "due_date", "assignee_id",
        "description", "category", "estimated_hours", "zone_id",
        "completed_at", "completion_notes",
        "recurrence_frequency", "recurrence_next_due", "recurrence_last_generated", "recurrence_cron", "recurrence_season",
    ],
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
            # Flatten TaskSchedule relationship into recurrence_* columns
            if field.startswith("recurrence_") and entity_type == "task":
                schedule = getattr(entity, "schedule", None)
                suffix = field[len("recurrence_"):]
                attr_map = {
                    "frequency": "frequency",
                    "next_due": "next_due",
                    "last_generated": "last_generated",
                    "cron": "custom_cron",
                    "season": "season_spec",
                }
                val = getattr(schedule, attr_map.get(suffix, suffix), None) if schedule else None
            else:
                val = getattr(entity, field, None)
            if hasattr(val, "value"):  # Enum
                val = val.value
            if isinstance(val, list):
                val = "|".join(str(v) for v in val)
            if isinstance(val, (date, datetime)):
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


# ── Import (2026-11-05: rebuilt for the web Import page; the CLI uses it too) ──────────────
#
# A spreadsheet from a new customer, not a round-trip of our own export, is the case this is
# built for. So: friendly column names (the export's names still work), properties and people
# named the way a person would name them, any common date format, Excel's encodings, and one
# row's problem never stopping the rest. Every row runs in its own SAVEPOINT and is flushed
# there, so a refused home (plan limit), a bad value or a constraint error is that row's
# message. A preview runs the same code and the caller rolls the whole transaction back.

#: Most rows a file may hold, and its largest size. The size stays under the 1 MB limit the form
#: parser puts on a non-file field: the preview posts the CSV back in a hidden field.
MAX_IMPORT_ROWS = 2000
MAX_IMPORT_BYTES = 900_000

#: Import columns per type: (column, required, what it takes). The template and the help on the
#: Import page are both generated from this, so they cannot disagree with the parser.
IMPORT_COLUMNS: dict[str, list[tuple[str, bool, str]]] = {
    "property": [
        ("name", True, "Home name"),
        ("address", False, "Street address"),
        ("property_type", False, "primary, vacation, seasonal, investment, rental, estate, other"),
        ("climate_zone", False, "e.g. southeast, coastal, northeast"),
        ("currency", False, "e.g. USD, EUR (defaults to the estate's)"),
    ],
    "staff": [
        ("name", True, "Full name"),
        ("role", False, "housekeeper, groundskeeper, property-manager, driver, chef, security, "
                        "personal-assistant, other"),
        ("phone", False, ""),
        ("email", False, ""),
    ],
    "vendor": [
        ("company_name", True, "Company"),
        ("contact_name", False, "Person to call"),
        ("phone", False, ""),
        ("email", False, ""),
        ("service_categories", False, "Several allowed, separated by | or ; (e.g. plumbing|hvac)"),
    ],
    "task": [
        ("title", True, "What needs doing"),
        ("property", True, "Home name or slug"),
        ("priority", False, "urgent, high, medium, low"),
        ("due_date", False, "2026-11-30 or 11/30/2026"),
        ("assignee", False, "Staff name or slug (leave blank for unassigned)"),
        ("repeat", False, "once, daily, weekly, biweekly, monthly, quarterly, seasonal, annual"),
        ("category", False, "plumbing, hvac, electrical, fire-safety, security, exterior, "
                            "landscaping, interior, appliances, operations, general, …"),
        ("description", False, ""),
        ("estimated_hours", False, "Number, e.g. 1.5"),
    ],
    "issue": [
        ("title", True, "What's wrong"),
        ("property", True, "Home name or slug"),
        ("severity", False, "critical, high, medium, low"),
        ("description", False, ""),
    ],
}

#: Other headers accepted for a column: our own export's names, and common spreadsheet wording.
_ALIASES = {
    "property_id": "property", "home": "property", "assignee_id": "assignee",
    "assigned_to": "assignee", "due": "due_date", "frequency": "repeat",
    "recurrence_frequency": "repeat", "company": "company_name", "contact": "contact_name",
    "categories": "service_categories", "type": "property_type",
}

LABELS = {"property": "homes", "staff": "staff", "vendor": "vendors", "task": "tasks",
          "issue": "issues"}
LIST_PAGES = {"property": "/properties/", "staff": "/staff/", "vendor": "/vendors/",
              "task": "/tasks/", "issue": "/issues/"}


class RowProblem(ValueError):
    """A row that cannot be imported, with a message for the person who wrote it."""


@dataclass
class RowResult:
    row: int            # spreadsheet row number (the header is row 1)
    label: str          # what the row is about, e.g. the task title
    status: str         # "created" | "exists" | "error"
    message: str = ""


@dataclass
class ImportResult:
    entity_type: str
    rows: list[RowResult] = dc_field(default_factory=list)
    unknown_columns: list[str] = dc_field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r.status == status)


def decode_csv(data: bytes) -> str:
    """Bytes from an upload as text. Excel writes UTF-8 with a BOM, or Windows-1252."""
    if len(data) > MAX_IMPORT_BYTES:
        raise RowProblem(f"The file is larger than {MAX_IMPORT_BYTES // 1000} KB. Split it into "
                         "smaller files.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RowProblem("The file isn't readable text. Save it from your spreadsheet as CSV.")


def import_template(entity_type: str) -> str:
    """A CSV with just the import headers, for the Import page's "Download template"."""
    columns = IMPORT_COLUMNS.get(entity_type)
    if not columns:
        raise ValueError(f"Import not supported for: {entity_type}")
    return ",".join(c for c, _, _ in columns) + "\r\n"


def _header(name: str) -> str:
    key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    return _ALIASES.get(key, key)


def _reader(csv_text: str) -> csv.DictReader:
    """European Excel saves with semicolons; let the header line decide."""
    first = csv_text.lstrip("﻿").splitlines()[0] if csv_text.strip() else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    return csv.DictReader(io.StringIO(csv_text.lstrip("﻿")), delimiter=delimiter)


def _enum(enum_cls, raw: str | None, default, what: str):
    if not raw:
        return default
    key = raw.strip().lower().replace(" ", "-").replace("_", "-")
    for member in enum_cls:
        if member.value == key or member.name.lower().replace("_", "-") == key:
            return member
    allowed = ", ".join(m.value for m in enum_cls)
    raise RowProblem(f"“{raw}” isn't a {what}. Use one of: {allowed}.")


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y")


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise RowProblem(f"“{raw}” isn't a date. Use 2026-11-30 or 11/30/2026.")


def _lookup(session: Session, model, name_attr: str, raw: str, what: str):
    """Find one record by id, slug or name. Names are not unique, so two matches is a problem
    for this row, not a guess: tasks filed under the wrong home are worse than a skipped row."""
    from sqlalchemy import func

    from mihomes.services.slug import EntityNotFoundError

    text = raw.strip()
    try:
        return resolve_identifier(session, model, text)
    except EntityNotFoundError:
        pass
    matches = session.query(model).filter(
        func.lower(getattr(model, name_attr)) == text.lower()
    ).all()
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RowProblem(f"No {what} called “{text}”.")
    slugs = ", ".join(m.slug for m in matches)
    raise RowProblem(f"{len(matches)} {what}s are called “{text}”. Use the slug instead: {slugs}.")


def _existing(session: Session, entity_type: str, v: dict, prop=None):
    """The record this row would duplicate, if it is already there. Re-uploading a corrected
    file is the normal way to fix a few bad rows, so rows already imported are skipped."""
    from sqlalchemy import func

    model = _get_model(entity_type)
    if entity_type in ("task", "issue"):
        return session.query(model).filter(
            func.lower(model.title) == v["title"].lower(), model.property_id == prop.id
        ).first()
    name_attr = "company_name" if entity_type == "vendor" else "name"
    return session.query(model).filter(
        func.lower(getattr(model, name_attr)) == v[name_attr].lower()
    ).first()


def _import_row(session: Session, entity_type: str, v: dict) -> tuple[str, str]:
    """Create one record from a cleaned row. Returns (status, label)."""
    from mihomes.models.property import Property

    required = [c for c, req, _ in IMPORT_COLUMNS[entity_type] if req]
    missing = [c for c in required if not v.get(c)]
    label_key = {"vendor": "company_name", "task": "title", "issue": "title"}.get(entity_type, "name")
    label = v.get(label_key) or "(blank)"
    if missing:
        raise RowProblem(f"Missing {', '.join(missing)}.")

    prop = _lookup(session, Property, "name", v["property"], "home") if "property" in required else None
    if _existing(session, entity_type, v, prop) is not None:
        return "exists", label

    if entity_type == "property":
        from mihomes.models.property import PropertyType
        from mihomes.services.property import create_property

        create_property(
            session, v["name"], address=v.get("address"),
            property_type=_enum(PropertyType, v.get("property_type"), PropertyType.OTHER, "home type"),
            climate_zone=v.get("climate_zone"), currency=v.get("currency") or None,
        )
    elif entity_type == "staff":
        from mihomes.models.staff import StaffRole
        from mihomes.services.staff import create_staff

        create_staff(session, v["name"], role=_enum(StaffRole, v.get("role"), StaffRole.OTHER, "role"),
                     phone=v.get("phone"), email=v.get("email"))
    elif entity_type == "vendor":
        from mihomes.services.vendor import create_vendor

        raw = v.get("service_categories") or ""
        cats = [c.strip() for c in raw.replace(";", "|").split("|") if c.strip()] or None
        create_vendor(session, v["company_name"], contact_name=v.get("contact_name"),
                      phone=v.get("phone"), email=v.get("email"), service_categories=cats)
    elif entity_type == "task":
        from mihomes.models.staff import Staff
        from mihomes.models.task import RecurrenceFrequency, TaskCategory, TaskPriority
        from mihomes.services.task import create_task

        assignee = _lookup(session, Staff, "name", v["assignee"], "staff member") if v.get("assignee") else None
        hours = v.get("estimated_hours")
        try:
            hours = float(hours) if hours else None
        except ValueError:
            raise RowProblem(f"“{hours}” isn't a number of hours.") from None
        create_task(
            session, v["title"], str(prop.id), description=v.get("description"),
            priority=_enum(TaskPriority, v.get("priority"), TaskPriority.MEDIUM, "priority"),
            assignee_id_or_slug=str(assignee.id) if assignee else None,
            due_date=_date(v.get("due_date")),
            recurrence=_enum(RecurrenceFrequency, v.get("repeat"), RecurrenceFrequency.ONCE, "repeat"),
            category=_enum(TaskCategory, v.get("category"), None, "category"),
            estimated_hours=hours,
        )
    elif entity_type == "issue":
        from mihomes.models.issue import IssueSeverity
        from mihomes.services.issue import create_issue

        create_issue(session, v["title"], str(prop.id),
                     severity=_enum(IssueSeverity, v.get("severity"), IssueSeverity.MEDIUM, "severity"),
                     description=v.get("description"))
    else:  # pragma: no cover - callers check the type
        raise ValueError(f"Import not supported for: {entity_type}")
    return "created", label


def run_import(session: Session, entity_type: str, csv_text: str) -> ImportResult:
    """Import every row it can; report each one. The caller commits, or rolls back for a preview.

    Rolling back the whole transaction (not a savepoint) is what makes a preview leave nothing
    behind: calendar sync is queued on commit and cleared only by an outermost rollback
    (`personal_calendar/trigger.py`).
    """
    if entity_type not in IMPORT_COLUMNS:
        raise ValueError(f"Import not supported for: {entity_type}. Supported: "
                         f"{', '.join(IMPORT_COLUMNS)}")
    reader = _reader(csv_text)
    headers = {h: _header(h) for h in (reader.fieldnames or [])}
    known = {c for c, _, _ in IMPORT_COLUMNS[entity_type]} | {"id", "slug"}
    result = ImportResult(entity_type, unknown_columns=sorted(
        h for h, key in headers.items() if h and key not in known))

    for row_no, raw in enumerate(reader, 2):
        if row_no - 1 > MAX_IMPORT_ROWS:
            raise RowProblem(f"The file has more than {MAX_IMPORT_ROWS} rows. Split it into "
                             "smaller files.")
        values = {headers.get(k, k): (val or "").strip() for k, val in raw.items() if k}
        if not any(values.values()):
            continue  # a blank line at the end of a spreadsheet is not a row
        label = values.get("title") or values.get("name") or values.get("company_name") or "(blank)"
        try:
            with session.begin_nested():
                status, label = _import_row(session, entity_type, values)
                session.flush()
            result.rows.append(RowResult(row_no, label, status,
                                         "Already exists — skipped" if status == "exists" else ""))
        except Exception as exc:  # noqa: BLE001 — every row's problem is reported, none stops the rest
            result.rows.append(RowResult(row_no, label, "error", _message(exc)))
    return result


def _message(exc: Exception) -> str:
    from sqlalchemy.exc import IntegrityError

    from mihomes.services.property import EntitlementError

    if isinstance(exc, (RowProblem, ValueError, PermissionError, EntitlementError)):
        return str(exc) or exc.__class__.__name__
    if isinstance(exc, IntegrityError):
        return "Conflicts with an existing record."
    return "Couldn't import this row."


def import_csv(session: Session, entity_type: str, csv_text: str) -> dict:
    """CLI shape of `run_import`: {"created": [...], "errors": [{"row", "error"}]}."""
    result = run_import(session, entity_type, csv_text)
    return {
        "created": [r for r in result.rows if r.status == "created"],
        "errors": [{"row": r.row, "error": r.message} for r in result.rows if r.status == "error"],
    }


def _get_model(entity_type: str):
    from mihomes.models.issue import Issue
    from mihomes.models.property import Property
    from mihomes.models.staff import Staff
    from mihomes.models.task import Task
    from mihomes.models.vendor import Vendor
    return {"property": Property, "staff": Staff, "vendor": Vendor, "task": Task, "issue": Issue}[entity_type]
