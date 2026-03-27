"""AI assessors — structured output for severity assessment, import parsing."""

from sqlalchemy.orm import Session

from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
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
