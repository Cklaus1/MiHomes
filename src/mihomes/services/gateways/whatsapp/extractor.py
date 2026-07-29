"""WhatsApp message extractor — auto-creates issues and tasks from group conversations."""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mihomes.services.config_service import get_config, set_config
from mihomes.services.gateways.whatsapp.client import WhatsAppClient
from mihomes.services.gateways.whatsapp.review import analyze_messages

LAST_RUN_KEY = "whatsapp.last_extract_ts"

# Persisted set of message IDs already processed — prevents duplicate
# issue/task creation if the extractor runs before the timestamp advances
# or if messages arrive with out-of-order timestamps.
# M22: share ONE dedup store with the monitor (same config key), not a separate
# sidecar file, so an id handled by either poller is seen by both.
PROCESSED_IDS_KEY = "whatsapp.processed_ids"
MAX_PROCESSED_IDS = 5000  # cap store size; oldest entries are pruned first


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

    messages = client.drain_messages(since=since, limit=500)

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
    result = analyze_messages(session, new_messages, property_name=property_slug, property_slug=property_slug)
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
    _add_processed_ids(new_ids)
    _update_last_run(session)

    return {
        "issues_created": issues_created,
        "tasks_created": tasks_created,
        "messages_processed": len(new_messages),
        "skipped": len(skipped),
        "errors": errors,
    }


def _id_store() -> "ProcessedIdStore":
    from mihomes.services.gateways.dedup import ProcessedIdStore

    return ProcessedIdStore(PROCESSED_IDS_KEY, cap=MAX_PROCESSED_IDS)


def _load_processed_ids() -> set:
    """Load the set of already-processed message IDs (shared store)."""
    return set(_id_store().load())


def _add_processed_ids(ids) -> None:
    """Append processed message IDs to the shared store (prunes oldest)."""
    _id_store().add(ids)


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
