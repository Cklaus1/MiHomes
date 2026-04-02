"""WhatsApp responder — logs issues/tasks and sends a simple confirmation."""

from sqlalchemy.orm import Session

from mihomes.services.gateways.whatsapp.client import WhatsAppClient
from mihomes.services.gateways.whatsapp.review import analyze_messages


def process_and_respond(
    session: Session,
    messages: list[dict],
    property_slug: str | None = None,
) -> dict:
    """
    Analyze messages, create issues/tasks, and send a simple confirmation
    back to the group for each successfully logged item.

    Sends nothing if an item could not be logged.

    Returns dict with: logged, replied, errors.
    """
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    client = WhatsAppClient()
    result = analyze_messages(session, messages, property_name=property_slug)
    items = result.get("items", [])

    # Find the group JID to reply to
    reply_jid = next(
        (m["jid"] for m in messages if m.get("jid") and (m.get("propertySlug") or property_slug)),
        None,
    )
    if not reply_jid:
        return {"replied": 0, "logged": 0, "errors": ["No linked group JID found"]}

    # Build a map of original message text by sender for the confirmation quote
    # Use the last message text as a fallback quote
    last_text = messages[-1].get("text", "") if messages else ""

    logged = 0
    replied = 0
    errors = []

    for item in items:
        category = item.get("category", "")
        title = item.get("title", "Unknown")
        prop = item.get("property_slug") or property_slug

        # Only log actionable categories
        if category not in ("issue", "task", "supply_need"):
            continue

        if not prop:
            continue

        try:
            if category == "issue":
                from mihomes.services.issue import create_issue
                from mihomes.models.issue import IssueSeverity
                sev_val = item.get("severity", "medium")
                try:
                    sev = IssueSeverity(sev_val)
                except ValueError:
                    sev = IssueSeverity.MEDIUM
                create_issue(session, title, prop, severity=sev, description=item.get("description"))
                logged += 1
            elif category in ("task", "supply_need"):
                from mihomes.services.task import create_task
                create_task(session, title, prop, description=item.get("description"))
                logged += 1
        except Exception as e:
            errors.append(f"Failed to create '{title}': {e}")
            continue  # Don't send confirmation if logging failed

        # Send simple confirmation — quote the title as logged
        try:
            confirmation = f'"{title}" logged ✓'
            client.send_group_message(reply_jid, confirmation)
            replied += 1
        except Exception as e:
            errors.append(f"Failed to send confirmation for '{title}': {e}")

    return {"replied": replied, "logged": logged, "errors": errors}
