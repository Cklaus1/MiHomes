"""AI orchestrator — central coordinator for AI advisory."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mihomes.config import MIHOMES_DIR
from mihomes.models.ai_conversation import AIConversation
from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
from mihomes.services.ai.context import assemble_context
from mihomes.services.ai.provider import AIProvider, get_provider
from mihomes.services.ai.roles import ROLES, route_query

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
) -> AIResponse:
    """Process an AI ask query."""
    # Resolve provider
    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    model = get_ai_model(session, provider_name)
    provider = get_provider(provider_name, api_key)

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
    response_text = provider.complete(system_prompt, query, context_data=context)

    # Store conversation
    convo = AIConversation(
        session_id=session_id,
        role=primary_role.name,
        user_message=query,
        ai_response=response_text,
        context_summary=f"Roles: {', '.join(r.name for r in roles)}; property: {property_slug or 'all'}",
        provider=provider_name,
        model=model,
    )
    session.add(convo)
    session.flush()

    # Save session ID
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
