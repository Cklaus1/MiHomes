"""AI tool definitions and executors — gives the AI advisor live DB access."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

# ── Tool schemas (Anthropic tool_use format) ──────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "query_library",
        "description": (
            "Query the estate book/library collection. Use to get book counts, list books by genre or property, "
            "search by title or author, or check book conditions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "genre": {"type": "string", "description": "Filter by genre (partial match, optional)"},
                "author": {"type": "string", "description": "Filter by author (partial match, optional)"},
                "title": {"type": "string", "description": "Search by title (partial match, optional)"},
                "condition": {"type": "string", "description": "Filter by condition: excellent, good, fair, poor, damaged"},
                "count_only": {"type": "boolean", "description": "Return only the total count"},
                "limit": {"type": "integer", "description": "Max records to return (default 20, max 100)"},
            },
        },
    },
    {
        "name": "query_assets",
        "description": (
            "Query physical assets (equipment, appliances, valuables). Use for asset counts, "
            "values, warranty info, or to find specific items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "asset_type": {"type": "string", "description": "Filter by type: equipment, appliance, valuable, furniture, vehicle, other"},
                "search": {"type": "string", "description": "Search asset names (partial match)"},
                "count_only": {"type": "boolean", "description": "Return only the total count"},
                "limit": {"type": "integer", "description": "Max records to return (default 20)"},
            },
        },
    },
    {
        "name": "query_tasks",
        "description": (
            "Query estate tasks. Use to find overdue tasks, upcoming tasks, tasks by property or assignee, "
            "or task completion status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "status": {"type": "string", "description": "Filter by status: pending, in-progress, completed, cancelled"},
                "priority": {"type": "string", "description": "Filter by priority: low, medium, high, urgent"},
                "overdue_only": {"type": "boolean", "description": "Return only overdue tasks"},
                "upcoming_days": {"type": "integer", "description": "Return tasks due within N days"},
                "assignee": {"type": "string", "description": "Filter by assignee name (partial match)"},
                "search": {"type": "string", "description": "Search task titles (partial match)"},
                "limit": {"type": "integer", "description": "Max records to return (default 20)"},
            },
        },
    },
    {
        "name": "query_issues",
        "description": (
            "Query property issues and maintenance problems. Use to find open issues, critical problems, "
            "or issues by severity/property/location."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "severity": {"type": "string", "description": "Filter by severity: low, medium, high, critical"},
                "status": {"type": "string", "description": "Filter by status: reported, assessed, scheduled, in-progress, resolved, verified"},
                "open_only": {"type": "boolean", "description": "Return only unresolved issues (default true)"},
                "search": {"type": "string", "description": "Search issue titles (partial match)"},
                "limit": {"type": "integer", "description": "Max records to return (default 20)"},
            },
        },
    },
    {
        "name": "query_work_orders",
        "description": (
            "Query work orders for contractors and vendors. Use to find active work, "
            "costs, vendor assignments, or completion status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "status": {"type": "string", "description": "Filter by status: draft, estimated, approved, assigned, in_progress, completed, verified, cancelled"},
                "vendor": {"type": "string", "description": "Filter by vendor name (partial match)"},
                "search": {"type": "string", "description": "Search work order titles (partial match)"},
                "limit": {"type": "integer", "description": "Max records to return (default 20)"},
            },
        },
    },
    {
        "name": "query_budget",
        "description": (
            "Query budget and financial transactions. Use to get spending totals, "
            "YTD spend by category, budget vs actual, or transaction history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "category": {"type": "string", "description": "Filter transactions by category (optional)"},
                "year": {"type": "integer", "description": "Year for YTD analysis (default current year)"},
                "summary_only": {"type": "boolean", "description": "Return only category totals, not individual transactions"},
            },
        },
    },
    {
        "name": "query_vendors",
        "description": (
            "Query vendor/contractor directory. Use to find vendors by service type, "
            "get contact info, or check vendor ratings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_category": {"type": "string", "description": "Filter by service category (partial match, e.g. 'plumbing', 'HVAC')"},
                "search": {"type": "string", "description": "Search vendor names (partial match)"},
                "limit": {"type": "integer", "description": "Max records to return (default 30)"},
            },
        },
    },
    {
        "name": "query_staff",
        "description": "Query estate staff. Use to find staff by role, property assignment, or get contact info.",
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property assignment (optional)"},
                "role": {"type": "string", "description": "Filter by role (partial match, e.g. 'housekeeper', 'manager')"},
                "search": {"type": "string", "description": "Search staff names (partial match)"},
            },
        },
    },
    {
        "name": "query_contracts",
        "description": (
            "Query service contracts. Use to find active contracts, upcoming renewals, "
            "costs, or contracts by vendor/property."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "expiring_within_days": {"type": "integer", "description": "Return contracts expiring within N days"},
                "vendor": {"type": "string", "description": "Filter by vendor name (partial match)"},
            },
        },
    },
    {
        "name": "query_insurance",
        "description": "Query insurance policies. Use to get coverage details, renewal dates, carriers, or policy types.",
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "insurance_type": {"type": "string", "description": "Filter by type (e.g. homeowners, liability, umbrella)"},
            },
        },
    },
    {
        "name": "query_consumables",
        "description": (
            "Query household consumables and supplies (cleaning products, pantry, etc.). "
            "Use to check stock levels, find low-stock items, or get inventory by category."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "category": {"type": "string", "description": "Filter by category (partial match)"},
                "low_stock_only": {"type": "boolean", "description": "Return only items below par level"},
                "search": {"type": "string", "description": "Search item names (partial match)"},
                "limit": {"type": "integer", "description": "Max records to return (default 30)"},
            },
        },
    },
    {
        "name": "query_inventory",
        "description": (
            "Query room-by-room inventory items (furniture, art, decor, electronics). "
            "Use to find items by category, room/space, value, or condition."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "space": {"type": "string", "description": "Filter by room/space name (partial match)"},
                "category": {"type": "string", "description": "Filter by category (partial match)"},
                "high_value_only": {"type": "boolean", "description": "Return only high-value items"},
                "search": {"type": "string", "description": "Search item names (partial match)"},
                "count_only": {"type": "boolean", "description": "Return only total count and value"},
                "limit": {"type": "integer", "description": "Max records to return (default 20)"},
            },
        },
    },
    {
        "name": "query_events",
        "description": "Query estate events and gatherings. Use to find upcoming events, guest counts, budgets, or past events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Filter by property slug (optional)"},
                "upcoming_only": {"type": "boolean", "description": "Return only future events"},
                "status": {"type": "string", "description": "Filter by status: planning, confirmed, completed, cancelled"},
            },
        },
    },
    {
        "name": "query_property",
        "description": (
            "Get detailed property information including spaces/rooms, year built, type, status, "
            "climate zone, and occupancy. Use to understand the estate layout."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_slug": {"type": "string", "description": "Specific property to look up (optional — returns all if omitted)"},
                "include_spaces": {"type": "boolean", "description": "Include room/space list (default true)"},
            },
        },
    },
    {
        "name": "query_alerts",
        "description": "Query active system alerts and warnings. Use to check what urgent items need attention. Returns only unresolved, non-snoozed alerts by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "description": "Filter by severity: low, medium, high, critical"},
                "status": {"type": "string", "description": "Filter by status: generated, seen, acknowledged, resolved (default: exclude resolved)"},
                "include_snoozed": {"type": "boolean", "description": "Include snoozed alerts (default false)"},
            },
        },
    },
]

# ── Tool label map (for status updates shown to user) ─────────────────────────

_TOOL_LABELS = {
    "query_library": "library records",
    "query_assets": "asset inventory",
    "query_tasks": "task list",
    "query_issues": "open issues",
    "query_work_orders": "work orders",
    "query_budget": "financial data",
    "query_vendors": "vendor directory",
    "query_staff": "staff records",
    "query_contracts": "contracts",
    "query_insurance": "insurance policies",
    "query_consumables": "supply inventory",
    "query_inventory": "room inventory",
    "query_events": "events calendar",
    "query_property": "property details",
    "query_alerts": "active alerts",
}


def tool_label(name: str) -> str:
    return _TOOL_LABELS.get(name, name.replace("_", " "))


# ── Executor ──────────────────────────────────────────────────────────────────

def _parse_enum(enum_cls, value: str, field: str):
    """Resolve a status/enum filter value or raise with the valid options.

    The old code wrapped enum construction in ``except ValueError: pass``, so an
    unrecognized status silently dropped the filter and returned *all* rows
    presented as filtered (spec D3/H10). Raising here lets ``execute_tool``
    surface the valid values back to the model instead of lying.
    """
    try:
        return enum_cls(value)
    except ValueError:
        valid = ", ".join(m.value for m in enum_cls)
        raise ValueError(
            f"Invalid {field} '{value}'. Valid values: {valid}."
        ) from None


def execute_tool(session: Session, name: str, inputs: dict[str, Any]) -> str:
    try:
        fn = _EXECUTORS.get(name)
        if not fn:
            return f"Unknown tool: {name}"
        return fn(session, inputs)
    except Exception as e:
        return f"Tool error ({name}): {e}"


# ── Individual executors ──────────────────────────────────────────────────────

def _query_library(session: Session, inp: dict) -> str:
    from sqlalchemy import func

    from mihomes.models.book import Book

    q = session.query(Book).filter(Book.active.is_(True))

    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(Book.property_id == prop.id)
    if inp.get("genre"):
        q = q.filter(Book.genre.ilike(f"%{inp['genre']}%"))
    if inp.get("author"):
        q = q.filter(Book.author.ilike(f"%{inp['author']}%"))
    if inp.get("title"):
        q = q.filter(Book.title.ilike(f"%{inp['title']}%"))
    if inp.get("condition"):
        q = q.filter(Book.condition == inp["condition"])

    total = q.count()

    if inp.get("count_only"):
        # Also break down by property
        from mihomes.models.property import Property as Prop
        by_prop = (
            session.query(Prop.name, func.count(Book.id))
            .join(Book, Book.property_id == Prop.id)
            .filter(Book.active.is_(True))
            .group_by(Prop.id)
            .all()
        )
        lines = [f"Total books in library: {total}"]
        for name, cnt in by_prop:
            lines.append(f"  - {name}: {cnt} books")
        # Genre breakdown
        genres = (
            session.query(Book.genre, func.count(Book.id))
            .filter(Book.active.is_(True), Book.genre.isnot(None))
            .group_by(Book.genre).order_by(func.count(Book.id).desc()).limit(10).all()
        )
        if genres:
            lines.append("Top genres: " + ", ".join(f"{g} ({n})" for g, n in genres))
        return "\n".join(lines)

    limit = min(int(inp.get("limit", 20)), 100)
    books = q.limit(limit).all()
    if not books:
        return "No books found matching the criteria."

    lines = [f"Found {total} book(s) (showing {len(books)}):"]
    for b in books:
        author = f" by {b.author}" if b.author else ""
        genre = f" [{b.genre}]" if b.genre else ""
        prop_name = b.property.name if b.property else "unknown"
        lines.append(f"- {b.title}{author}{genre} @ {prop_name} ({b.condition.value})")
    return "\n".join(lines)


def _query_assets(session: Session, inp: dict) -> str:
    from sqlalchemy import func

    from mihomes.models.asset import Asset, AssetType

    q = session.query(Asset).filter(Asset.active.is_(True))

    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(Asset.property_id == prop.id)
    if inp.get("asset_type"):
        try:
            q = q.filter(Asset.asset_type == AssetType(inp["asset_type"]))
        except ValueError:
            pass
    if inp.get("search"):
        q = q.filter(Asset.name.ilike(f"%{inp['search']}%"))

    total = q.count()
    if inp.get("count_only"):
        by_type = (
            session.query(Asset.asset_type, func.count(Asset.id))
            .filter(Asset.active.is_(True))
            .group_by(Asset.asset_type).all()
        )
        lines = [f"Total assets: {total}"]
        for t, n in by_type:
            lines.append(f"  - {t.value}: {n}")
        total_val = session.query(func.sum(Asset.purchase_price)).filter(Asset.active.is_(True)).scalar()
        if total_val:
            lines.append(f"Total declared value: ${total_val:,.0f}")
        return "\n".join(lines)

    limit = int(inp.get("limit", 20))
    assets = q.limit(limit).all()
    if not assets:
        return "No assets found matching the criteria."

    lines = [f"Found {total} asset(s) (showing {len(assets)}):"]
    for a in assets:
        val = f", value ${a.purchase_price:,.0f}" if a.purchase_price else ""
        warranty = f", warranty expires {a.warranty_expires}" if a.warranty_expires else ""
        prop_name = a.property.name if a.property else "unknown"
        lines.append(f"- {a.name} ({a.asset_type.value}) @ {prop_name}{val}{warranty}")
    return "\n".join(lines)


def _query_tasks(session: Session, inp: dict) -> str:
    from datetime import date, timedelta

    from mihomes.models.task import Task, TaskPriority, TaskStatus

    q = session.query(Task)

    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(Task.property_id == prop.id)
    if inp.get("status"):
        q = q.filter(Task.status == _parse_enum(TaskStatus, inp["status"], "status"))
    if inp.get("priority"):
        try:
            q = q.filter(Task.priority == TaskPriority(inp["priority"]))
        except ValueError:
            pass
    _terminal = [TaskStatus.COMPLETED, TaskStatus.CANCELLED]
    if inp.get("overdue_only"):
        q = q.filter(
            Task.status.notin_(_terminal),
            Task.due_date < date.today(),
        )
    elif inp.get("upcoming_days"):
        days = int(inp["upcoming_days"])
        q = q.filter(
            Task.status.notin_(_terminal),
            Task.due_date >= date.today(),
            Task.due_date <= date.today() + timedelta(days=days),
        )
    if inp.get("assignee"):
        from mihomes.models.staff import Staff
        q = q.join(Staff, Task.assignee_id == Staff.id, isouter=True)
        q = q.filter(Staff.name.ilike(f"%{inp['assignee']}%"))
    if inp.get("search"):
        q = q.filter(Task.title.ilike(f"%{inp['search']}%"))

    limit = int(inp.get("limit", 20))
    tasks = q.order_by(Task.due_date).limit(limit).all()
    total = q.count()

    if not tasks:
        return "No tasks found matching the criteria."

    lines = [f"Found {total} task(s) (showing {len(tasks)}):"]
    for t in tasks:
        assignee = t.assignee.name if t.assignee else "unassigned"
        prop_name = t.property.name if t.property else "unknown"
        due = f", due {t.due_date}" if t.due_date else ""
        lines.append(f"- [{t.priority.value}] {t.title} @ {prop_name} — {t.status.value}{due}, {assignee}")
    return "\n".join(lines)


def _query_issues(session: Session, inp: dict) -> str:
    from mihomes.models.issue import Issue, IssueSeverity, IssueStatus

    q = session.query(Issue)

    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(Issue.property_id == prop.id)
    if inp.get("severity"):
        q = q.filter(Issue.severity == _parse_enum(IssueSeverity, inp["severity"], "severity"))
    if inp.get("status"):
        q = q.filter(Issue.status == _parse_enum(IssueStatus, inp["status"], "status"))
    elif inp.get("open_only", True):
        q = q.filter(Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]))
    if inp.get("search"):
        q = q.filter(Issue.title.ilike(f"%{inp['search']}%"))

    limit = int(inp.get("limit", 20))
    issues = q.limit(limit).all()
    total = q.count()

    if not issues:
        return "No issues found matching the criteria."

    lines = [f"Found {total} issue(s) (showing {len(issues)}):"]
    for i in issues:
        space = f" ({i.space.name})" if getattr(i, "space", None) else ""
        prop_name = i.property.name if i.property else "unknown"
        desc = f"\n  {i.description[:150]}" if i.description else ""
        lines.append(f"- [{i.severity.value}] {i.title} @ {prop_name}{space} — {i.status.value}{desc}")
    return "\n".join(lines)


def _query_work_orders(session: Session, inp: dict) -> str:
    from mihomes.models.work_order import WorkOrder, WorkOrderStatus

    q = session.query(WorkOrder)

    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(WorkOrder.property_id == prop.id)
    if inp.get("status"):
        q = q.filter(WorkOrder.status == _parse_enum(WorkOrderStatus, inp["status"], "status"))
    if inp.get("vendor"):
        from mihomes.models.vendor import Vendor
        q = q.join(Vendor, WorkOrder.vendor_id == Vendor.id, isouter=True)
        q = q.filter(Vendor.company_name.ilike(f"%{inp['vendor']}%"))
    if inp.get("search"):
        q = q.filter(WorkOrder.title.ilike(f"%{inp['search']}%"))

    limit = int(inp.get("limit", 20))
    wos = q.order_by(WorkOrder.created_at.desc()).limit(limit).all()
    total = q.count()

    if not wos:
        return "No work orders found matching the criteria."

    lines = [f"Found {total} work order(s) (showing {len(wos)}):"]
    for wo in wos:
        vendor = (wo.vendor.company_name if wo.vendor else wo.vendor_name) or "unassigned"
        prop_name = wo.property.name if wo.property else "unknown"
        est = f", est. ${wo.estimated_cost:,.0f}" if wo.estimated_cost else ""
        actual = f", actual ${wo.actual_cost:,.0f}" if wo.actual_cost else ""
        lines.append(f"- [{wo.status.value}] {wo.title} @ {prop_name} — {vendor}{est}{actual}")
    return "\n".join(lines)


def _query_budget(session: Session, inp: dict) -> str:
    from datetime import date

    from sqlalchemy import func

    from mihomes.models.budget import Transaction

    year = int(inp.get("year", date.today().year))
    start = date(year, 1, 1)
    end = date(year, 12, 31)

    # Collect every filter once so the summary aggregate honours the same
    # property/category scope as the row listing (spec M32 — previously the
    # aggregate re-queried with only the date filter, so "how much did Beach
    # House spend?" silently reported the whole estate on the default path).
    filters = [Transaction.date >= start, Transaction.date <= end]
    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        filters.append(Transaction.property_id == prop.id)
    if inp.get("category"):
        filters.append(Transaction.category.ilike(f"%{inp['category']}%"))

    q = session.query(Transaction).filter(*filters)

    if inp.get("summary_only", True):
        by_cat = (
            session.query(
                Transaction.category,
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("cnt"),
            )
            .filter(*filters)
            .group_by(Transaction.category)
            .order_by(func.sum(Transaction.amount).desc())
            .all()
        )
        if not by_cat:
            return f"No transactions recorded for {year}."
        total = sum(r.total for r in by_cat)
        lines = [f"Financial summary for {year} — Total spend: ${total:,.0f}"]
        for r in by_cat:
            lines.append(f"  - {r.category}: ${r.total:,.0f} ({r.cnt} transaction{'s' if r.cnt != 1 else ''})")
        return "\n".join(lines)

    txns = q.order_by(Transaction.date.desc()).limit(30).all()
    if not txns:
        return f"No transactions found for {year}."
    lines = [f"Transactions for {year} (showing up to 30):"]
    for t in txns:
        prop_name = t.property.name if t.property else "general"
        lines.append(f"- {t.date} ${t.amount:,.0f} [{t.category}] @ {prop_name}: {t.description or ''}")
    return "\n".join(lines)


def _query_vendors(session: Session, inp: dict) -> str:
    from mihomes.models.vendor import Vendor

    q = session.query(Vendor)
    if inp.get("service_category"):
        q = q.filter(Vendor.service_categories.ilike(f"%{inp['service_category']}%"))
    if inp.get("search"):
        q = q.filter(Vendor.company_name.ilike(f"%{inp['search']}%"))

    limit = int(inp.get("limit", 30))
    vendors = q.limit(limit).all()
    total = q.count()

    if not vendors:
        return "No vendors found matching the criteria."

    lines = [f"Found {total} vendor(s) (showing {len(vendors)}):"]
    for v in vendors:
        cats = ", ".join(v.service_categories) if v.service_categories else "general"
        contacts = []
        if v.contacts:
            for c in (v.contacts if isinstance(v.contacts, list) else []):
                name = c.get("name", "")
                phone = c.get("phone", "")
                email = c.get("email", "")
                if name or phone:
                    contacts.append(f"{name} {phone} {email}".strip())
        contact_str = f" | {contacts[0]}" if contacts else ""
        lines.append(f"- {v.company_name} ({cats}){contact_str}")
    return "\n".join(lines)


def _query_staff(session: Session, inp: dict) -> str:
    from mihomes.models.staff import Staff, is_staff_role

    q = session.query(Staff)
    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(Staff.properties.any(Property.id == prop.id))
    if inp.get("role"):
        q = q.filter(Staff.role.ilike(f"%{inp['role']}%"))
    if inp.get("search"):
        q = q.filter(Staff.name.ilike(f"%{inp['search']}%"))

    # The staff table also holds residents/owners/associates (the Directory);
    # this tool answers about actual employees only — keep it consistent with
    # the staff context in context.py.
    staff = [s for s in q.all() if is_staff_role(s.role)]
    if not staff:
        return "No staff found matching the criteria."

    lines = [f"Found {len(staff)} staff member(s):"]
    for s in staff:
        props = ", ".join(p.name for p in s.properties) or "unassigned"
        phone = getattr(s, "phone", "") or ""
        email = getattr(s, "email", "") or ""
        contact = f" | {phone}" if phone else (f" | {email}" if email else "")
        lines.append(f"- {s.name} ({s.role.value}) — {props}{contact}")
    return "\n".join(lines)


def _query_contracts(session: Session, inp: dict) -> str:
    from datetime import date, timedelta

    from mihomes.models.contract import Contract

    q = session.query(Contract)
    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(Contract.property_id == prop.id)
    if inp.get("expiring_within_days"):
        cutoff = date.today() + timedelta(days=int(inp["expiring_within_days"]))
        q = q.filter(Contract.end_date <= cutoff, Contract.end_date >= date.today())
    if inp.get("vendor"):
        from mihomes.models.vendor import Vendor
        q = q.join(Vendor, Contract.vendor_id == Vendor.id, isouter=True)
        q = q.filter(Vendor.company_name.ilike(f"%{inp['vendor']}%"))

    contracts = q.all()
    if not contracts:
        return "No contracts found matching the criteria."

    lines = [f"Found {len(contracts)} contract(s):"]
    for c in contracts:
        vendor_name = c.vendor.company_name if c.vendor else "unknown vendor"
        prop_name = c.property.name if c.property else "unknown"
        end = str(c.end_date) if c.end_date else "ongoing"
        cost = f" ${c.annualized_cost:,.0f}/yr" if c.annualized_cost else ""
        renew = " (auto-renew)" if c.auto_renew else ""
        lines.append(f"- {vendor_name} → {prop_name}: ends {end}{cost}{renew}")
    return "\n".join(lines)


def _query_insurance(session: Session, inp: dict) -> str:
    from mihomes.models.insurance import InsurancePolicy

    q = session.query(InsurancePolicy)
    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(InsurancePolicy.property_id == prop.id)
    if inp.get("insurance_type"):
        q = q.filter(InsurancePolicy.insurance_type.ilike(f"%{inp['insurance_type']}%"))

    policies = q.all()
    if not policies:
        return "No insurance policies found."

    lines = [f"Found {len(policies)} policy/policies:"]
    for p in policies:
        prop_name = p.property.name if p.property else "general"
        coverage = f" coverage ${p.coverage_limit:,.0f}" if p.coverage_limit else ""
        renewal = str(p.renewal_date) if p.renewal_date else "no date"
        premium = f" premium ${p.annual_premium:,.0f}/yr" if getattr(p, "annual_premium", None) else ""
        lines.append(f"- {p.carrier} ({p.insurance_type.value}) → {prop_name}{coverage}{premium}, renewal {renewal}")
    return "\n".join(lines)


def _query_consumables(session: Session, inp: dict) -> str:
    from mihomes.models.consumable import Consumable

    q = session.query(Consumable)
    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(Consumable.property_id == prop.id)
    if inp.get("category"):
        q = q.filter(Consumable.category.ilike(f"%{inp['category']}%"))
    if inp.get("low_stock_only"):
        q = q.filter(
            Consumable.par_level.isnot(None),
            Consumable.quantity_in_stock < Consumable.par_level,
        )
    if inp.get("search"):
        q = q.filter(Consumable.name.ilike(f"%{inp['search']}%"))

    limit = int(inp.get("limit", 30))
    items = q.limit(limit).all()
    total = q.count()

    if not items:
        return "No consumables found matching the criteria."

    lines = [f"Found {total} consumable(s) (showing {len(items)}):"]
    for c in items:
        prop_name = c.property.name if c.property else "unknown"
        stock = f"stock: {c.quantity_in_stock} {c.unit or ''}".strip()
        par = f", par: {c.par_level}" if c.par_level else ""
        cat = f" [{c.category}]" if c.category else ""
        low = " ⚠️ LOW" if (c.par_level and c.quantity_in_stock is not None and c.quantity_in_stock < c.par_level) else ""
        lines.append(f"- {c.name}{cat} @ {prop_name} — {stock}{par}{low}")
    return "\n".join(lines)


def _query_inventory(session: Session, inp: dict) -> str:
    """Room/estate inventory = durable Assets + Consumable stock (spec M44/Q4).

    The old implementation queried a phantom ``inventory_items`` table that no
    migration ever created, so every call raised "no such table". Inventory is
    modelled as ``Asset`` (durable, room-placed, valued) plus ``Consumable``
    (stocked supplies), so this queries both ORM entities.
    """
    from mihomes.models.asset import Asset, AssetType
    from mihomes.models.consumable import Consumable
    from mihomes.models.space import Space

    prop = None
    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])

    # --- Durable assets ---
    aq = session.query(Asset).filter(Asset.active.is_(True))
    if prop is not None:
        aq = aq.filter(Asset.property_id == prop.id)
    if inp.get("space"):
        aq = aq.join(Space, Asset.space_id == Space.id).filter(Space.name.ilike(f"%{inp['space']}%"))
    if inp.get("category"):
        cat = inp["category"]
        aq = aq.filter((Asset.asset_type.ilike(f"%{cat}%")) | (Asset.name.ilike(f"%{cat}%")))
    if inp.get("high_value_only"):
        aq = aq.filter(Asset.asset_type == AssetType.VALUABLE)
    if inp.get("search"):
        aq = aq.filter(Asset.name.ilike(f"%{inp['search']}%"))

    # --- Consumable stock (no space/high-value concept; excluded when those filter) ---
    cq = session.query(Consumable)
    if prop is not None:
        cq = cq.filter(Consumable.property_id == prop.id)
    if inp.get("category"):
        cq = cq.filter(Consumable.category.ilike(f"%{inp['category']}%"))
    if inp.get("search"):
        cq = cq.filter(Consumable.name.ilike(f"%{inp['search']}%"))
    include_consumables = not inp.get("space") and not inp.get("high_value_only")

    assets = aq.all()
    consumables = cq.all() if include_consumables else []

    if inp.get("count_only"):
        asset_val = sum(a.purchase_price or 0 for a in assets)
        stock_val = sum((c.unit_price or 0) * (c.quantity_in_stock or 0) for c in consumables)
        total = len(assets) + len(consumables)
        val = asset_val + stock_val
        val_str = f", total estimated value ${val:,.0f}" if val else ""
        return f"Inventory: {total} item(s) ({len(assets)} assets, {len(consumables)} consumables){val_str}"

    limit = int(inp.get("limit", 20))
    total = len(assets) + len(consumables)
    rows = []
    for a in assets:
        space = f" in {a.space.name}" if a.space else ""
        prop_name = f" @ {a.property.name}" if a.property else ""
        val = f", est. ${a.purchase_price:,.0f}" if a.purchase_price else ""
        hv = " ★ high-value" if a.asset_type == AssetType.VALUABLE else ""
        rows.append(f"- {a.name} [{a.asset_type.value}]{space}{prop_name}{val}{hv}")
    for c in consumables:
        prop_name = f" @ {c.property.name}" if c.property else ""
        cat = f" [{c.category}]" if c.category else ""
        qty = f", {c.quantity_in_stock:g} {c.unit or 'units'} in stock" if c.quantity_in_stock is not None else ""
        rows.append(f"- {c.name}{cat}{prop_name}{qty} ({c.status.value})")

    if not rows:
        return "No inventory items found matching the criteria."

    rows = rows[:limit]
    return "\n".join([f"Found {total} inventory item(s) (showing {len(rows)}):", *rows])


def _query_events(session: Session, inp: dict) -> str:
    from datetime import date

    from mihomes.models.event import Event, EventStatus

    q = session.query(Event)
    if inp.get("property_slug"):
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, inp["property_slug"])
        q = q.filter(Event.property_id == prop.id)
    if inp.get("upcoming_only"):
        q = q.filter(Event.event_date >= date.today())
    if inp.get("status"):
        try:
            q = q.filter(Event.status == EventStatus(inp["status"]))
        except ValueError:
            pass

    events = q.order_by(Event.event_date).all()
    if not events:
        return "No events found matching the criteria."

    lines = [f"Found {len(events)} event(s):"]
    for e in events:
        prop_name = e.property.name if e.property else "unknown"
        guests = f", {e.expected_guests} guests" if e.expected_guests else ""
        budget = f", budget ${e.budget:,.0f}" if e.budget else ""
        lines.append(f"- {e.title} @ {prop_name} on {e.event_date} — {e.status.value}{guests}{budget}")
    return "\n".join(lines)


def _query_property(session: Session, inp: dict) -> str:
    from mihomes.models.property import Property
    from mihomes.models.space import Space

    if inp.get("property_slug"):
        from mihomes.services.slug import resolve_identifier
        props = [resolve_identifier(session, Property, inp["property_slug"])]
    else:
        props = session.query(Property).all()

    if not props:
        return "No properties found."

    lines = []
    for p in props:
        occ = ", occupied" if p.occupied else ""
        sqft = f", {p.sqft:,.0f} sqft" if p.sqft else ""
        lines.append(
            f"## {p.name} (slug: {p.slug})\n"
            f"  Type: {p.property_type.value}, Status: {p.status.value}{occ}{sqft}\n"
            f"  Climate: {p.climate_zone or 'default'}"
        )
        if getattr(p, "address", None):
            lines.append(f"  Address: {p.address}")
        if inp.get("include_spaces", True):
            spaces = session.query(Space).filter(Space.property_id == p.id).all()
            if spaces:
                space_list = ", ".join(f"{s.name} ({s.space_type})" for s in spaces)
                lines.append(f"  Spaces/Rooms: {space_list}")
    return "\n".join(lines)


def _query_alerts(session: Session, inp: dict) -> str:
    from mihomes.models.alert import AlertSeverity, AlertStatus
    from mihomes.services.alerts import list_alerts

    # Route through the service so resolved/snoozed alerts are excluded by
    # default (spec M33 — the old direct query returned every alert regardless
    # of status while claiming to show only active ones).
    status = (
        _parse_enum(AlertStatus, inp["status"], "status") if inp.get("status") else None
    )
    alerts = list_alerts(
        session, status=status, include_snoozed=bool(inp.get("include_snoozed", False))
    )
    if inp.get("severity"):
        sev = _parse_enum(AlertSeverity, inp["severity"], "severity")
        alerts = [a for a in alerts if a.severity == sev]

    if not alerts:
        return "No active alerts."

    lines = [f"Found {len(alerts)} alert(s):"]
    for a in alerts:
        lines.append(f"- [{a.severity.value}] {a.message}")
    return "\n".join(lines)


# ── Executor registry ──────────────────────────────────────────────────────────

_EXECUTORS = {
    "query_library": _query_library,
    "query_assets": _query_assets,
    "query_tasks": _query_tasks,
    "query_issues": _query_issues,
    "query_work_orders": _query_work_orders,
    "query_budget": _query_budget,
    "query_vendors": _query_vendors,
    "query_staff": _query_staff,
    "query_contracts": _query_contracts,
    "query_insurance": _query_insurance,
    "query_consumables": _query_consumables,
    "query_inventory": _query_inventory,
    "query_events": _query_events,
    "query_property": _query_property,
    "query_alerts": _query_alerts,
}
