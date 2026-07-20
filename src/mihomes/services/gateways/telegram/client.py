"""Telegram Bot API client — plain urllib, no external dependencies."""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TELEGRAM_API = "https://api.telegram.org"
MEDIA_DIR = Path(os.path.expanduser("~/.mihomes/media/telegram"))


class TelegramError(Exception):
    pass


class TelegramClient:
    """Telegram Bot API client using plain urllib — mirrors the WhatsApp client interface."""

    def __init__(self, token: str):
        self.token = token
        self._base = f"{TELEGRAM_API}/bot{token}"
        self._file_base = f"{TELEGRAM_API}/file/bot{token}"

    # -------------------------------------------------------------------------
    # Bot info
    # -------------------------------------------------------------------------

    def get_me(self) -> dict:
        """Validate token and return bot info."""
        return self._get("getMe")["result"]

    def is_connected(self) -> bool:
        """Return True if the bot token is valid and reachable."""
        try:
            self.get_me()
            return True
        except TelegramError:
            return False

    # -------------------------------------------------------------------------
    # Sending
    # -------------------------------------------------------------------------

    def send_message(self, chat_id: int | str, text: str) -> dict:
        """Send a text message to any chat (direct or group)."""
        return self._post("sendMessage", {"chat_id": int(chat_id), "text": text})

    def send_group_message(self, chat_id: int | str, text: str) -> dict:
        """Alias for send_message — keeps the same interface as the WhatsApp client."""
        return self.send_message(chat_id, text)

    # -------------------------------------------------------------------------
    # Updates / message fetching
    # -------------------------------------------------------------------------

    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch new updates via short-poll (timeout=0) or long-poll (timeout>0).

        Pass offset=last_update_id+1 to acknowledge all previously seen updates.
        Returns a list of raw Telegram Update objects.
        """
        payload: dict = {
            "limit": limit,
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._post("getUpdates", payload)
        return result.get("result", [])

    # -------------------------------------------------------------------------
    # Media
    # -------------------------------------------------------------------------

    def download_file(self, file_id: str) -> str | None:
        """Download a Telegram file to ~/.mihomes/media/telegram/.

        Returns the local path, or None on failure.
        """
        try:
            info = self._get(f"getFile?file_id={file_id}")
            remote_path = info["result"]["file_path"]
            ext = Path(remote_path).suffix or ".bin"
            MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            local_path = MEDIA_DIR / f"{file_id}{ext}"
            if local_path.exists():
                return str(local_path)
            url = f"{self._file_base}/{remote_path}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=60) as resp:
                local_path.write_bytes(resp.read())
            return str(local_path)
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Message normalization
    # -------------------------------------------------------------------------

    def normalize_update(self, update: dict, chat_links: dict) -> dict | None:
        """Convert a raw Telegram Update into the internal message dict format.

        The internal format matches the WhatsApp message dict so review.py and
        responder.py work unchanged:
            id, timestamp, jid (chat_id), isGroup, sender (user_id),
            senderName, text, hasMedia, mediaPath, propertySlug

        Returns None for non-message updates or bot's own messages.
        """
        msg = update.get("message")
        if not msg:
            return None

        # Skip system events — new member joins, member leaves, etc.
        if msg.get("new_chat_members") or msg.get("left_chat_member"):
            return None

        from_user = msg.get("from") or {}
        if from_user.get("is_bot"):
            return None

        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        user_id = str(from_user.get("id", ""))

        ts = datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc).isoformat()
        text = (msg.get("text") or msg.get("caption") or "").strip()

        # Detect and download media — largest photo, or document/video
        file_id = None
        if msg.get("photo"):
            file_id = msg["photo"][-1]["file_id"]
        elif msg.get("document"):
            file_id = msg["document"]["file_id"]
        elif msg.get("video"):
            file_id = msg["video"]["file_id"]

        has_media = file_id is not None
        media_path = self.download_file(file_id) if file_id else None

        first = from_user.get("first_name", "")
        last = from_user.get("last_name", "")
        sender_name = f"{first} {last}".strip() or from_user.get("username", "Unknown")

        return {
            "id": f"{chat_id}:{msg['message_id']}",
            "timestamp": ts,
            "jid": chat_id,          # "jid" slot — same key as WhatsApp for pipeline compat
            "isGroup": chat.get("type") in ("group", "supergroup", "channel"),
            "sender": user_id,        # Telegram user_id (no phone number available)
            "senderName": sender_name,
            "senderUsername": from_user.get("username", ""),
            "text": text,
            "hasMedia": has_media and media_path is not None,
            "mediaPath": media_path,
            "propertySlug": chat_links.get(chat_id),
        }

    # -------------------------------------------------------------------------
    # Internal HTTP helpers
    # -------------------------------------------------------------------------

    def _get(self, path: str) -> dict:
        url = f"{self._base}/{path}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise TelegramError(f"Telegram API {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise TelegramError(f"Cannot reach Telegram API: {e}") from e
        if not data.get("ok"):
            raise TelegramError(f"Telegram error: {data.get('description', data)}")
        return data

    def _post(self, method: str, payload: dict) -> dict:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self._base}/{method}",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise TelegramError(f"Telegram API {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise TelegramError(f"Cannot reach Telegram API: {e}") from e
        if not result.get("ok"):
            raise TelegramError(f"Telegram error: {result.get('description', result)}")
        return result
