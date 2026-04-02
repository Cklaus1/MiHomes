"""WhatsApp AI responder — analyzes incoming messages and sends AI advisory replies."""

from sqlalchemy.orm import Session

from mihomes.services.gateways.whatsapp.client import WhatsAppClient
from mihomes.services.gateways.whatsapp.review import analyze_messages


def _build_reply(item: dict, issue_id: int | None = None, task_id: int | None = None) -> str:
    """Build a WhatsApp-formatted reply for a logged item."""
    category = item.get("category", "")
    title = item.get("title", "Unknown")
    severity = item.get("severity", "")
    advice = item.get("description") or ""

    sev_icon = {"critical": "🚨", "high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "")

    if category == "issue":
        ref = f" (#{issue_id})" if issue_id else ""
        lines = [f"✅ *Issue logged{ref}* — {title}"]
        if severity:
            lines.append(f"Severity: {sev_icon} {severity.capitalize()}")
        if advice:
            lines.append(f"\n*AI Assessment:* {advice}")
        lines.append("\n_MiHomes AI — Estate Advisor_")
    elif category in ("task", "supply_need"):
        ref = f" (#{task_id})" if task_id else ""
        lines = [f"📋 *Task logged{ref}* — {title}"]
        if advice:
            lines.append(f"\n*Note:* {advice}")
        lines.append("\n_MiHomes AI — Estate Advisor_")
    else:
        lines = [f"ℹ️ *Noted* — {title}", "\n_MiHomes AI_"]

    return "\n".join(lines)


def _build_advisory_reply(question: str, session: Session, property_slug: str | None) -> str:
    """Ask the AI advisor and format a WhatsApp-friendly reply."""
    try:
        from mihomes.services.ai.orchestrator import ask
        response = ask(session, question, role="maintenance", property_slug=property_slug)
        # Trim to ~500 chars for WhatsApp readability
        text = response.text.strip()
        if len(text) > 500:
            text = text[:497] + "..."
        return f"*AI Estate Advisor:*\n{text}\n\n_MiHomes AI_"
    except Exception as e:
        return f"_AI advisor unavailable: {e}_"


def process_and_respond(
    session: Session,
    messages: list[dict],
    property_slug: str | None = None,
) -> dict:
    """
    Analyze messages, create issues/tasks, and send AI replies back to the group.

    Returns dict with counts of replies sent.
    """
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    client = WhatsAppClient()
    result = analyze_messages(session, messages, property_name=property_slug)
    items = result.get("items", [])

    replied = 0
    logged = 0
    errors = []

    # Group messages by JID so we know where to reply
    jid_map = {}
    for m in messages:
        if m.get("jid"):
            jid_map[m["jid"]] = m.get("propertySlug") or property_slug

    # Pick the first linked group JID to reply to
    reply_jid = next((jid for jid, slug in jid_map.items() if slug), None)
    if not reply_jid:
        return {"replied": 0, "logged": 0, "errors": ["No linked group JID found"]}

    for item in items:
        category = item.get("category", "")
        title = item.get("title", "Unknown")
        prop = item.get("property_slug") or property_slug

        if category in ("informational", "vendor_activity"):
            continue

        if category == "task_completion":
            # Acknowledge task completions
            try:
                client.send_group_message(reply_jid, f"✅ *Noted* — {title} marked as complete.\n\n_MiHomes AI_")
                replied += 1
            except Exception as e:
                errors.append(str(e))
            continue

        if not prop:
            continue

        issue_id = None
        task_id = None

        try:
            if category == "issue":
                from mihomes.services.issue import create_issue
                from mihomes.models.issue import IssueSeverity
                sev_val = item.get("severity", "medium")
                try:
                    sev = IssueSeverity(sev_val)
                except ValueError:
                    sev = IssueSeverity.MEDIUM
                issue = create_issue(session, title, prop, severity=sev, description=item.get("description"))
                issue_id = issue.id
                logged += 1
            elif category in ("task", "supply_need"):
                from mihomes.services.task import create_task
                task = create_task(session, title, prop, description=item.get("description"))
                task_id = task.id
                logged += 1
        except Exception as e:
            errors.append(f"Failed to create '{title}': {e}")
            continue

        # Send AI reply back to the group
        try:
            reply = _build_reply(item, issue_id=issue_id, task_id=task_id)
            client.send_group_message(reply_jid, reply)
            replied += 1
        except Exception as e:
            errors.append(f"Failed to send reply for '{title}': {e}")

    return {"replied": replied, "logged": logged, "errors": errors}
