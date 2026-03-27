"""WhatsApp bridge client — Python HTTP client for the Node.js Baileys bridge."""

import json
import urllib.request
import urllib.error
from datetime import datetime


class WhatsAppBridgeError(Exception):
    pass


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
        self, since: datetime | None = None, group_jid: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Fetch messages from the bridge."""
        params = []
        if since:
            params.append(f"since={since.isoformat()}")
        if group_jid:
            params.append(f"groupJid={group_jid}")
        params.append(f"limit={limit}")
        query = "&".join(params)
        result = self._get(f"/messages?{query}")
        return result.get("messages", [])

    def get_groups(self) -> list[dict]:
        """List all WhatsApp groups."""
        result = self._get("/groups")
        return result.get("groups", [])

    def link_group(self, group_jid: str, property_slug: str) -> dict:
        """Link a WhatsApp group to a property."""
        return self._post("/link-group", {"groupJid": group_jid, "propertySlug": property_slug})

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
