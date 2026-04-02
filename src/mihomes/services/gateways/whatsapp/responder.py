"""WhatsApp responder — logs issues/tasks and answers questions via AI."""

import re

from sqlalchemy.orm import Session

from mihomes.services.gateways.whatsapp.client import WhatsAppClient
from mihomes.services.gateways.whatsapp.review import analyze_messages


def _ask_ai(session: Session, question: str, property_slug: str | None) -> str:
    """Ask the AI advisor and return a concise WhatsApp-friendly plain-text answer."""
    try:
        from mihomes.services.ai.orchestrator import ask
        # Instruct the AI to be brief and plain — no markdown, no bullet points
        whatsapp_question = (
            f"{question}\n\n"
            "Reply in 2-3 sentences maximum. Plain text only — no bullet points, "
            "no headers, no markdown. Be direct and specific."
        )
        response = ask(session, whatsapp_question, role="estate_manager", property_slug=property_slug)
        text = response.text.strip()
        # Strip any markdown the AI still included
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        text = re.sub(r'\*(.*?)\*', r'\1', text)
        text = re.sub(r'#{1,6}\s*', '', text)
        text = re.sub(r'[-•]\s+', '', text)
        text = re.sub(r'\n{2,}', ' ', text).strip()
        return f"🏠 {text}"
    except Exception:
        return "🏠 I don't have that information available right now."


def process_and_respond(
    session: Session,
    messages: list[dict],
    property_slug: str | None = None,
) -> dict:
    """
    Analyze messages, create issues/tasks, answer questions.

    - Issues/tasks/supply_needs: log + send '🏠 "title" logged ✓'
    - Questions about the home: send AI advisory answer
    - Informational/irrelevant: no response

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

    logged = 0
    replied = 0
    errors = []

    for item in items:
        category = item.get("category", "")
        title = item.get("title", "Unknown")
        prop = item.get("property_slug") or property_slug

        # --- Questions: answer with AI ---
        if category == "question":
            question_text = item.get("description") or title
            answer = _ask_ai(session, question_text, prop or property_slug)
            if answer:
                try:
                    client.send_group_message(reply_jid, answer)
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to send answer: {e}")
            continue

        # --- Actionable items: log + confirm ---
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

        try:
            client.send_group_message(reply_jid, f'🏠 "{title}" logged ✓')
            replied += 1
        except Exception as e:
            errors.append(f"Failed to send confirmation for '{title}': {e}")

    return {"replied": replied, "logged": logged, "errors": errors}
