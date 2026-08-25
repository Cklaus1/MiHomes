"""AI orchestrator — central coordinator for AI advisory."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from mihomes.config import MIHOMES_DIR
from mihomes.models.ai_conversation import AIConversation
from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
from mihomes.services.ai.context import assemble_context
from mihomes.services.ai.provider import get_provider
from mihomes.services.ai.roles import ROLES, route_query

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from mihomes.services.ai.file_processor import Attachment

SESSION_FILE = MIHOMES_DIR / "current_ai_session"
SESSION_TIMEOUT_MINUTES = 30


@dataclass
class AIResponse:
    text: str
    role: str
    session_id: str
    tokens_used: int | None = None
    suggestions: list[str] = field(default_factory=list)


def ask(
    session: Session,
    query: str,
    *,
    role: str | None = None,
    property_slug: str | None = None,
    continue_session: bool = False,
    attachments: list[Attachment] | None = None,
) -> AIResponse:
    """Process an AI ask query.

    Args:
        session: SQLAlchemy database session.
        query: The user's natural language question.
        role: Force a specific AI role (e.g., 'financial', 'maintenance').
              If None, auto-routes based on query keywords.
        property_slug: Scope context assembly to a single property.
        continue_session: If True, loads conversation history from the
              previous session (within 30 min timeout).

    Returns:
        AIResponse with the AI's text, active role, and session ID.
    """
    # Resolve provider
    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    model = get_ai_model(session, provider_name)
    provider = get_provider(provider_name, api_key, model=model, entry_point="ai.orchestrator")

    # Route to role(s)
    roles = route_query(query, explicit_role=role)
    primary_role = roles[0]

    # Get or create session ID
    session_id = _get_session_id(continue_session)

    # Assemble context
    context = assemble_context(
        session, roles, query,
        property_slug=property_slug,
        session_id=session_id if continue_session else None,
    )

    # Build system prompt
    if len(roles) > 1:
        system_prompt = (
            f"You are acting as multiple advisors: {', '.join(r.display_name for r in roles)}.\n\n"
            + roles[0].system_prompt
        )
    else:
        system_prompt = primary_role.system_prompt

    # Call AI
    response_text = provider.complete(system_prompt, query, context_data=context, attachments=attachments)

    # Log conversation in a separate session so a DB lock here never
    # corrupts the caller's transaction or rolls back real work.
    try:
        from mihomes.db import get_session as _get_log_session
        with _get_log_session() as _log:
            _log.add(AIConversation(
                session_id=session_id,
                role=primary_role.name,
                user_message=query,
                ai_response=response_text,
                context_summary=f"Roles: {', '.join(r.name for r in roles)}; property: {property_slug or 'all'}",
                provider=provider_name,
                model=model,
            ))
    except Exception:
        logger.exception("ask: suppressed exception")

    _save_session_id(session_id)

    return AIResponse(
        text=response_text,
        role=primary_role.display_name,
        session_id=session_id,
    )


def review(
    session: Session,
    *,
    property_slug: str | None = None,
) -> AIResponse:
    """Proactive AI review — analyze current state and make recommendations."""
    query = (
        "Review the current state of the estate and provide prioritized recommendations. "
        "Focus on: overdue tasks, budget variances over 75%, upcoming deadlines in 30 days "
        "(contracts, insurance, certifications), open critical/high issues, and any seasonal "
        "preparations needed based on the current date and property climate zones. "
        "Rank all recommendations using the SPACE framework."
    )
    return ask(session, query, role="estate_manager", property_slug=property_slug)


def dashboard_summary(
    session: Session,
    *,
    property_slug: str | None = None,
) -> AIResponse:
    """Compact 3-5 bullet summary for the dashboard AI panel."""
    query = (
        "Give me exactly 3 to 5 prioritized action items for the estate right now. "
        "Each item must be a single line, starting with its SPACE priority letter (S, P, A, C, or E) "
        "followed by a dash and the recommendation. Example: 'A - Beach House: roof leak worsening, "
        "schedule inspection before Thursday rain forecast.' "
        "Focus on what needs attention this week: overdue tasks, open critical/high issues, "
        "upcoming deadlines within 30 days, budget overruns, and seasonal preparations. "
        "Be specific — name the property and the issue. "
        "Output only the bullet lines, no headers, no extra commentary."
    )
    return ask(session, query, role="estate_manager", property_slug=property_slug)


def budget_review(
    session: Session,
    *,
    property_slug: str | None = None,
) -> AIResponse:
    """Deep financial review — variance analysis, anomalies, and optimization recommendations."""
    from datetime import date

    from mihomes.models.property import Property
    from mihomes.models.recurring_expense import RecurringExpense
    from mihomes.services.financial_report import spending_by_category, spending_by_vendor
    from mihomes.services.property import list_properties
    from mihomes.services.slug import resolve_identifier

    today = date.today()
    year_start = date(today.year, 1, 1)

    # Build rich financial context beyond what context.py provides
    extra_lines = ["## Detailed Financial Breakdown (YTD)"]

    props = list_properties(session)
    if property_slug:
        prop_obj = resolve_identifier(session, Property, property_slug)
        props = [p for p in props if p.id == prop_obj.id]

    for prop in props:
        extra_lines.append(f"\n### {prop.name}")

        # Spending by category YTD
        try:
            by_cat = spending_by_category(session, str(prop.id), year_start, today)
            if by_cat:
                extra_lines.append("**Spending by category (YTD):**")
                for r in by_cat:
                    extra_lines.append(f"  - {r['category']}: ${r['total']:,.0f} ({r['transaction_count']} transactions)")
        except Exception:
            logger.exception("budget_review: suppressed exception")

        # Top vendors by spend YTD
        try:
            by_vendor = spending_by_vendor(session, str(prop.id), year_start, today)
            if by_vendor:
                extra_lines.append("**Top vendors by spend (YTD):**")
                for r in by_vendor[:5]:
                    extra_lines.append(f"  - {r['vendor']}: ${r['total']:,.0f}")
        except Exception:
            logger.exception("budget_review: suppressed exception")

        # Recurring expenses annualized
        try:
            freq_multiplier = {"weekly": 52, "biweekly": 26, "monthly": 12, "quarterly": 4, "annual": 1}
            recurring = session.query(RecurringExpense).filter(
                RecurringExpense.property_id == prop.id,
                RecurringExpense.active.is_(True),
            ).all()
            if recurring:
                annual_total = sum(
                    r.amount * freq_multiplier.get(r.frequency.value, 12)
                    for r in recurring
                )
                extra_lines.append(f"**Recurring expenses ({len(recurring)} items, ~${annual_total:,.0f}/yr annualized):**")
                for r in recurring:
                    mult = freq_multiplier.get(r.frequency.value, 12)
                    extra_lines.append(f"  - {r.name}: ${r.amount:,.0f}/{r.frequency.value} (~${r.amount * mult:,.0f}/yr)")
        except Exception:
            logger.exception("budget_review: suppressed exception")

    extra_context = "\n".join(extra_lines)

    query = (
        "Perform a comprehensive budget review for the estate. Analyze:\n"
        "1. **Variance analysis** — which categories are over/under budget and by how much?\n"
        "2. **Anomalies** — any unusual spending patterns, spikes, or one-time costs that stand out?\n"
        "3. **Recurring expense load** — is the committed recurring spend sustainable vs. budget?\n"
        "4. **Optimization opportunities** — where can costs be reduced or reallocated?\n"
        "5. **Forecast flag** — based on YTD pace, which properties/categories will overshoot by year-end?\n"
        "6. **Action items** — specific, prioritized steps to improve financial health.\n\n"
        "Be specific with numbers. Reference actual property names, categories, and vendors. "
        "Use SPACE framework to prioritize any financial risks."
    )

    # Resolve provider
    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    model = get_ai_model(session, provider_name)
    provider = get_provider(provider_name, api_key, model=model, entry_point="ai.orchestrator")

    role = ROLES["financial"]
    session_id = _get_session_id(False)

    # Combine standard context with extra financial detail
    base_context = assemble_context(
        session, [role], query,
        property_slug=property_slug,
    )
    full_context = base_context + "\n\n" + extra_context

    response_text = provider.complete(role.system_prompt, query, context_data=full_context)

    try:
        from mihomes.db import get_session as _get_log_session
        with _get_log_session() as _log:
            _log.add(AIConversation(
                session_id=session_id,
                role=role.name,
                user_message=query,
                ai_response=response_text,
                context_summary=f"budget_review; property: {property_slug or 'all'}",
                provider=provider_name,
                model=model,
            ))
    except Exception:
        logger.exception("budget_review: suppressed exception")
    _save_session_id(session_id)

    return AIResponse(text=response_text, role=role.display_name, session_id=session_id)


def _get_session_id(continue_previous: bool) -> str:
    """Get or create a session ID for conversation continuity."""
    if continue_previous and SESSION_FILE.exists():
        data = SESSION_FILE.read_text().strip().split("|")
        if len(data) == 2:
            sid, ts = data
            try:
                last_time = datetime.fromisoformat(ts)
                if datetime.now(timezone.utc) - last_time < timedelta(minutes=SESSION_TIMEOUT_MINUTES):
                    return sid
            except ValueError:
                pass
    return uuid.uuid4().hex[:12]


def _save_session_id(session_id: str) -> None:
    """Save current session ID with timestamp."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(f"{session_id}|{datetime.now(timezone.utc).isoformat()}")
