"""Context assembly — build structured text from existing services for AI prompts."""

from datetime import date

from sqlalchemy.orm import Session

from mihomes.models.ai_conversation import AIConversation
from mihomes.services.ai.roles import AIRole


def assemble_context(
    session: Session,
    roles: list[AIRole],
    query: str,
    *,
    property_slug: str | None = None,
    session_id: str | None = None,
    max_tokens: int = 50000,
) -> str:
    """Build a structured context string for the AI prompt."""
    # Merge data categories from all active roles
    categories = set()
    for role in roles:
        categories.update(role.data_categories)

    sections = []
    token_est = 0

    if "properties" in categories:
        text = _fetch_properties(session, property_slug)
        sections.append(text)
        token_est += len(text) // 4

    if "tasks" in categories and token_est < max_tokens:
        text = _fetch_tasks(session, property_slug)
        sections.append(text)
        token_est += len(text) // 4

    if "issues" in categories and token_est < max_tokens:
        text = _fetch_issues(session, property_slug)
        sections.append(text)
        token_est += len(text) // 4

    if "budgets" in categories and token_est < max_tokens:
        text = _fetch_budgets(session, property_slug)
        sections.append(text)
        token_est += len(text) // 4

    if "alerts" in categories and token_est < max_tokens:
        text = _fetch_alerts(session)
        sections.append(text)
        token_est += len(text) // 4

    if "contracts" in categories and token_est < max_tokens:
        text = _fetch_contracts(session, property_slug)
        sections.append(text)
        token_est += len(text) // 4

    if "insurance" in categories and token_est < max_tokens:
        text = _fetch_insurance(session, property_slug)
        sections.append(text)
        token_est += len(text) // 4

    if "vendors" in categories and token_est < max_tokens:
        text = _fetch_vendors(session)
        sections.append(text)
        token_est += len(text) // 4

    if "staff" in categories and token_est < max_tokens:
        text = _fetch_staff(session, property_slug)
        sections.append(text)
        token_est += len(text) // 4

    if token_est < max_tokens:
        text = _fetch_assets(session, property_slug)
        if text:
            sections.append(text)
            token_est += len(text) // 4

    if token_est < max_tokens:
        text = _fetch_books(session, property_slug)
        if text:
            sections.append(text)
            token_est += len(text) // 4

    if token_est < max_tokens:
        text = _fetch_work_orders(session, property_slug)
        if text:
            sections.append(text)
            token_est += len(text) // 4

    if token_est < max_tokens:
        text = _fetch_weather(session, property_slug)
        if text:
            sections.append(text)
            token_est += len(text) // 4

    # Conversation history
    if session_id and token_est < max_tokens:
        text = _fetch_conversation_history(session, session_id)
        if text:
            sections.append(text)

    return "\n\n".join(s for s in sections if s.strip())


def _fetch_properties(session: Session, property_slug: str | None) -> str:
    from mihomes.services.issue import list_issues
    from mihomes.services.property import list_properties

    lines = ["## Properties"]
    props = list_properties(session)
    for p in props:
        open_issues = len(list_issues(session, property_id_or_slug=str(p.id), open_only=True))
        occ = ", occupied" if p.occupied else ""
        lines.append(
            f"- {p.name} ({p.property_type.value}, {p.status.value}{occ}): "
            f"climate={p.climate_zone or 'default'}, {open_issues} open issues"
        )
    return "\n".join(lines)


def _fetch_tasks(session: Session, property_slug: str | None) -> str:
    from mihomes.services.task import get_overdue_tasks, get_upcoming_tasks

    lines = ["## Tasks"]
    overdue = get_overdue_tasks(session, property_id_or_slug=property_slug)
    if overdue:
        lines.append(f"### Overdue ({len(overdue)})")
        for t in overdue[:20]:
            assignee = t.assignee.name if t.assignee else "unassigned"
            prop_name = t.property.name if t.property else "unknown"
            lines.append(f"- [{t.priority.value}] {t.title} @ {prop_name} — due {t.due_date}, {assignee}")

    upcoming = get_upcoming_tasks(session, days=30, property_id_or_slug=property_slug)
    if upcoming:
        lines.append(f"### Upcoming 30 days ({len(upcoming)})")
        for t in upcoming[:20]:
            prop_name = t.property.name if t.property else "unknown"
            lines.append(f"- [{t.priority.value}] {t.title} @ {prop_name} — due {t.due_date}")

    if len(lines) == 1:
        lines.append("No overdue or upcoming tasks.")
    return "\n".join(lines)


def _fetch_issues(session: Session, property_slug: str | None) -> str:
    from mihomes.services.issue import list_issues

    lines = ["## Open Issues"]
    issues = list_issues(session, property_id_or_slug=property_slug, open_only=True)
    if not issues:
        lines.append("No open issues.")
    else:
        for i in issues[:20]:
            space = f" ({i.space.name})" if i.space else ""
            prop_name = i.property.name if i.property else "unknown"
            lines.append(f"- [{i.severity.value}] {i.title} @ {prop_name}{space} — {i.status.value}")
            if i.description:
                lines.append(f"  Description: {i.description[:200]}")
    return "\n".join(lines)


def _fetch_budgets(session: Session, property_slug: str | None) -> str:
    from mihomes.services.budget import get_budget_report
    from mihomes.services.property import list_properties

    today = date.today()
    year_start = date(today.year, 1, 1)
    year_end = date(today.year + 1, 1, 1)
    lines = ["## Budget Status (YTD)"]

    props = list_properties(session)
    for p in props:
        if property_slug and p.slug != property_slug:
            continue
        report = get_budget_report(session, str(p.id), year_start, year_end)
        if report:
            total_budgeted = sum(r["budgeted"] for r in report)
            total_spent = sum(r["spent"] for r in report)
            pct = round(total_spent / total_budgeted * 100, 1) if total_budgeted else 0
            lines.append(f"- {p.name}: ${total_spent:,.0f} / ${total_budgeted:,.0f} ({pct}% used)")
            for r in report:
                if r["pct_used"] > 75:
                    lines.append(f"  WARNING: {r['category']} at {r['pct_used']}%")

    if len(lines) == 1:
        lines.append("No budgets set.")
    return "\n".join(lines)


def _fetch_alerts(session: Session) -> str:
    from mihomes.services.alerts import list_alerts

    lines = ["## Active Alerts"]
    alerts = list_alerts(session)
    if not alerts:
        lines.append("No active alerts.")
    else:
        for a in alerts[:10]:
            lines.append(f"- [{a.severity.value}] {a.message}")
    return "\n".join(lines)


def _fetch_contracts(session: Session, property_slug: str | None) -> str:
    from mihomes.services.contract import list_contracts

    lines = ["## Contracts"]
    contracts = list_contracts(session, property_id_or_slug=property_slug)
    if not contracts:
        lines.append("No contracts.")
    else:
        for c in contracts[:15]:
            end = str(c.end_date) if c.end_date else "ongoing"
            cost = f"${c.annualized_cost:,.0f}/yr" if c.annualized_cost else ""
            renew = " (auto-renew)" if c.auto_renew else ""
            vendor_name = c.vendor.company_name if c.vendor else "unknown vendor"
            prop_name = c.property.name if c.property else "unknown"
            lines.append(f"- {vendor_name} → {prop_name}: {end} {cost}{renew}")
    return "\n".join(lines)


def _fetch_insurance(session: Session, property_slug: str | None) -> str:
    from mihomes.services.insurance import list_policies

    lines = ["## Insurance Policies"]
    policies = list_policies(session, property_id_or_slug=property_slug)
    if not policies:
        lines.append("No policies.")
    else:
        for p in policies[:15]:
            prop_name = p.property.name if p.property else "general"
            renewal = str(p.renewal_date) if p.renewal_date else "no date"
            lines.append(
                f"- {p.carrier} ({p.insurance_type.value}) → {prop_name}: "
                f"coverage ${p.coverage_limit:,.0f}, renewal {renewal}" if p.coverage_limit else
                f"- {p.carrier} ({p.insurance_type.value}) → {prop_name}: renewal {renewal}"
            )
    return "\n".join(lines)


def _fetch_vendors(session: Session) -> str:
    from mihomes.services.vendor import list_vendors

    lines = ["## Vendors"]
    vendors = list_vendors(session)
    if not vendors:
        lines.append("No vendors.")
    else:
        for v in vendors:
            cats = ", ".join(v.service_categories) if v.service_categories else "general"
            lines.append(f"- {v.company_name} ({cats})")
    return "\n".join(lines)


def _fetch_staff(session: Session, property_slug: str | None) -> str:
    from mihomes.services.staff import list_staff

    lines = ["## Staff"]
    staff = list_staff(session, category="Staff")
    if not staff:
        lines.append("No staff.")
    else:
        for s in staff[:15]:
            props = ", ".join(p.name for p in s.properties) or "unassigned"
            lines.append(f"- {s.name} ({s.role.value}) — {props}")
    return "\n".join(lines)


def _fetch_assets(session: Session, property_slug: str | None) -> str:
    from sqlalchemy import func

    from mihomes.models.asset import Asset

    base = session.query(Asset).filter(Asset.active.is_(True))
    if property_slug:
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, property_slug)
        base = base.filter(Asset.property_id == prop.id)

    total = base.count()
    if not total:
        return ""

    lines = [f"## Assets ({total} total)"]

    # Summary by type
    by_type = (
        session.query(Asset.asset_type, func.count(Asset.id))
        .filter(Asset.active.is_(True))
        .group_by(Asset.asset_type)
        .all()
    )
    if by_type:
        lines.append("By type: " + ", ".join(f"{t.value}: {n}" for t, n in by_type))

    # List up to 20 notable assets
    assets = base.limit(20).all()
    for a in assets:
        warranty = f", warranty expires {a.warranty_expires}" if a.warranty_expires else ""
        val = f", value ${a.purchase_price:,.0f}" if a.purchase_price else ""
        prop_name = a.property.name if a.property else "unknown"
        lines.append(f"- {a.name} ({a.asset_type.value}) @ {prop_name}{val}{warranty}")

    return "\n".join(lines)


def _fetch_books(session: Session, property_slug: str | None) -> str:
    from sqlalchemy import func

    from mihomes.models.book import Book

    base = session.query(Book).filter(Book.active.is_(True))
    if property_slug:
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, property_slug)
        base = base.filter(Book.property_id == prop.id)

    total = base.count()
    if not total:
        return ""

    lines = [f"## Library / Books ({total} total)"]

    # Count by property
    by_prop = (
        session.query(Book.property_id, func.count(Book.id))
        .filter(Book.active.is_(True))
        .group_by(Book.property_id)
        .all()
    )
    from mihomes.models.property import Property as Prop
    for prop_id, cnt in by_prop:
        p = session.get(Prop, prop_id)
        prop_name = p.name if p else f"property {prop_id}"
        lines.append(f"- {prop_name}: {cnt} books")

    # Genre breakdown (top 10)
    by_genre = (
        session.query(Book.genre, func.count(Book.id))
        .filter(Book.active.is_(True), Book.genre.isnot(None))
        .group_by(Book.genre)
        .order_by(func.count(Book.id).desc())
        .limit(10)
        .all()
    )
    if by_genre:
        lines.append("Top genres: " + ", ".join(f"{g} ({n})" for g, n in by_genre))

    return "\n".join(lines)


def _fetch_work_orders(session: Session, property_slug: str | None) -> str:
    from mihomes.models.work_order import WorkOrder, WorkOrderStatus

    lines = ["## Open Work Orders"]
    query = session.query(WorkOrder).filter(
        WorkOrder.status.notin_([WorkOrderStatus.COMPLETED, WorkOrderStatus.VERIFIED, WorkOrderStatus.CANCELLED]),
    )
    if property_slug:
        from mihomes.models.property import Property
        from mihomes.services.slug import resolve_identifier
        prop = resolve_identifier(session, Property, property_slug)
        query = query.filter(WorkOrder.property_id == prop.id)
    work_orders = query.limit(15).all()
    if not work_orders:
        return ""
    for wo in work_orders:
        vendor = wo.vendor.company_name if wo.vendor else "unassigned"
        cost = f"${wo.estimated_cost:,.0f}" if wo.estimated_cost else "no estimate"
        prop_name = wo.property.name if wo.property else "unknown"
        lines.append(f"- [{wo.status.value}] {wo.title} @ {prop_name} — {vendor}, {cost}")
    return "\n".join(lines)


def _fetch_weather(session: Session, property_slug: str | None) -> str:
    from mihomes.models.property import Property
    from mihomes.services.weather import forecast_summary, get_forecast_for_property

    query = session.query(Property)
    if property_slug:
        from mihomes.services.slug import resolve_identifier
        try:
            prop = resolve_identifier(session, Property, property_slug)
            props = [prop]
        except (ValueError, KeyError):
            props = []
    else:
        props = query.all()

    summaries = []
    for prop in props:
        try:
            forecast = get_forecast_for_property(session, prop)
            if forecast:
                summaries.append(forecast_summary(forecast))
        except (OSError, KeyError, ValueError, TypeError):
            pass

    return "\n\n".join(summaries) if summaries else ""


def _fetch_conversation_history(session: Session, session_id: str) -> str:
    history = session.query(AIConversation).filter(
        AIConversation.session_id == session_id,
    ).order_by(AIConversation.created_at.desc()).limit(3).all()

    if not history:
        return ""

    lines = ["## Previous Conversation"]
    for h in reversed(history):
        lines.append(f"User: {h.user_message[:300]}")
        lines.append(f"AI ({h.role}): {h.ai_response[:500]}")
        lines.append("")
    return "\n".join(lines)
