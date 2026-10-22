"""Telegram Bot API client — plain urllib, no external dependencies."""

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MEDIA_DIR = Path(os.path.expanduser("~/.mihomes/media/telegram"))


def media_file_id(msg: dict) -> str | None:
    """The file_id of a message's media — the largest photo, else a document or video."""
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"]
    for kind in ("document", "video"):
        if msg.get(kind):
            return msg[kind]["file_id"]
    return None


def normalize_message(update: dict) -> dict | None:
    """One Telegram update → the internal message envelope, or None. **Both transports call this.**

    The poller (`TelegramClient.normalize_update`) and the webhook (`normalize_telegram_update`)
    used to normalize separately and had drifted:

    * the webhook kept member joins/leaves and other bots' messages, which the poller skips —
      so on webhooks a new member joining a group was sent to the AI;
    * the webhook's `id` was `update_id` and the poller's `chat:message_id`, so the shared
      processed-id store (A17) could never match one message across the two transports;
    * the webhook's `timestamp` was a raw epoch int and the poller's an ISO string.

    `id` is `"<chat_id>:<message_id>"`, the form already in live `telegram.processed_ids` stores
    (message_id is unique per chat and repeats on redelivery). `timestamp` is ISO-8601 UTC.
    `propertySlug` and `mediaPath` are left None: the chat→property map is tenant config, read
    only once an account is bound, and media needs an authenticated fetch.
    """
    msg = update.get("message") if isinstance(update, dict) else None
    if not isinstance(msg, dict):
        return None

    # Skip system events — new member joins, member leaves, etc.
    if msg.get("new_chat_members") or msg.get("left_chat_member"):
        return None

    from_user = msg.get("from") or {}
    if from_user.get("is_bot"):
        return None

    chat = msg.get("chat") or {}
    if chat.get("id") is None or msg.get("message_id") is None:
        return None
    chat_id = str(chat["id"])

    first = from_user.get("first_name", "")
    last = from_user.get("last_name", "")
    sender_name = f"{first} {last}".strip() or from_user.get("username", "Unknown")

    return {
        "id": f"{chat_id}:{msg['message_id']}",
        "timestamp": datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc).isoformat(),
        "jid": chat_id,  # "jid" slot — WhatsApp's vocabulary, kept so the envelope is one shape
        "isGroup": chat.get("type") in ("group", "supergroup", "channel"),
        "sender": str(from_user.get("id", "")),  # Telegram user_id (no phone number available)
        "senderName": sender_name,
        "senderUsername": from_user.get("username", ""),
        "text": (msg.get("text") or msg.get("caption") or "").strip(),
        "hasMedia": media_file_id(msg) is not None,
        "mediaPath": None,
        "propertySlug": None,
    }


def platform_bot_token() -> str:
    """The deployment's bot token, from `TELEGRAM_BOT_TOKEN`, or "" when unset.

    Read from the environment for the same reason `TELEGRAM_WEBHOOK_SECRET` is: the webhook's
    unlinked-sender reply runs before any account is bound, so the tenant-owned
    `telegram.bot_token` config row is unreadable there. One webhook URL means one bot, so this
    is also the token every webhook-path reply should come from (per-account bots are U6).
    """
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


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
            # Bot API rejects limit>100 with HTTP 400. Clamp here so every
            # caller (review=200, extractor=500) is covered at one point.
            "limit": max(1, min(limit, 100)),
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
            logger.exception("download_file: suppressed exception")
            return

    # -------------------------------------------------------------------------
    # Message normalization
    # -------------------------------------------------------------------------

    def normalize_update(self, update: dict, chat_links: dict) -> dict | None:
        """The poller's normalize: `normalize_message`, then the chat's property and its media.

        Media is fetched here rather than in `normalize_message` because it needs this client's
        token and an API call. L15: only for chats linked to a property — fetching media for
        unlinked chats fills MEDIA_DIR and slows the poll.
        """
        message = normalize_message(update)
        if message is None:
            return None

        message["propertySlug"] = chat_links.get(message["jid"])
        file_id = media_file_id(update["message"])
        media_path = (
            self.download_file(file_id) if file_id and message["propertySlug"] else None
        )
        message["mediaPath"] = media_path
        # The poller has always reported media only once it is on disk to look at.
        message["hasMedia"] = media_path is not None
        return message

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
