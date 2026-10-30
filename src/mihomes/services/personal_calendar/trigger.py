"""Sync calendars soon after a save — off the request thread, never blocking the save.

evo-dev has no scheduler, so without this a change would only reach Google on "Sync now". A
session listener notes which accounts had a calendar-relevant row flushed; once that transaction
**commits** (never on rollback), each account is queued on a small worker pool, which opens its
own session under that account's context and runs `sync.sync_account`.

Deliberately coarse: any appointment, task, property or scope change in an account queues that
account. The sync is a diff, so a run with nothing to do costs a few reads; queuing per account
(not per row) means a bulk import queues once. An account already queued is not queued twice.

Off when `MIHOMES_CALENDAR_AUTOSYNC=0` — the test suite sets it, so a test never starts a thread
that talks to the application database behind its transaction.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_KEY = "mihomes_calendar_dirty_accounts"
_executor: ThreadPoolExecutor | None = None
_queued: set = set()
_lock = threading.Lock()
_installed = False


def _tracked_classes() -> tuple:
    from mihomes.models.appointment import Appointment
    from mihomes.models.membership import Membership, MembershipPropertyScope
    from mihomes.models.property import Property
    from mihomes.models.task import Task

    return (Appointment, Task, Property, Membership, MembershipPropertyScope)


def enabled() -> bool:
    return os.environ.get("MIHOMES_CALENDAR_AUTOSYNC", "1") != "0"


def _after_flush(session: Session, _ctx) -> None:
    tracked = _tracked_classes()
    dirty = session.info.setdefault(_KEY, set())
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, tracked):
            account_id = getattr(obj, "account_id", None)
            if account_id is not None:
                dirty.add(account_id)


def _after_commit(session: Session) -> None:
    accounts = session.info.pop(_KEY, None)
    if accounts and enabled():
        for account_id in accounts:
            request_sync(account_id)


def _after_rollback(session: Session, previous_transaction) -> None:
    # Only the outermost rollback discards the work; a savepoint rolled back inside a
    # transaction that later commits must not lose the accounts flushed before it.
    if previous_transaction.parent is None:
        session.info.pop(_KEY, None)


def request_sync(account_id) -> None:
    """Queue a sync of every connected calendar in this account (no-op if already queued)."""
    global _executor
    with _lock:
        if account_id in _queued:
            return
        _queued.add(account_id)
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="calendar-sync")
    _executor.submit(_run, account_id)


def _run(account_id) -> None:
    from mihomes.db import get_session
    from mihomes.services.personal_calendar.sync import sync_account
    from mihomes.tenancy import account_context

    with _lock:
        _queued.discard(account_id)
    try:
        with account_context(account_id):
            sync_account(get_session)
    except Exception:  # noqa: BLE001 — a background worker must not die silently or loudly
        logger.exception("calendar auto-sync failed for account %s", account_id)


def install() -> None:
    """Attach the listeners once per process (idempotent)."""
    global _installed
    if _installed:
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_soft_rollback", _after_rollback)
    _installed = True
