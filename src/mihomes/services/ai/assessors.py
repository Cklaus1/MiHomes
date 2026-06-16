"""AI assessors — structured output for severity assessment, import parsing."""

from sqlalchemy.orm import Session

from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_provider_name
from mihomes.services.ai.provider import get_provider

IMPORT_SCHEMAS = {
    "vendor": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "company_name": {"type": "string"},
                        "contact_name": {"type": "string"},
                        "phone": {"type": "string"},
                        "email": {"type": "string"},
                        "categories": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["company_name"],
                },
            }
        },
        "required": ["items"],
    },
    "task": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "priority": {"type": "string", "enum": ["urgent", "high", "medium", "low"]},
                        "due_date": {"type": "string"},
                        "description": {"type": "string"},
                        "recurrence": {"type": "string", "enum": ["once", "weekly", "biweekly", "monthly", "quarterly", "seasonal", "annual"]},
                    },
                    "required": ["title"],
                },
            }
        },
        "required": ["items"],
    },
    "issue": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                        "description": {"type": "string"},
                    },
                    "required": ["title"],
                },
            }
        },
        "required": ["items"],
    },
}


SUGGESTION_SCHEMAS = {
    "task": {
        "type": "object",
        "properties": {
            "priority": {
                "type": "string",
                "enum": ["urgent", "high", "medium", "low"],
                "description": "Recommended priority based on title and description",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2–4 short, lowercase tags relevant to the task (e.g. 'plumbing', 'urgent-repair', 'seasonal')",
                "maxItems": 4,
            },
            "reasoning": {"type": "string", "description": "One-sentence explanation of suggestions"},
        },
        "required": ["priority", "tags"],
    },
    "issue": {
        "type": "object",
        "properties": {
            "severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low"],
                "description": "Recommended severity based on title and description",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2–4 short, lowercase tags relevant to the issue (e.g. 'water-damage', 'safety', 'electrical')",
                "maxItems": 4,
            },
            "reasoning": {"type": "string", "description": "One-sentence explanation of suggestions"},
        },
        "required": ["severity", "tags"],
    },
}


def suggest_tags_and_priority(
    session: Session,
    entity_type: str,
    title: str,
    description: str | None = None,
    property_name: str | None = None,
) -> dict:
    """Use AI to suggest tags and priority/severity for a new task or issue.

    Returns a dict with 'tags' (list[str]) and 'priority' or 'severity' (str),
    plus optional 'reasoning' (str).
    """
    schema = SUGGESTION_SCHEMAS.get(entity_type)
    if schema is None:
        raise ValueError(f"Suggestions not supported for entity type: {entity_type}")

    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    provider = get_provider(provider_name, api_key)

    system_prompt = (
        f"You are an estate management AI. Given the title and optional description of a new {entity_type}, "
        f"suggest the most appropriate priority/severity and 2-4 relevant lowercase tags. "
        "Base severity on safety risk, urgency, and impact. Use the SPACE framework (Safety, Presence, "
        "Asset Protection, Compliance, Economy) to guide priority. Tags should be short, lowercase, "
        "hyphenated (e.g. 'water-damage', 'seasonal', 'safety')."
    )

    user_text = f"Title: {title}"
    if description:
        user_text += f"\nDescription: {description}"
    if property_name:
        user_text += f"\nProperty: {property_name}"

    return provider.structured_output(system_prompt, user_text, schema)


ASSET_SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short descriptive name, e.g. 'Leather sofa', 'Samsung 65\" TV'"},
                    "asset_type": {"type": "string", "enum": ["appliance", "valuable", "equipment", "vehicle"]},
                    "condition": {"type": "string", "enum": ["excellent", "good", "fair", "poor"]},
                    "make": {"type": "string"},
                    "model": {"type": "string"},
                    "estimated_value": {"type": "number", "description": "Rough resale/replacement value in USD (a best-guess estimate)"},
                    "note": {"type": "string", "description": "Any distinguishing detail (color, location in room, visible damage)"},
                },
                "required": ["name", "asset_type"],
            },
        }
    },
    "required": ["items"],
}


def parse_room_scan(session: Session, attachments, room_name: str | None = None) -> list[dict]:
    """Identify trackable assets from photo(s) of a room using AI vision.

    Returns a list of item dicts (see ASSET_SCAN_SCHEMA). Requires the Claude
    provider — only it sends image attachments to the model.
    """
    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    provider = get_provider(provider_name, api_key)

    where = f" of the {room_name}" if room_name else ""
    system_prompt = (
        "You are an estate inventory assistant. From the attached photo(s)"
        f"{where}, identify the distinct, durable physical assets worth tracking "
        "in a home inventory (furniture, electronics, appliances, equipment, art, "
        "valuables). For each, give a short name, the best-fitting asset_type, a "
        "condition estimate, make/model if legible, and a rough USD value estimate. "
        "Ignore disposable, trivial, or fixed structural items (e.g. trash, food, "
        "outlets, flooring). If unsure of a value, give your best estimate."
    )
    user_msg = "List the trackable assets visible in these photo(s)."
    result = provider.structured_output(system_prompt, user_msg, ASSET_SCAN_SCHEMA, attachments=attachments)
    return result.get("items", [])


def parse_import_text(
    session: Session,
    entity_type: str,
    text: str,
    property_slug: str | None = None,
) -> list[dict]:
    """Use AI to parse unstructured text into structured records."""
    schema = IMPORT_SCHEMAS.get(entity_type)
    if schema is None:
        raise ValueError(f"Import not supported for entity type: {entity_type}. Supported: {', '.join(IMPORT_SCHEMAS.keys())}")

    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    provider = get_provider(provider_name, api_key)

    system_prompt = (
        f"You are a data extraction assistant. Parse the following text into structured {entity_type} records. "
        f"Extract every {entity_type} you can find. Be thorough — even partial information is useful. "
        "If a field is not mentioned, omit it."
    )

    context = f"Target property: {property_slug}" if property_slug else ""
    result = provider.structured_output(system_prompt, text, schema, context_data=context or None)
    return result.get("items", [])
