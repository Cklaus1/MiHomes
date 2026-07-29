"""Shared gateway dedup store + poll lease.

Spec M22/M23. Before this module each gateway had FOUR disjoint processed-id
stores — the monitor used a DB config key, the extractor used a sidecar JSON
file — so an id handled by one poller was invisible to the other and messages
were double-processed into duplicate issues/tasks. And every store pruned via
``list(set)[-N:]``: a set has arbitrary iteration order, so pruning could evict
the *newest* ids and keep stale ones.

`ProcessedIdStore` is the single source of truth per gateway: one DB-backed,
insertion-ordered list keyed by a gateway string, shared by the monitor and the
extractor. `poll_lease` is an advisory, TTL'd lock so a monitor and an extractor
polling the same gateway concurrently can't both advance the offset and skip
each other's updates (the M22 "409 race").
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from mihomes.db import get_session
from mihomes.services.config_service import get_config, set_config

logger = logging.getLogger(__name__)

DEFAULT_CAP = 5000


class ProcessedIdStore:
    """Insertion-ordered, DB-backed set of already-processed message ids.

    Ordering is preserved so pruning drops the OLDEST ids (from the front),
    never the most recent. Backed by a single config key so the monitor and the
    extractor for a gateway share one store.
    """

    def __init__(self, key: str, cap: int = DEFAULT_CAP):
        self.key = key
        self.cap = cap

    def _read(self) -> list[str]:
        with get_session() as s:
            stored = get_config(s, self.key)
        if not stored:
            return []
        try:
            data = json.loads(stored)
        except (json.JSONDecodeError, TypeError):
            return []
        # Tolerate legacy payloads that were stored as a JSON array of a set.
        return [str(x) for x in data] if isinstance(data, list) else []

    def _write(self, ids: list[str]) -> None:
        with get_session() as s:
            set_config(s, self.key, json.dumps(ids))

    def load(self) -> list[str]:
        """Return processed ids in insertion order (oldest first)."""
        return self._read()

    def contains(self, msg_id: str) -> bool:
        return msg_id in set(self._read())

    def add(self, msg_ids) -> None:
        """Append new ids (dedup-preserving order), then prune from the front."""
        current = self._read()
        # dict.fromkeys keeps first-seen insertion order and drops duplicates.
        merged = list(dict.fromkeys([*current, *(str(m) for m in msg_ids if m)]))
        if len(merged) > self.cap:
            merged = merged[-self.cap :]  # keep the newest `cap`, drop oldest
        self._write(merged)


class PoisonGuard:
    """Per-id attempt counter that quarantines deterministically-crashing ids.

    Spec M21. The monitor marks each id's attempt BEFORE processing; a crash
    therefore leaves the counter incremented, so the id is retried on restart
    (no message loss). Once an id reaches ``max_attempts`` it is quarantined and
    the caller may advance the offset past it (no poison hot-loop). A
    successfully processed id is ``clear``ed so its counter never leaks into a
    future reuse of the same id.
    """

    def __init__(self, gateway: str, max_attempts: int = 3):
        self.key = f"gateway.{gateway}.attempts"
        self.max_attempts = max_attempts

    def _read(self) -> dict[str, int]:
        with get_session() as s:
            stored = get_config(s, self.key)
        if not stored:
            return {}
        try:
            data = json.loads(stored)
        except (json.JSONDecodeError, TypeError):
            return {}
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _write(self, counts: dict[str, int]) -> None:
        with get_session() as s:
            set_config(s, self.key, json.dumps(counts))

    def mark_attempt(self, msg_ids) -> None:
        """Increment the attempt counter for each id (call BEFORE processing)."""
        counts = self._read()
        for m in msg_ids:
            if m:
                counts[str(m)] = counts.get(str(m), 0) + 1
        self._write(counts)

    def is_quarantined(self, msg_id: str) -> bool:
        return self._read().get(str(msg_id), 0) >= self.max_attempts

    def clear(self, msg_ids) -> None:
        """Drop the attempt counters for successfully processed ids."""
        counts = self._read()
        changed = False
        for m in msg_ids:
            if str(m) in counts:
                del counts[str(m)]
                changed = True
        if changed:
            self._write(counts)

    def partition(self, msg_ids) -> tuple[list[str], list[str]]:
        """Mark an attempt for each id, then split into (live, quarantined).

        ``live`` should be processed this cycle; ``quarantined`` has exceeded the
        retry cap and should be skipped (offset advanced past it).
        """
        ids = [str(m) for m in msg_ids if m]
        self.mark_attempt(ids)
        counts = self._read()
        live, poison = [], []
        for m in ids:
            (poison if counts.get(m, 0) >= self.max_attempts else live).append(m)
        return live, poison


@contextmanager
def poll_lease(gateway: str, ttl_seconds: int = 90):
    """Advisory single-poller lease for a gateway, backed by a config key.

    Yields True if this caller acquired the lease, False if another poller holds
    a still-fresh lease. A lease older than ``ttl_seconds`` is considered stale
    (the holder crashed) and can be taken over. Released on context exit only if
    we still own it, so a slow poller never clears a newer holder's lease.
    """
    key = f"gateway.{gateway}.poll_lease"
    now = datetime.now(timezone.utc)
    token = now.isoformat()
    acquired = False
    try:
        with get_session() as s:
            stored = get_config(s, key)
            fresh = False
            if stored:
                try:
                    held = datetime.fromisoformat(stored)
                    fresh = (now - held).total_seconds() < ttl_seconds
                except ValueError:
                    fresh = False
            if not fresh:
                set_config(s, key, token)
                acquired = True
        yield acquired
    finally:
        if acquired:
            try:
                with get_session() as s:
                    if get_config(s, key) == token:
                        set_config(s, key, "")
            except Exception:
                logger.exception("poll_lease: release failed for %s", gateway)
