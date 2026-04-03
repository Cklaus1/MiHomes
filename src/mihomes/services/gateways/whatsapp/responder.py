"""WhatsApp responder — logs issues/tasks and answers questions via AI."""

import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from mihomes.services.gateways.whatsapp.client import WhatsAppClient
from mihomes.services.gateways.whatsapp.review import analyze_messages


def _parse_event_date(timestamp_str: str | None) -> date | None:
    """Parse an AI-extracted timestamp string into a date, or return None."""
    if not timestamp_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(timestamp_str, fmt).date()
        except ValueError:
            continue
    return None


def _strip_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'[-•]\s+', '', text)
    return text.strip()


def _ai_response(session: Session, prompt: str, role: str, property_slug: str | None) -> str | None:
    """Call the AI advisor and return a plain-text 1-2 sentence response, or None on failure."""
    try:
        from mihomes.services.ai.orchestrator import ask
        full_prompt = (
            f"{prompt}\n\n"
            "Reply in 1-2 sentences maximum. Plain text only — no bullet points, "
            "no headers, no markdown. Be direct and practical. "
            "If you are not confident in your answer or do not have enough information, "
            "respond with exactly: NO_RESPONSE"
        )
        response = ask(session, full_prompt, role=role, property_slug=property_slug)
        text = _strip_markdown(response.text.strip())
        text = re.sub(r'\n{2,}', ' ', text)
        if not text or text.upper() == "NO_RESPONSE":
            return None
        return text
    except Exception:
        return None


def _issue_expert_reply(session: Session, title: str, description: str | None, property_slug: str | None) -> str | None:
    """Get a maintenance expert assessment for a logged issue."""
    context = description or title
    prompt = (
        f"A maintenance issue was just reported at a property: '{title}'. "
        f"Details: {context}. "
        "As a maintenance expert, give a brief practical assessment: "
        "what this likely requires and what the immediate next step should be."
    )
    return _ai_response(session, prompt, role="maintenance", property_slug=property_slug)


def _answer_question(session: Session, question: str, property_slug: str | None) -> str | None:
    """Answer a home-related question using the estate manager role."""
    return _ai_response(session, question, role="estate_manager", property_slug=property_slug)


def process_and_respond(
    session: Session,
    messages: list[dict],
    property_slug: str | None = None,
) -> dict:
    """
    Analyze messages, create issues/tasks, answer questions.

    - Issues: log + send '🏠 "title" logged ✓\\n\\n[maintenance expert assessment]'
    - Tasks/supply_needs with date: log + create event + send '🏠 scheduled "title" ✓'
    - Tasks/supply_needs without date: log + send '🏠 "title" logged ✓'
    - Vendor activity: create event + send '🏠 scheduled "title" ✓'
    - Questions about the home: send '🏠 [AI estate manager answer]'
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

        # --- Questions: answer with AI estate manager ---
        if category == "question":
            question_text = item.get("description") or title
            answer = _answer_question(session, question_text, prop or property_slug)
            if answer:
                try:
                    client.send_group_message(reply_jid, f"🏠 {answer}")
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to send answer: {e}")
            continue

        # --- Actionable items: log + confirm ---
        if category not in ("issue", "task", "supply_need", "vendor_activity"):
            continue

        if not prop:
            continue

        event_date = _parse_event_date(item.get("timestamp"))
        scheduled = event_date is not None

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
                if scheduled:
                    from mihomes.services.event import create_event
                    create_event(session, title, prop, event_date, description=item.get("description"))
            elif category == "vendor_activity":
                if scheduled:
                    from mihomes.services.event import create_event
                    create_event(session, title, prop, event_date, description=item.get("description"))
                    logged += 1
                else:
                    continue  # vendor activity without a date — no action needed
        except Exception as e:
            errors.append(f"Failed to create '{title}': {e}")
            continue  # Don't send confirmation if logging failed

        # Build confirmation message
        if category == "issue":
            expert_note = _issue_expert_reply(session, title, item.get("description"), prop)
            if expert_note:
                confirmation = f'🏠 "{title}" logged ✓\n\n{expert_note}'
            else:
                confirmation = f'🏠 "{title}" logged ✓'
        elif scheduled:
            confirmation = f'🏠 scheduled "{title}" ✓'
        else:
            confirmation = f'🏠 "{title}" logged ✓'

        try:
            client.send_group_message(reply_jid, confirmation)
            replied += 1
        except Exception as e:
            errors.append(f"Failed to send confirmation for '{title}': {e}")

    return {"replied": replied, "logged": logged, "errors": errors}
