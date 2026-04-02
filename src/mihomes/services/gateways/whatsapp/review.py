"""WhatsApp conversation review — AI-powered passive issue detection."""

from datetime import datetime

from sqlalchemy.orm import Session

from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
from mihomes.services.ai.provider import get_provider


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["issue", "task", "task_completion", "supply_need", "vendor_activity", "question", "informational"],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "reported_by": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "related_asset": {"type": "string"},
                },
                "required": ["category", "title"],
            },
        },
        "skipped": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
    },
    "required": ["items"],
}


def analyze_messages(
    session: Session,
    messages: list[dict],
    property_name: str | None = None,
) -> dict:
    """Analyze WhatsApp messages and extract actionable items.

    Returns dict with 'items' (actionable) and 'skipped' (non-actionable).
    """
    if not messages:
        return {"items": [], "skipped": []}

    # Format messages for AI
    formatted = []
    for msg in messages:
        sender = msg.get("senderName", "Unknown")
        text = msg.get("text", "")
        ts = msg.get("timestamp", "")
        media = " [photo]" if msg.get("hasMedia") else ""
        formatted.append(f"[{ts}] {sender}: {text}{media}")

    conversation_text = "\n".join(formatted)

    system_prompt = (
        "You are analyzing a WhatsApp staff group chat for a property management system. "
        "Extract ALL actionable items from the conversation.\n\n"
        "Classify each message or message cluster into:\n"
        "- issue: something broken, damaged, malfunctioning, or needing repair\n"
        "- task: someone requesting something to be done\n"
        "- task_completion: someone confirming work was done\n"
        "- supply_need: something needs purchasing or restocking\n"
        "- vendor_activity: a vendor visit or service happening\n"
        "- question: someone asking a question about the property, maintenance, schedules, vendors, or estate status\n"
        "- informational: no action needed — social chat, greetings, personal messages, food, anything unrelated to the home\n\n"
        "For questions: use 'title' as a concise restatement of the question, and 'description' as the full question text verbatim.\n"
        "For issues/tasks: extract title, description, severity (if issue), who reported it, and any related asset.\n\n"
        "Only classify as 'question' if it is genuinely about the home, property, maintenance, staff, vendors, or estate. "
        "Greetings, nonsensical messages, and off-topic chat are 'informational'.\n\n"
        "Also list items you skipped with brief reasons.\n\n"
        "Be thorough — even terse messages like 'deer treatment' are task requests.\n"
        "Correlate related messages (e.g., low tire + possible hole = severity upgrade)."
    )

    context = f"Property: {property_name}" if property_name else ""

    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    provider = get_provider(provider_name, api_key)

    result = provider.structured_output(
        system_prompt, conversation_text, REVIEW_SCHEMA,
        context_data=context or None,
    )

    return result
