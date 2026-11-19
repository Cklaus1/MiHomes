"""Webhook delivery service for G3: webhook delivery with async queue, retry, and dead-letter."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class WebhookStatus(str, Enum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


@dataclass
class WebhookEvent:
    """A webhook event queued for delivery."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = ""
    event_type: str = ""
    url: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    secret: str = ""
    status: WebhookStatus = WebhookStatus.PENDING
    attempts: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    delivered_at: Optional[float] = None
    last_error: Optional[str] = None
    signature: str = ""

    def is_exhausted(self) -> bool:
        return self.attempts >= self.max_retries


# ---------------------------------------------------------------------------
# Delivery service
# ---------------------------------------------------------------------------

DeliveryFn = Callable[[WebhookEvent], Awaitable[httpx.Response]]


class WebhookDeliveryService:
    """Async webhook delivery with retry, backoff, and dead-letter queue.

    Uses an in-memory asyncio.Queue as the delivery queue.  A background
    worker coroutine drains the queue, applies exponential-backoff retry,
    and moves permanently-failed events to the dead-letter store.
    """

    def __init__(
        self,
        delivery_fn: Optional[DeliveryFn] = None,
        base_backoff: float = 1.0,
        max_backoff: float = 60.0,
        queue_size: int = 10_000,
    ) -> None:
        self._delivery_fn = delivery_fn or self._default_delivery
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._queue: asyncio.Queue[WebhookEvent] = asyncio.Queue(maxsize=queue_size)
        self._dead_letter: List[WebhookEvent] = []
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._event_store: Dict[str, WebhookEvent] = {}

    # -- public queue -------------------------------------------------------

    async def enqueue(self, event: WebhookEvent) -> str:
        """Queue an event for delivery. Returns event id."""
        if self._queue.full():
            raise ValueError("store full")
        self._event_store[event.id] = event
        await self._queue.put(event)
        logger.info("webhook queued %s for tenant %s", event.id, event.tenant_id)
        return event.id

    @property
    def dead_letter(self) -> List[WebhookEvent]:
        return list(self._dead_letter)

    def get_event(self, event_id: str) -> Optional[WebhookEvent]:
        return self._event_store.get(event_id)

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("webhook delivery service started")

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("webhook delivery service stopped")

    # -- internal -----------------------------------------------------------

    async def _worker(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            await self._deliver_with_retry(event)

    async def _deliver_with_retry(self, event: WebhookEvent) -> None:
        event.status = WebhookStatus.DELIVERING
        self._event_store[event.id] = event

        while not event.is_exhausted() and event.status != WebhookStatus.DEAD_LETTER:
            event.attempts += 1
            try:
                response = await self._delivery_fn(event)
                if 200 <= response.status_code < 300:
                    event.status = WebhookStatus.DELIVERED
                    event.delivered_at = time.time()
                    self._event_store[event.id] = event
                    logger.info(
                        "webhook delivered %s after %d attempt(s)",
                        event.id,
                        event.attempts,
                    )
                    return
                else:
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
            except Exception as exc:
                event.last_error = str(exc)
                logger.warning(
                    "webhook attempt %d failed for %s: %s",
                    event.attempts,
                    event.id,
                    exc,
                )
                if event.is_exhausted():
                    event.status = WebhookStatus.DEAD_LETTER
                    self._dead_letter.append(event)
                    self._event_store[event.id] = event
                    logger.error("webhook dead-lettered %s after %d attempts", event.id, event.attempts)
                    return
                # Exponential backoff
                backoff = min(
                    self._base_backoff * (2 ** (event.attempts - 1)),
                    self._max_backoff,
                )
                await asyncio.sleep(backoff)

    async def _default_delivery(self, event: WebhookEvent) -> httpx.Response:
        """Default HTTP POST delivery using httpx async client."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Content-Type": "application/json"}
            if event.secret:
                import hashlib
                import hmac

                raw = f"{event.id}.{event.payload}"
                sig = hmac.new(
                    event.secret.encode(),
                    raw.encode(),
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={sig}"
            return await client.post(event.url, json=event.payload, headers=headers)