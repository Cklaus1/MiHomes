"""In-process chat pub/sub (SPEC-012 D16, D17).

Each stream subscribes with its own event loop and a bounded queue. `publish` is safe from any
thread — the after-commit hook runs in request worker threads and in background tasks — because it
hands each event to the subscriber's own loop with `call_soon_threadsafe`. With no subscribers it is
a no-op, so committing chat rows never needs a running loop.

The bus is per-process. With several processes, `REALTIME_DATABASE_URL` enables LISTEN/NOTIFY
(`start_listener`), whose task re-publishes each committed event on this process's bus.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

QUEUE_MAX = 256


@dataclass(eq=False)
class Subscription:
    account_id: uuid.UUID
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX))
    overflowed: bool = False


def _offer(sub: Subscription, event: dict) -> None:
    """Runs on the subscriber's loop. On overflow: one `resync`, then drop until drained."""
    if sub.overflowed:
        return
    try:
        sub.queue.put_nowait(event)
    except asyncio.QueueFull:
        sub.overflowed = True
        try:
            sub.queue.get_nowait()  # make room for the resync marker
        except asyncio.QueueEmpty:  # pragma: no cover
            pass
        sub.queue.put_nowait({"type": "resync"})


class ChatBus:
    def __init__(self) -> None:
        self._subs: set[Subscription] = set()
        self._lock = threading.Lock()

    def subscribe(self, account_id: uuid.UUID) -> Subscription:
        """Call on the stream's event loop; the loop is captured for thread-safe delivery."""
        sub = Subscription(account_id=account_id, loop=asyncio.get_running_loop())
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subs.discard(sub)

    def publish(self, account_id, event: dict) -> None:
        """Any thread. Delivers to every subscriber of `account_id` on its own loop."""
        account_id = uuid.UUID(str(account_id))
        with self._lock:
            targets = [s for s in self._subs if s.account_id == account_id]
        for sub in targets:
            try:
                sub.loop.call_soon_threadsafe(_offer, sub, event)
            except RuntimeError:  # loop closed: the stream is gone
                self.unsubscribe(sub)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


BUS = ChatBus()


async def start_listener(dsn: str, bus: ChatBus = BUS) -> None:
    """D17: LISTEN on the chat channel and re-publish locally. Reconnects with backoff (1→30 s).

    Needs a **direct** Postgres connection: LISTEN does not survive PgBouncer's transaction mode.
    """
    import psycopg

    from mihomes.services.chat.events import NOTIFY_CHANNEL

    delay = 1.0
    while True:
        try:
            conn = await psycopg.AsyncConnection.connect(dsn.replace("postgresql+psycopg://",
                                                                     "postgresql://"),
                                                         autocommit=True)
            async with conn:
                await conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
                delay = 1.0
                async for note in conn.notifies():
                    try:
                        data = json.loads(note.payload)
                        account = data.pop("a")
                        bus.publish(account, data)
                    except Exception:
                        logger.exception("chat listener: bad notification")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("chat listener: connection failed; retrying in %.0fs", delay,
                           exc_info=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
