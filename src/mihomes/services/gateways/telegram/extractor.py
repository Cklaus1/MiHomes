"""Telegram message extractor — auto-creates issues and tasks from group conversations."""

import json

from sqlalchemy.orm import Session

from mihomes.config import MIHOMES_DIR
from mihomes.services.config_service import get_config, set_config
from mihomes.services.gateways.telegram.review import analyze_messages

LAST_UPDATE_ID_KEY = "telegram.last_update_id"

PROCESSED_IDS_FILE = MIHOMES_DIR / "telegram-processed-ids.json"
MAX_PROCESSED_IDS = 5000


def extract_and_create(
    session: Session,
    property_slug: str | None = None,
) -> dict:
    """Fetch new Telegram messages, AI-analyze, and auto-create issues and tasks.

    Uses two deduplication layers:
    1. Update ID offset — only fetches updates newer than the last run.
    2. Message ID set — skips any message already processed.

    Returns:
        dict with: issues_created, tasks_created, messages_processed, skipped, errors
    """
    from mihomes.services.gateways.telegram.client import TelegramClient, TelegramError

    token = get_config(session, "telegram.bot_token")
    if not token:
        return {**_empty_result(0), "errors": ["telegram.bot_token not configured"]}

    chat_links_raw = get_config(session, "telegram.chat_links") or "{}"
    try:
        chat_links: dict = json.loads(chat_links_raw)
    except (json.JSONDecodeError, ValueError):
        chat_links = {}

    last_update_id = _load_last_update_id(session)
    offset = last_update_id + 1 if last_update_id is not None else None

    client = TelegramClient(token)
    try:
        updates = client.get_updates(offset=offset, timeout=0, limit=500)
    except TelegramError as e:
        return {**_empty_result(0), "errors": [f"Telegram API error: {e}"]}

    if not updates:
        _save_last_update_id(session, last_update_id)
        return _empty_result(0)

    new_last_update_id = max(u["update_id"] for u in updates)

    messages = []
    for update in updates:
        msg = client.normalize_update(update, chat_links)
        if msg:
            messages.append(msg)

    if property_slug:
        messages = [m for m in messages if m.get("propertySlug") == property_slug]

    if not messages:
        _save_last_update_id(session, new_last_update_id)
        return _empty_result(len(updates))

    processed_ids = _load_processed_ids()
    new_messages = [m for m in messages if m.get("id") not in processed_ids]

    if not new_messages:
        _save_last_update_id(session, new_last_update_id)
        return _empty_result(len(updates))

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
                from mihomes.models.issue import IssueSeverity
                from mihomes.services.issue import create_issue
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

    new_ids = [m["id"] for m in new_messages if m.get("id")]
    _save_processed_ids(processed_ids | set(new_ids))
    _save_last_update_id(session, new_last_update_id)

    return {
        "issues_created": issues_created,
        "tasks_created": tasks_created,
        "messages_processed": len(new_messages),
        "skipped": len(skipped),
        "errors": errors,
    }


def _load_last_update_id(session: Session) -> int | None:
    stored = get_config(session, LAST_UPDATE_ID_KEY)
    if stored:
        try:
            return int(stored)
        except (ValueError, TypeError):
            pass
    return None


def _save_last_update_id(session: Session, update_id: int | None) -> None:
    if update_id is not None:
        set_config(session, LAST_UPDATE_ID_KEY, str(update_id))


def _load_processed_ids() -> set:
    if PROCESSED_IDS_FILE.exists():
        try:
            return set(json.loads(PROCESSED_IDS_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _save_processed_ids(ids: set) -> None:
    id_list = list(ids)
    if len(id_list) > MAX_PROCESSED_IDS:
        id_list = id_list[-MAX_PROCESSED_IDS:]
    try:
        PROCESSED_IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROCESSED_IDS_FILE.write_text(json.dumps(id_list))
    except OSError:
        pass


def _empty_result(messages_processed: int) -> dict:
    return {
        "issues_created": 0,
        "tasks_created": 0,
        "messages_processed": messages_processed,
        "skipped": 0,
        "errors": [],
    }
