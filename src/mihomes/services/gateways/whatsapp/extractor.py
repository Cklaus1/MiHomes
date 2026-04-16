"""WhatsApp message extractor — auto-creates issues and tasks from group conversations."""

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mihomes.config import MIHOMES_DIR
from mihomes.services.config_service import get_config, set_config
from mihomes.services.gateways.whatsapp.client import WhatsAppClient
from mihomes.services.gateways.whatsapp.review import analyze_messages

LAST_RUN_KEY = "whatsapp.last_extract_ts"

# Persisted set of message IDs already processed — prevents duplicate
# issue/task creation if the extractor runs before the timestamp advances
# or if messages arrive with out-of-order timestamps.
PROCESSED_IDS_FILE = MIHOMES_DIR / "whatsapp-processed-ids.json"
MAX_PROCESSED_IDS = 5000  # cap file size; oldest entries are pruned first


def extract_and_create(
    session: Session,
    property_slug: str | None = None,
    since: datetime | None = None,
) -> dict:
    """Fetch new WhatsApp messages, AI-analyze, and auto-create issues and tasks.

    Uses two deduplication layers:
    1. Timestamp tracking — only fetches messages newer than the last run.
    2. Message ID tracking — skips any message whose ID was already processed,
       guarding against timestamp races and bridge restarts replaying old messages.

    Returns:
        dict with: issues_created, tasks_created, messages_processed, skipped, errors
    """
    client = WhatsAppClient()

    # Resolve since timestamp: explicit arg > stored config > None (all messages)
    if since is None:
        stored = get_config(session, LAST_RUN_KEY)
        if stored:
            try:
                since = datetime.fromisoformat(stored)
            except ValueError:
                since = None

    messages = client.get_messages(since=since, limit=500)

    if not messages:
        _update_last_run(session)
        return _empty_result(0)

    # Scope to property if requested
    if property_slug:
        messages = [m for m in messages if m.get("propertySlug") == property_slug]

    if not messages:
        _update_last_run(session)
        return _empty_result(0)

    # Deduplicate by message ID
    processed_ids = _load_processed_ids()
    new_messages = [m for m in messages if m.get("id") not in processed_ids]

    if not new_messages:
        _update_last_run(session)
        return _empty_result(0)

    # Run AI extraction on new messages only
    result = analyze_messages(session, new_messages, property_name=property_slug)
    items = result.get("items", [])
    skipped = result.get("skipped", [])

    issues_created = 0
    tasks_created = 0
    errors = []

    for item in items:
        category = item.get("category", "")
        title = item.get("title", "Unknown")
        description = item.get("description")
        prop = item.get("property_slug") or property_slug

        if category in ("informational", "task_completion", "vendor_activity"):
            continue

        if not prop:
            errors.append(f"No property resolved for '{title}' — skipped")
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
                create_issue(session, title, prop, severity=sev, description=description)
                issues_created += 1
            elif category in ("task", "supply_need"):
                from mihomes.services.task import create_task
                create_task(session, title, prop, description=description)
                tasks_created += 1
        except Exception as e:
            errors.append(f"Failed to create '{title}': {e}")

    # Mark all fetched messages as processed (not just actionable ones)
    new_ids = [m["id"] for m in new_messages if m.get("id")]
    _save_processed_ids(processed_ids | set(new_ids))
    _update_last_run(session)

    return {
        "issues_created": issues_created,
        "tasks_created": tasks_created,
        "messages_processed": len(new_messages),
        "skipped": len(skipped),
        "errors": errors,
    }


def _load_processed_ids() -> set:
    """Load the set of already-processed message IDs from disk."""
    if PROCESSED_IDS_FILE.exists():
        try:
            return set(json.loads(PROCESSED_IDS_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _save_processed_ids(ids: set) -> None:
    """Persist processed message IDs, pruning oldest if over the cap."""
    id_list = list(ids)
    if len(id_list) > MAX_PROCESSED_IDS:
        id_list = id_list[-MAX_PROCESSED_IDS:]
    try:
        PROCESSED_IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROCESSED_IDS_FILE.write_text(json.dumps(id_list))
    except OSError:
        pass  # Non-fatal — worst case we re-process a message


def _update_last_run(session: Session) -> None:
    set_config(session, LAST_RUN_KEY, datetime.now(timezone.utc).isoformat())


def _empty_result(messages_processed: int) -> dict:
    return {
        "issues_created": 0,
        "tasks_created": 0,
        "messages_processed": messages_processed,
        "skipped": 0,
        "errors": [],
    }
