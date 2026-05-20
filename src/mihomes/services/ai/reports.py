"""AI-powered report generation — Situation Reports and Estate Digests."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.orm import Session

from mihomes.models.ai_conversation import AIConversation
from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
from mihomes.services.ai.orchestrator import AIResponse, _get_session_id, _save_session_id
from mihomes.services.ai.provider import get_provider

if TYPE_CHECKING:
    from mihomes.services.ai.file_processor import Attachment

# ── Situation Report ─────────────────────────────────────────────────────────

SITUATION_REPORT_PROMPT = """\
Act as a world-class residential estate manager, owner's representative, and building-systems advisor.
Prepare a comprehensive homeowner-facing advisory report based on the input provided below.

Write for a homeowner who wants clear executive guidance — not technical jargon. Be practical,
financially aware, and skeptical of unnecessary upsells while recognizing legitimate repair risks.
Act as the homeowner's advocate. Distinguish facts from assumptions clearly.

Structure the report with these sections:

## 1. Executive Summary
- Current status of the issue
- What appears urgent vs non-urgent
- Likely root cause
- Top 3 recommended actions
- Total estimated financial exposure

## 2. Property/System Background
- Affected area, system, appliance, or component
- Known age, condition, prior repairs, warranty, service history
- Missing information that should be confirmed

## 3. Contractor Estimate Review
(If estimates or quotes are included — skip this section with a note if not applicable)
For each estimate: scope, line-item breakdown, what is necessary vs optional vs unclear,
fair-market price assessment, red flags, questions to ask before approval.

## 4. Options Analysis
Compare all reasonable paths (do nothing / minor repair / full repair / preventive /
replacement / upgrade / second opinion). For each option: expected cost, pros, cons,
risk level, expected useful life, when to choose it, when to avoid it.

## 5. Financial Impact
Immediate cost, 1-year / 5-year / 10-year implications, repair-vs-replace economics,
rebates/tax credits/warranties, risk of cascading costs if deferred.

## 6. Risk Assessment
Rate each as Low / Medium / High: Safety, Property Damage, Comfort, Cost Escalation,
Reliability, Contractor Execution, Hidden-Condition.

## 7. Recommendation Matrix
A ranked table: Recommended Action | Priority | Estimated Cost | Confidence | Reasoning | Dependencies.

## 8. Final Recommendation
What to approve now | defer | reject | get a second opinion on | what documentation to require before payment.

## 9. Homeowner Questions / Contractor Script
Exact language the owner or property manager should send to the contractor:
clarifying questions, requests for photos/model numbers/diagnostics/warranty status/itemized pricing,
negotiation points, approval conditions.

## 10. Missing Information Checklist
Everything needed to increase confidence (equipment age, model/serial, photos, diagnostics,
warranty status, maintenance history, competing quotes).

## 11. Confidence Levels
For each major conclusion: High / Medium / Low confidence, and what would change it.

Tone: Professional, calm, direct, homeowner-friendly. No jargon. Flag upsells and vague line items.\
"""


def generate_situation_report(
    session: Session,
    content: str,
    *,
    subject: str = "",
    work_order_slug: str | None = None,
    property_slug: str | None = None,
    attachments: list[Attachment] | None = None,
) -> AIResponse:
    """Generate a structured homeowner advisory report from user-provided input."""
    context_lines: list[str] = []

    if work_order_slug:
        try:
            from mihomes.models.work_order import WorkOrder
            from mihomes.services.slug import resolve_identifier
            wo = resolve_identifier(session, WorkOrder, work_order_slug)
            vendor_name = (wo.vendor.company_name if wo.vendor else wo.vendor_name) or "Unassigned"
            context_lines.append("## Linked Work Order (from estate records)")
            context_lines.append(f"Title: {wo.title}")
            context_lines.append(f"Property: {wo.property.name}")
            context_lines.append(f"Status: {wo.status.value}")
            context_lines.append(f"Vendor: {vendor_name}")
            if wo.estimated_cost:
                context_lines.append(f"Estimated Cost: ${wo.estimated_cost:,.0f}")
            if wo.actual_cost:
                context_lines.append(f"Actual Cost: ${wo.actual_cost:,.0f}")
            if wo.description:
                context_lines.append(f"Description: {wo.description[:600]}")
        except Exception:
            pass

    if property_slug:
        try:
            from mihomes.services.property import get_property
            from mihomes.services.issue import list_issues
            from mihomes.services.asset import list_assets
            prop = get_property(session, property_slug)
            context_lines.append(f"\n## Property Context: {prop.name}")
            context_lines.append(f"Type: {prop.property_type.value}, Status: {prop.status.value}")
            if prop.year_built:
                context_lines.append(f"Year Built: {prop.year_built}")
            open_issues = list_issues(session, property_id_or_slug=property_slug, open_only=True)
            if open_issues:
                context_lines.append(
                    f"Open issues ({len(open_issues)}): "
                    + ", ".join(f"[{i.severity.value}] {i.title}" for i in open_issues[:5])
                )
        except Exception:
            pass

    subject_line = f"Subject: {subject}\n" if subject else ""
    user_message = f"{subject_line}\n{content}" if subject_line else content
    if context_lines:
        user_message = "Estate records context:\n" + "\n".join(context_lines) + "\n\n---\n\n" + user_message

    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    model = get_ai_model(session, provider_name)
    provider = get_provider(provider_name, api_key)

    response_text = provider.complete(SITUATION_REPORT_PROMPT, user_message, attachments=attachments)

    session_id = _get_session_id(False)
    session.add(AIConversation(
        session_id=session_id,
        role="situation_report",
        user_message=f"[Situation Report] {subject or type_label}: {content[:200]}",
        ai_response=response_text,
        context_summary=f"situation_report; property:{property_slug or 'all'}; wo:{work_order_slug or 'none'}",
        provider=provider_name,
        model=model,
    ))
    session.flush()
    _save_session_id(session_id)

    return AIResponse(text=response_text, role="Estate Advisor", session_id=session_id)


# ── Estate Digest ─────────────────────────────────────────────────────────────

ESTATE_DIGEST_PROMPT = """\
You are a world-class estate manager writing a periodic summary report for the property owner.

Based on the estate activity data below, write a clear, narrative-style digest that:
- Leads with the most important items (safety, urgent issues, significant spend)
- Groups by theme (operations, maintenance, financials, staff, looking ahead)
- Is written in professional prose — not just bullet dumps
- Flags anything requiring owner attention or approval
- Notes patterns, trends, or concerns worth the owner's awareness
- Ends with a concise "Looking Ahead" section (upcoming deadlines, planned work, decisions needed)

Think of this as a weekly letter from your estate manager to the owner.
Format with clear ## section headers. Be specific — name properties, vendors, amounts, and staff.\
"""


def generate_estate_digest(
    session: Session,
    start_date: date,
    end_date: date,
    *,
    property_slug: str | None = None,
    attachments: list[Attachment] | None = None,
) -> AIResponse:
    """Generate a narrative estate digest for a time period, pulling live data from the DB."""
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    prop_filter = None
    if property_slug:
        try:
            from mihomes.models.property import Property
            from mihomes.services.slug import resolve_identifier
            prop_filter = resolve_identifier(session, Property, property_slug)
        except Exception:
            pass

    period_label = f"{start_date.strftime('%B %d')} – {end_date.strftime('%B %d, %Y')}"
    lines = [f"## Estate Activity: {period_label}"]
    if prop_filter:
        lines.append(f"Scope: {prop_filter.name}")

    # Tasks
    try:
        from mihomes.models.task import Task, TaskStatus
        q_done = session.query(Task).filter(
            Task.status == TaskStatus.DONE,
            Task.updated_at >= start_dt,
            Task.updated_at <= end_dt,
        )
        if prop_filter:
            q_done = q_done.filter(Task.property_id == prop_filter.id)
        tasks_done = q_done.all()

        q_overdue = session.query(Task).filter(
            Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]),
            Task.due_date < end_date,
        )
        if prop_filter:
            q_overdue = q_overdue.filter(Task.property_id == prop_filter.id)
        tasks_overdue = q_overdue.all()

        lines.append("\n### Tasks")
        if tasks_done:
            lines.append(f"Completed this period ({len(tasks_done)}):")
            for t in tasks_done[:20]:
                assignee = t.assignee.name if t.assignee else "unassigned"
                lines.append(f"  - {t.title} @ {t.property.name} — {assignee}")
        else:
            lines.append("  No tasks completed this period.")
        if tasks_overdue:
            lines.append(f"Still overdue ({len(tasks_overdue)}):")
            for t in tasks_overdue[:10]:
                lines.append(f"  - [{t.priority.value}] {t.title} @ {t.property.name}, due {t.due_date}")
    except Exception:
        pass

    # Work Orders & Vendors
    try:
        from mihomes.models.work_order import WorkOrder, WorkOrderStatus
        q_new = session.query(WorkOrder).filter(
            WorkOrder.created_at >= start_dt,
            WorkOrder.created_at <= end_dt,
        )
        q_done = session.query(WorkOrder).filter(
            WorkOrder.status.in_([WorkOrderStatus.COMPLETED, WorkOrderStatus.VERIFIED]),
            WorkOrder.updated_at >= start_dt,
            WorkOrder.updated_at <= end_dt,
        )
        if prop_filter:
            q_new = q_new.filter(WorkOrder.property_id == prop_filter.id)
            q_done = q_done.filter(WorkOrder.property_id == prop_filter.id)
        wos_new = q_new.all()
        wos_done = q_done.all()

        lines.append("\n### Work Orders & Vendors")
        if wos_new:
            lines.append(f"New work orders opened ({len(wos_new)}):")
            for wo in wos_new:
                vendor = (wo.vendor.company_name if wo.vendor else wo.vendor_name) or "TBD"
                cost = f"${wo.estimated_cost:,.0f} est." if wo.estimated_cost else "no estimate"
                lines.append(f"  - {wo.title} @ {wo.property.name} — {vendor}, {cost}")
        if wos_done:
            lines.append(f"Work orders completed ({len(wos_done)}):")
            for wo in wos_done:
                vendor = (wo.vendor.company_name if wo.vendor else wo.vendor_name) or "unknown"
                actual = (
                    f"${wo.actual_cost:,.0f} actual" if wo.actual_cost
                    else (f"${wo.estimated_cost:,.0f} est." if wo.estimated_cost else "cost unknown")
                )
                lines.append(f"  - {wo.title} @ {wo.property.name} — {vendor}, {actual}")
        if not wos_new and not wos_done:
            lines.append("  No work order activity this period.")
    except Exception:
        pass

    # Issues
    try:
        from mihomes.models.issue import Issue, IssueStatus
        q_new = session.query(Issue).filter(
            Issue.created_at >= start_dt,
            Issue.created_at <= end_dt,
        )
        q_resolved = session.query(Issue).filter(
            Issue.status.in_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
            Issue.updated_at >= start_dt,
            Issue.updated_at <= end_dt,
        )
        if prop_filter:
            q_new = q_new.filter(Issue.property_id == prop_filter.id)
            q_resolved = q_resolved.filter(Issue.property_id == prop_filter.id)
        issues_new = q_new.all()
        issues_resolved = q_resolved.all()

        lines.append("\n### Issues")
        if issues_new:
            lines.append(f"New issues reported ({len(issues_new)}):")
            for i in issues_new:
                reporter = i.reported_by.name if getattr(i, "reported_by", None) else ""
                reporter_str = f" — reported by {reporter}" if reporter else ""
                lines.append(f"  - [{i.severity.value}] {i.title} @ {i.property.name}{reporter_str}")
        if issues_resolved:
            lines.append(f"Issues resolved ({len(issues_resolved)}):")
            for i in issues_resolved:
                lines.append(f"  - {i.title} @ {i.property.name}")
        if not issues_new and not issues_resolved:
            lines.append("  No issue activity this period.")
    except Exception:
        pass

    # Financial Activity
    try:
        from mihomes.models.budget import Transaction
        q_spend = session.query(
            Transaction.category,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("cnt"),
        ).filter(
            Transaction.date >= start_date,
            Transaction.date <= end_date,
        )
        if prop_filter:
            q_spend = q_spend.filter(Transaction.property_id == prop_filter.id)
        spend_rows = q_spend.group_by(Transaction.category).all()

        lines.append("\n### Financial Activity")
        if spend_rows:
            total = sum(r.total for r in spend_rows)
            lines.append(f"Total spend: ${total:,.0f} across {len(spend_rows)} categories")
            for r in sorted(spend_rows, key=lambda x: x.total, reverse=True)[:10]:
                lines.append(f"  - {r.category}: ${r.total:,.0f} ({r.cnt} transaction{'s' if r.cnt != 1 else ''})")
        else:
            lines.append("  No transactions recorded this period.")
    except Exception:
        pass

    # Staff snapshot
    try:
        from mihomes.services.staff import list_staff
        staff = list_staff(session)
        if staff:
            lines.append("\n### Staff")
            for s in staff[:10]:
                props = ", ".join(p.name for p in s.properties) or "unassigned"
                lines.append(f"  - {s.name} ({s.role.value}) — {props}")
    except Exception:
        pass

    # Upcoming context (next 14 days after end_date)
    try:
        from mihomes.services.task import get_upcoming_tasks
        upcoming = get_upcoming_tasks(session, days=14, property_id_or_slug=property_slug)
        if upcoming:
            lines.append("\n### Upcoming (next 14 days)")
            for t in upcoming[:10]:
                lines.append(f"  - {t.title} @ {t.property.name}, due {t.due_date}")
    except Exception:
        pass

    estate_data = "\n".join(lines)
    user_message = f"Generate an estate digest for {period_label}.\n\n{estate_data}"

    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    model = get_ai_model(session, provider_name)
    provider = get_provider(provider_name, api_key)

    response_text = provider.complete(ESTATE_DIGEST_PROMPT, user_message, attachments=attachments)

    session_id = _get_session_id(False)
    session.add(AIConversation(
        session_id=session_id,
        role="estate_digest",
        user_message=f"[Estate Digest] {period_label}",
        ai_response=response_text,
        context_summary=f"estate_digest; {period_label}; property:{property_slug or 'all'}",
        provider=provider_name,
        model=model,
    ))
    session.flush()
    _save_session_id(session_id)

    return AIResponse(text=response_text, role="Estate Manager", session_id=session_id)
