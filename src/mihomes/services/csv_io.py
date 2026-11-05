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
        ("name", True, "Property name"),
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
        ("property", True, "Property name or slug"),
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
        ("property", True, "Property name or slug"),
        ("severity", False, "critical, high, medium, low"),
        ("description", False, ""),
    ],
    # 2026-11-05: the two lists an estate most often already keeps in a spreadsheet.
    "asset": [
        ("name", True, "What it is, e.g. Sub-Zero fridge"),
        ("property", True, "Property name or slug"),
        ("room", False, "Room name; a room that doesn't exist yet is created (the preview says so)"),
        ("asset_type", False, "appliance, vehicle, valuable, consumable, equipment (default equipment)"),
        ("make", False, ""),
        ("model", False, ""),
        ("serial_number", False, ""),
        ("condition", False, "excellent, good, fair, poor"),
        ("purchase_date", False, "2024-05-01 or 5/1/2024"),
        ("purchase_price", False, "Number, e.g. 1299.99"),
        ("warranty_expires", False, "Date; you'll get an alert 30 days before"),
        ("notes", False, ""),
    ],
    "inventory": [
        ("name", True, "Item, e.g. Paper towels"),
        ("property", True, "Property name or slug"),
        ("category", False, "e.g. cleaning, kitchen, bathroom"),
        ("unit", False, "e.g. rolls, boxes, bottles"),
        ("quantity", False, "How many you have now"),
        ("par_level", False, "How many you like to keep"),
        ("low_stock_threshold", False, "Alert when stock falls to this"),
        ("notes", False, ""),
    ],
}

#: Other headers accepted for a column: our own export's names, and common spreadsheet wording.
_ALIASES = {
    "property_id": "property", "home": "property", "assignee_id": "assignee",
    "assigned_to": "assignee", "due": "due_date", "frequency": "repeat",
    "recurrence_frequency": "repeat", "company": "company_name", "contact": "contact_name",
    "categories": "service_categories", "type": "property_type",
    "space": "room", "location": "room", "model_name": "model", "serial": "serial_number",
    "warranty": "warranty_expires", "warranty_expiry": "warranty_expires", "price": "purchase_price",
    "qty": "quantity", "quantity_in_stock": "quantity", "in_stock": "quantity", "par": "par_level",
    "threshold": "low_stock_threshold", "type_of_asset": "asset_type",
}

LABELS = {"property": "properties", "staff": "staff", "vendor": "vendors", "task": "tasks",
          "issue": "issues", "asset": "assets", "inventory": "inventory items"}
LIST_PAGES = {"property": "/properties/", "staff": "/staff/", "vendor": "/vendors/",
              "task": "/tasks/", "issue": "/issues/", "asset": "/assets/", "inventory": "/inventory/"}


class RowProblem(ValueError):
    """A row that cannot be imported, with a message for the person who wrote it."""


@dataclass
class RowResult:
    row: int            # spreadsheet row number (the header is row 1)
    label: str          # what the row is about, e.g. the task title
    status: str         # "created" | "updated" | "exists" (no changes) | "error"
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
    article = "an" if what[:1] in "aeiou" else "a"
    raise RowProblem(f"“{raw}” isn't {article} {what}. Use one of: {allowed}.")


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
    plural = "properties" if what == "property" else f"{what}s"
    raise RowProblem(f"{len(matches)} {plural} are called “{text}”. Use the slug instead: {slugs}.")


def _existing(session: Session, entity_type: str, v: dict, prop=None) -> list:
    """The records this row names, if they are already there: properties, staff and vendors by name,
    tasks and issues by title within their home. One match is updated from the row; more than
    one is the row's error, because updating the wrong record is worse than skipping one."""
    from sqlalchemy import func

    model = _get_model(entity_type)
    if entity_type in ("task", "issue"):
        return session.query(model).filter(
            func.lower(model.title) == v["title"].lower(), model.property_id == prop.id
        ).all()
    if entity_type in ("asset", "inventory"):  # by name within its property
        query = session.query(model).filter(
            func.lower(model.name) == v["name"].lower(), model.property_id == prop.id
        )
        if entity_type == "asset" and v.get("room"):
            # A house has a smoke detector in the kitchen *and* one in the hall: with a room
            # given, the room is part of what the row names. An unknown room means a new asset.
            from mihomes.models.space import Space

            text = v["room"].strip()
            room_ids = [r.id for r in session.query(Space).filter(
                Space.property_id == prop.id,
                (func.lower(Space.name) == text.lower()) | (Space.slug == text))]
            if not room_ids:
                return []
            query = query.filter(model.space_id.in_(room_ids))
        return query.all()
    name_attr = "company_name" if entity_type == "vendor" else "name"
    return session.query(model).filter(
        func.lower(getattr(model, name_attr)) == v[name_attr].lower()
    ).all()


def _import_row(session: Session, entity_type: str, v: dict) -> tuple[str, str]:
    """Create one record from a cleaned row. Returns (status, label)."""
    from mihomes.models.property import Property

    required = [c for c, req, _ in IMPORT_COLUMNS[entity_type] if req]
    missing = [c for c in required if not v.get(c)]
    label_key = {"vendor": "company_name", "task": "title", "issue": "title"}.get(entity_type, "name")
    label = v.get(label_key) or "(blank)"
    if missing:
        raise RowProblem(f"Missing {', '.join(missing)}.")

    prop = _lookup(session, Property, "name", v["property"], "property") if "property" in required else None
    matches = _existing(session, entity_type, v, prop)
    if len(matches) > 1:
        raise RowProblem(f"{len(matches)} existing {LABELS[entity_type]} match “{label}”, so "
                         "it isn't clear which to update. Rename one in MiHomes first.")
    if matches:
        return _update_row(session, entity_type, matches[0], v, label)

    if entity_type == "property":
        from mihomes.models.property import PropertyType
        from mihomes.services.property import create_property

        create_property(
            session, v["name"], address=v.get("address"),
            property_type=_enum(PropertyType, v.get("property_type"), PropertyType.OTHER, "property type"),
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
    elif entity_type == "asset":
        from mihomes.models.asset import AssetType
        from mihomes.services.asset import create_asset

        room, note = _room(session, prop, v.get("room"))
        fields = _wanted(session, entity_type, v, prop=prop, room=room)
        fields.pop("space_id", None)
        create_asset(
            session, v["name"], fields.pop("asset_type", AssetType.EQUIPMENT), str(prop.id),
            space_id_or_slug=str(room.id) if room else None, **fields,
        )
        return "created", label, note
    elif entity_type == "inventory":
        from mihomes.services.consumable import create_consumable

        fields = _wanted(session, entity_type, v)
        create_consumable(
            session, v["name"], str(prop.id), unit=fields.get("unit"),
            category=fields.get("category"), par_level=fields.get("par_level"),
            quantity_in_stock=fields.get("quantity_in_stock"),
            low_stock_threshold=fields.get("low_stock_threshold"), notes=fields.get("notes"),
        )
    else:  # pragma: no cover - callers check the type
        raise ValueError(f"Import not supported for: {entity_type}")
    return "created", label, ""


def _number(raw: str | None, what: str) -> float | None:
    if not raw:
        return None
    text = raw.replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip()
    try:
        return float(text)
    except ValueError:
        raise RowProblem(f"“{raw}” isn't a number for {what}.") from None


def _room(session: Session, prop, raw: str | None):
    """The room called `raw` in `prop`, created when there is none (and the row says so, so the
    preview shows a typo before it becomes a room). Two rooms with the name is the row's error."""
    from sqlalchemy import func

    from mihomes.models.space import Space
    from mihomes.services.space import create_space

    if not raw:
        return None, ""
    text = raw.strip()
    rooms = session.query(Space).filter(
        Space.property_id == prop.id,
        (func.lower(Space.name) == text.lower()) | (Space.slug == text),
    ).all()
    if len(rooms) > 1:
        raise RowProblem(f"{len(rooms)} rooms in {prop.name} are called “{text}”. Use the slug instead: "
                         f"{', '.join(r.slug for r in rooms)}.")
    if rooms:
        return rooms[0], ""
    return create_space(session, text, str(prop.id)), f"New room “{text}”"


def _wanted(session: Session, entity_type: str, v: dict, *, prop=None, room=None) -> dict:
    """Attribute → new value, for every **filled** cell an existing record can take. A blank
    cell means "leave it as it is", so a sheet with only a few columns cannot wipe the rest."""
    from mihomes.models.issue import IssueSeverity
    from mihomes.models.property import PropertyType
    from mihomes.models.staff import Staff, StaffRole
    from mihomes.models.task import RecurrenceFrequency, TaskCategory, TaskPriority

    def given(col):
        return v.get(col) or None

    out: dict = {}
    if entity_type == "property":
        out = {"address": given("address"), "climate_zone": given("climate_zone"),
               "currency": (given("currency") or "").upper() or None}
        if given("property_type"):
            out["property_type"] = _enum(PropertyType, v["property_type"], None, "property type")
    elif entity_type == "staff":
        out = {"phone": given("phone"), "email": given("email")}
        if given("role"):
            out["role"] = _enum(StaffRole, v["role"], None, "role")
    elif entity_type == "vendor":
        out = {"contact_name": given("contact_name"), "phone": given("phone"),
               "email": given("email")}
        if given("service_categories"):
            out["service_categories"] = [
                c.strip() for c in v["service_categories"].replace(";", "|").split("|") if c.strip()
            ]
    elif entity_type == "task":
        out = {"description": given("description")}
        if given("priority"):
            out["priority"] = _enum(TaskPriority, v["priority"], None, "priority")
        if given("due_date"):
            out["due_date"] = _date(v["due_date"])
        if given("category"):
            out["category"] = _enum(TaskCategory, v["category"], None, "category")
        if given("assignee"):
            out["assignee_id"] = _lookup(session, Staff, "name", v["assignee"], "staff member").id
        if given("estimated_hours"):
            try:
                out["estimated_hours"] = float(v["estimated_hours"])
            except ValueError:
                raise RowProblem(f"“{v['estimated_hours']}” isn't a number of hours.") from None
        if given("repeat"):
            out["repeat"] = _enum(RecurrenceFrequency, v["repeat"], None, "repeat")
    elif entity_type == "issue":
        out = {"description": given("description")}
        if given("severity"):
            out["severity"] = _enum(IssueSeverity, v["severity"], None, "severity")
    elif entity_type == "asset":
        from mihomes.models.asset import AssetCondition, AssetType

        out = {"make": given("make"), "model_name": given("model"),
               "serial_number": given("serial_number"), "notes": given("notes"),
               "purchase_date": _date(given("purchase_date")),
               "purchase_price": _number(given("purchase_price"), "purchase_price"),
               "warranty_expires": _date(given("warranty_expires"))}
        if given("asset_type"):
            out["asset_type"] = _enum(AssetType, v["asset_type"], None, "asset type")
        if given("condition"):
            out["condition"] = _enum(AssetCondition, v["condition"], None, "condition")
        if room is not None:
            out["space_id"] = room.id
    elif entity_type == "inventory":
        out = {"category": given("category"), "unit": given("unit"), "notes": given("notes"),
               "quantity_in_stock": _number(given("quantity"), "quantity"),
               "par_level": _number(given("par_level"), "par_level"),
               "low_stock_threshold": _number(given("low_stock_threshold"), "low_stock_threshold")}
    return {k: val for k, val in out.items() if val is not None}


def _shown(session: Session, attr: str, value) -> str:
    """A value as the person wrote it: a staff member's name, not their id."""
    if value is None or value == "" or value == []:
        return "—"
    if attr == "assignee_id":
        from mihomes.models.staff import Staff

        person = session.get(Staff, value)
        return person.name if person else "—"
    if attr == "space_id":
        from mihomes.models.space import Space

        room = session.get(Space, value)
        return room.name if room else "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _value(value)


#: How a changed attribute is named in the preview: the column the person wrote, not the field.
_CHANGE_NAMES = {"assignee_id": "assignee", "space_id": "room", "model_name": "model",
                 "quantity_in_stock": "quantity"}


def _update_row(session: Session, entity_type: str, record, v: dict, label: str):
    """Bring an existing record in line with the row; returns (status, label, what changed).

    Through each type's own update function, so validation, frozen-home refusal and the audit
    log apply exactly as they do to an edit in the app.
    """
    from mihomes.models.task import RecurrenceFrequency
    from mihomes.services import issue as issue_svc
    from mihomes.services import property as prop_svc
    from mihomes.services import staff as staff_svc
    from mihomes.services import task as task_svc
    from mihomes.services import vendor as vendor_svc

    room_note = ""
    room = None
    if entity_type == "asset":
        room, room_note = _room(session, record.property, v.get("room"))
    wanted = _wanted(session, entity_type, v, room=room)
    repeat = wanted.pop("repeat", None)
    current_repeat = None
    if entity_type == "task":
        current_repeat = record.schedule.frequency if record.schedule else RecurrenceFrequency.ONCE
    repeat_changes = repeat is not None and repeat != current_repeat

    changes = {attr: new for attr, new in wanted.items() if getattr(record, attr) != new}
    notes = [f"{_CHANGE_NAMES.get(attr, attr)}: {_shown(session, attr, getattr(record, attr))} → "
             f"{_shown(session, attr, new)}" for attr, new in changes.items()]
    if room_note:
        notes.append(room_note)
    if repeat_changes:
        notes.append(f"repeat: {current_repeat.value} → {repeat.value}")
    if not notes:
        return "exists", label, "No changes"

    if entity_type == "inventory":
        _update_inventory(session, record, changes)
        return "updated", label, "; ".join(notes)

    from mihomes.services import asset as asset_svc

    update = {"property": prop_svc.update_property, "staff": staff_svc.update_staff,
              "vendor": vendor_svc.update_vendor, "task": task_svc.update_task,
              "issue": issue_svc.update_issue, "asset": asset_svc.update_asset}[entity_type]
    if changes:
        update(session, str(record.id), **changes)
    if repeat_changes:
        task_svc.set_task_frequency(session, str(record.id), repeat)
    return "updated", label, "; ".join(notes)


def _update_inventory(session: Session, item, changes: dict) -> None:
    """Inventory has three update paths, and the stock status must be recomputed by them:
    details (category, unit), stock (quantity), and settings (par level, low-stock threshold).
    Each is passed the current value for whatever the row left blank."""
    from mihomes.services import consumable as consumable_svc

    if "category" in changes or "unit" in changes:
        consumable_svc.update_consumable(
            session, item.slug, name=item.name,
            category=changes.get("category", item.category), unit=changes.get("unit", item.unit))
    if "quantity_in_stock" in changes:
        # By name: `update_stock` finds the item by name within the property and *creates* one
        # when nothing matches, so passing the slug silently made a second item called it.
        consumable_svc.update_stock(session, item.name, str(item.property_id),
                                    quantity_in_stock=changes["quantity_in_stock"])
    if "par_level" in changes or "low_stock_threshold" in changes:
        consumable_svc.update_consumable_settings(
            session, item.slug,
            par_level=changes.get("par_level", item.par_level),
            low_stock_threshold=changes.get("low_stock_threshold", item.low_stock_threshold))
    if "notes" in changes:
        item.notes = changes["notes"]
    session.flush()


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
    known = {c for c, _, _ in IMPORT_COLUMNS[entity_type]} | {"id", "slug"} | EXPORT_ONLY_COLUMNS
    result = ImportResult(entity_type, unknown_columns=sorted(
        h for h, key in headers.items() if h and key not in known))

    for row_no, raw in enumerate(reader, 2):
        if row_no - 1 > MAX_IMPORT_ROWS:
            raise RowProblem(f"The file has more than {MAX_IMPORT_ROWS} rows. Split it into "
                             "smaller files.")
        values = {headers.get(k, k): _unguard((val or "").strip()) for k, val in raw.items() if k}
        if not any(values.values()):
            continue  # a blank line at the end of a spreadsheet is not a row
        label = values.get("title") or values.get("name") or values.get("company_name") or "(blank)"
        try:
            with session.begin_nested():
                status, label, message = _import_row(session, entity_type, values)
                session.flush()
            result.rows.append(RowResult(row_no, label, status, message))
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
    """CLI shape of `run_import`: {"created": [...], "updated": [...], "errors": [{"row", "error"}]}."""
    result = run_import(session, entity_type, csv_text)
    return {
        "created": [r for r in result.rows if r.status == "created"],
        "updated": [r for r in result.rows if r.status == "updated"],
        "errors": [{"row": r.row, "error": r.message} for r in result.rows if r.status == "error"],
    }


def _get_model(entity_type: str):
    from mihomes.models.asset import Asset
    from mihomes.models.consumable import Consumable
    from mihomes.models.issue import Issue
    from mihomes.models.property import Property
    from mihomes.models.staff import Staff
    from mihomes.models.task import Task
    from mihomes.models.vendor import Vendor
    return {"property": Property, "staff": Staff, "vendor": Vendor, "task": Task, "issue": Issue,
            "asset": Asset, "inventory": Consumable}[entity_type]


# ── Spreadsheet export (2026-11-05): the import's own columns, so a file goes out and back ───
#
# `export_csv` above writes database ids (`property_id` as a UUID) for the CLI; a person opening
# that in Excel cannot tell one home from another. This writes the import template's columns
# with homes and people by **name**, falling back to the slug only where a name is shared, the
# same rule the import resolves by. So export → edit → re-import works, and rows that already
# exist are skipped on the way back in.

#: Columns the export adds after the import ones, for reading only. The import ignores them
#: without listing them as unknown, so a round-tripped file previews cleanly.
EXPORT_ONLY_COLUMNS = {"status"}
_EXPORT_EXTRA = {"task": ["status"], "issue": ["status"]}

#: A cell starting with one of these is a formula to Excel and Sheets. Text that arrived from
#: outside (a Telegram message turned into an issue) must not run as one, so it gets a leading
#: apostrophe — the spreadsheet convention for "this is text", which spreadsheets hide.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _guard(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _unguard(text: str) -> str:
    """Undo `_guard` on the way back in, when the file was never opened in a spreadsheet."""
    if len(text) > 1 and text[0] == "'" and text[1:].startswith(_FORMULA_START):
        return text[1:]
    return text


def _labeller(session: Session, model, name_attr: str):
    """`label(record)`: its name, or its slug when another record shares the name."""
    from collections import Counter

    counts = Counter((n or "").lower() for (n,) in session.query(getattr(model, name_attr)))

    def label(record) -> str:
        if record is None:
            return ""
        name = getattr(record, name_attr) or ""
        return record.slug if counts[name.lower()] > 1 else name
    return label


def _value(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "value"):  # Enum
        return v.value
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, list):
        return "|".join(str(x) for x in v)
    return str(v)


def export_for_spreadsheet(session: Session, entity_type: str) -> str:
    """Every record of `entity_type` the viewer can see, as a CSV the import accepts back."""
    from mihomes.models.property import Property
    from mihomes.models.staff import Staff

    if entity_type not in IMPORT_COLUMNS:
        raise ValueError(f"Export not supported for: {entity_type}")
    columns = [c for c, _, _ in IMPORT_COLUMNS[entity_type]] + _EXPORT_EXTRA.get(entity_type, [])
    home = _labeller(session, Property, "name")
    person = _labeller(session, Staff, "name")
    model = _get_model(entity_type)
    query = session.query(model)
    if entity_type == "task":
        query = query.filter(model.archived_at.is_(None))
    if entity_type == "asset":
        query = query.filter(model.active.is_(True))
    order = {"vendor": "company_name", "task": "title", "issue": "title"}.get(entity_type, "name")

    def row(r) -> dict:
        if entity_type == "task":
            freq = r.schedule.frequency if r.schedule else None
            return {"title": r.title, "property": home(r.property), "priority": r.priority,
                    "due_date": r.due_date, "assignee": person(r.assignee),
                    "repeat": freq or "once", "category": r.category,
                    "description": r.description, "estimated_hours": r.estimated_hours,
                    "status": r.status}
        if entity_type == "issue":
            return {"title": r.title, "property": home(r.property), "severity": r.severity,
                    "description": r.description, "status": r.status}
        if entity_type == "asset":
            return {"name": r.name, "property": home(r.property),
                    "room": r.space.name if r.space else "", "asset_type": r.asset_type,
                    "make": r.make, "model": r.model_name, "serial_number": r.serial_number,
                    "condition": r.condition, "purchase_date": r.purchase_date,
                    "purchase_price": r.purchase_price, "warranty_expires": r.warranty_expires,
                    "notes": r.notes}
        if entity_type == "inventory":
            return {"name": r.name, "property": home(r.property), "category": r.category,
                    "unit": r.unit, "quantity": r.quantity_in_stock, "par_level": r.par_level,
                    "low_stock_threshold": r.low_stock_threshold, "notes": r.notes}
        return {c: getattr(r, c, None) for c in columns}

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    for record in query.order_by(getattr(model, order)):
        values = row(record)
        writer.writerow({c: _guard(_value(values.get(c))) for c in columns})
    return out.getvalue()
