"""Home Assistant WebSocket client.

Handles connect → auth → subscribe flow and yields state_changed events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class StateChangedEvent:
    entity_id: str
    old_state: str | None
    new_state: str
    attributes: dict[str, Any]
    context: dict[str, Any]


class HAConnectionError(Exception):
    pass


class HAAuthError(Exception):
    pass


async def connect_and_subscribe(
    ws_url: str,
    token: str,
    *,
    reconnect_delay: float = 10.0,
) -> AsyncGenerator[StateChangedEvent, None]:
    """Async generator: yields StateChangedEvent objects indefinitely.

    Automatically reconnects on disconnect with exponential back-off up to 5 min.
    """
    import websockets

    delay = reconnect_delay
    while True:
        try:
            async for event in _session(ws_url, token):
                delay = reconnect_delay  # reset on success
                yield event
        except HAAuthError:
            raise  # auth errors should not retry
        except (OSError, websockets.ConnectionClosed, asyncio.TimeoutError) as exc:
            log.warning("HA WebSocket disconnected (%s). Reconnecting in %.0fs…", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
        except Exception as exc:  # noqa: BLE001
            log.error("HA bridge unexpected error: %s", exc, exc_info=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


async def _session(
    ws_url: str,
    token: str,
) -> AsyncGenerator[StateChangedEvent, None]:
    import websockets

    log.info("Connecting to Home Assistant at %s", ws_url)
    async with websockets.connect(ws_url, open_timeout=15, ping_interval=30) as ws:
        # Step 1: receive auth_required
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if msg.get("type") != "auth_required":
            raise HAConnectionError(f"Expected auth_required, got: {msg}")

        # Step 2: authenticate
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if msg.get("type") == "auth_invalid":
            raise HAAuthError(f"Authentication failed: {msg.get('message')}")
        if msg.get("type") != "auth_ok":
            raise HAConnectionError(f"Expected auth_ok, got: {msg}")

        log.info("HA authentication successful (HA version: %s)", msg.get("ha_version", "?"))

        # Step 3: subscribe to state_changed
        await ws.send(json.dumps({
            "id": 1,
            "type": "subscribe_events",
            "event_type": "state_changed",
        }))
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if not (msg.get("type") == "result" and msg.get("success")):
            raise HAConnectionError(f"Subscription failed: {msg}")

        log.info("Subscribed to state_changed events")

        # Step 4: consume events
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") != "event":
                continue

            event_data = msg.get("event", {}).get("data", {})
            new_state_obj = event_data.get("new_state") or {}
            old_state_obj = event_data.get("old_state") or {}

            entity_id = new_state_obj.get("entity_id", "")
            new_state = new_state_obj.get("state", "")
            old_state = old_state_obj.get("state")
            attributes = new_state_obj.get("attributes", {})
            context = new_state_obj.get("context", {})

            if not entity_id or new_state == old_state:
                continue  # skip no-change events

            yield StateChangedEvent(
                entity_id=entity_id,
                old_state=old_state,
                new_state=new_state,
                attributes=attributes,
                context=context,
            )


async def test_connection(ws_url: str, token: str) -> dict[str, Any]:
    """One-shot connection test. Returns status dict."""
    import websockets

    try:
        async with websockets.connect(ws_url, open_timeout=10) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if msg.get("type") != "auth_required":
                return {"ok": False, "error": f"Unexpected message: {msg}"}

            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))

            if msg.get("type") == "auth_invalid":
                return {"ok": False, "error": "Invalid access token"}
            if msg.get("type") == "auth_ok":
                return {"ok": True, "ha_version": msg.get("ha_version", "unknown")}

            return {"ok": False, "error": f"Unexpected auth response: {msg}"}
    except HAAuthError as exc:
        return {"ok": False, "error": str(exc)}
    except (OSError, asyncio.TimeoutError) as exc:
        return {"ok": False, "error": f"Connection failed: {exc}"}
