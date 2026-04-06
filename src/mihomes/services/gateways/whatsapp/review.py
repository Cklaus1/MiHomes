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
                    "quantity_in_stock": {"type": "number"},
                    "quantity_to_order": {"type": "number"},
                    "unit": {"type": "string"},
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
        "- issue: reporting something broken, damaged, malfunctioning, or needing repair (e.g. 'toilet is broken', 'AC not working')\n"
        "- task: requesting a specific action be performed (e.g. 'please clean the pool', 'order more towels')\n"
        "- task_completion: confirming work was completed (e.g. 'done', 'finished the cleaning')\n"
        "- supply_need: something needs purchasing or restocking. "
        "Extract quantity_in_stock if the message says how much is left (e.g. '1 bottle left', 'only 2 rolls'). "
        "Extract quantity_to_order if the message says how much to buy (e.g. 'need to order 3', 'get 2 more'). "
        "Extract unit if mentioned (bottles, rolls, bags, boxes, etc.).\n"
        "- vendor_activity: a vendor visit or service happening\n"
        "- question: asking for information, status, schedules, or updates (e.g. 'what is the AC status?', 'when is the next pool check?', 'has the plumber been called?', 'check AC repair status')\n"
        "- informational: social chat, greetings, personal messages, or anything unrelated to the home\n\n"
        "IMPORTANT DISTINCTION — question vs task:\n"
        "- If the message is asking for INFORMATION or STATUS → question\n"
        "- If the message is reporting a PROBLEM → issue\n"
        "- If the message is requesting an ACTION to be performed → task\n"
        "'Check X status', 'what is the status of X', 'has X been done', 'when is X scheduled' are ALL questions.\n\n"
        "For questions: 'title' = concise restatement, 'description' = full question text verbatim.\n"
        "For issues/tasks: extract title, description, severity (if issue), reporter, related asset.\n\n"
        "Only classify as 'question' if genuinely about the home, property, maintenance, staff, vendors, or estate. "
        "Greetings and off-topic chat are 'informational'.\n\n"
        "Also list skipped items with brief reasons.\n"
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
