"""WhatsApp bridge client — Python HTTP client for the Node.js Baileys bridge."""

import json
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime


class WhatsAppBridgeError(Exception):
    pass


def drain_messages(fetch, since=None, limit: int = 100, max_pages: int = 100) -> list[dict]:
    """Page a bridge message feed forward until fully drained (spec M30).

    ``fetch(since, limit)`` must return up to ``limit`` messages with
    ``timestamp >= since``, OLDEST FIRST. A single fetch caps at ``limit`` rows,
    so a burst larger than one page would silently drop the oldest messages if
    the caller then advanced its cursor to "now". This pages forward from the
    last message's timestamp, de-duplicating the boundary message by id, until a
    short page signals the backlog is exhausted.

    ``max_pages`` bounds the loop so a pathological feed (more identical-timestamp
    messages than a page) can't hang the monitor.
    """
    drained: list[dict] = []
    seen: set = set()
    cursor = since
    for _ in range(max_pages):
        page = fetch(cursor, limit)
        if not page:
            break
        added_any = False
        for m in page:
            mid = m.get("id")
            if mid is not None and mid in seen:
                continue
            if mid is not None:
                seen.add(mid)
            drained.append(m)
            added_any = True
        if len(page) < limit:
            break  # short page → backlog exhausted
        # Advance cursor to the newest timestamp in this full page.
        last_ts = page[-1].get("timestamp")
        next_cursor = _parse_ts(last_ts)
        if next_cursor is None or next_cursor == cursor or not added_any:
            break  # cannot advance (all same timestamp / unparseable) → avoid hot-loop
        cursor = next_cursor
    return drained


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


class WhatsAppClient:
    """HTTP client for the MiHomes WhatsApp Bridge (Node.js/Baileys)."""

    def __init__(self, base_url: str = "http://localhost:7867"):
        self.base_url = base_url.rstrip("/")

    def get_status(self) -> dict:
        """Get bridge connection status."""
        return self._get("/status")

    def get_qr(self) -> dict:
        """Get QR code for WhatsApp pairing."""
        return self._get("/qr")

    def send_message(self, phone: str, text: str, media_path: str | None = None) -> dict:
        """Send a message to a phone number."""
        return self._post("/send", {"phone": phone, "text": text, "mediaPath": media_path})

    def send_group_message(self, group_jid: str, text: str) -> dict:
        """Send a message to a group."""
        return self._post("/send-group", {"groupJid": group_jid, "text": text})

    def get_messages(
        self,
        since: datetime | None = None,
        group_jid: str | None = None,
        limit: int = 100,
        order: str | None = None,
    ) -> list[dict]:
        """Fetch messages from the bridge.

        ``order='asc'`` returns the oldest ``limit`` messages at/after ``since``
        (forward paging); the default returns the newest window.
        """
        params: dict = {"limit": limit}
        if since:
            # Format as UTC Z-suffix to avoid + encoding issues in query strings
            params["since"] = since.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        if group_jid:
            params["groupJid"] = group_jid
        if order:
            params["order"] = order
        query = urllib.parse.urlencode(params)
        result = self._get(f"/messages?{query}")
        return result.get("messages", [])

    def drain_messages(
        self, since: datetime | None = None, group_jid: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Drain the full backlog since ``since`` by paging forward (spec M30)."""
        return drain_messages(
            lambda cur, lim: self.get_messages(
                since=cur, group_jid=group_jid, limit=lim, order="asc"
            ),
            since=since,
            limit=limit,
        )

    def get_groups(self) -> list[dict]:
        """List all WhatsApp groups."""
        result = self._get("/groups")
        return result.get("groups", [])

    def link_group(self, group_jid: str, property_slug: str) -> dict:
        """Link a WhatsApp group to a property."""
        return self._post("/link-group", {"groupJid": group_jid, "propertySlug": property_slug})

    def unlink_group(self, group_jid: str) -> dict:
        """Unlink a WhatsApp group from its property."""
        return self._post("/unlink-group", {"groupJid": group_jid})

    def clear_messages(self) -> dict:
        """Clear all messages from the buffer."""
        req = urllib.request.Request(
            f"{self.base_url}/messages",
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            raise WhatsAppBridgeError(f"Failed to clear messages: {e}")

    def is_connected(self) -> bool:
        """Check if bridge is running and connected."""
        try:
            status = self.get_status()
            return status.get("status") == "connected"
        except WhatsAppBridgeError:
            return False

    def _get(self, path: str) -> dict:
        try:
            req = urllib.request.Request(f"{self.base_url}{path}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise WhatsAppBridgeError(
                f"Cannot connect to WhatsApp bridge at {self.base_url}. "
                f"Is the bridge running? Start with: cd bridge && npm start — Error: {e}"
            )

    def _post(self, path: str, data: dict) -> dict:
        try:
            payload = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}{path}",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise WhatsAppBridgeError(f"Bridge request failed: {e}")
