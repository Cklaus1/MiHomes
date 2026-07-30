"""Telegram client hardening — getUpdates limit clamp (H28) and lazy media (L15).

H28: Bot API caps `getUpdates` limit at 100; callers pass 200 (review) and 500
(extractor), so every default run 400s. The clamp belongs in the client so all
callers are covered at one point.

L15: `normalize_update` downloaded media for EVERY update — including messages
from unlinked chats that are filtered out downstream — filling MEDIA_DIR and
slowing the poll. Media must only be fetched once the chat is known-linked.
"""

from mihomes.services.gateways.telegram.client import TelegramClient


def _client():
    return TelegramClient(token="TEST:TOKEN")


# --------------------------------------------------------------------------- H28


def test_get_updates_clamps_limit_to_100(monkeypatch):
    sent = {}

    def fake_post(method, payload):
        sent["method"] = method
        sent["payload"] = payload
        return {"ok": True, "result": []}

    client = _client()
    monkeypatch.setattr(client, "_post", fake_post)

    client.get_updates(offset=5, limit=500)

    assert sent["method"] == "getUpdates"
    assert sent["payload"]["limit"] == 100, "limit>100 must be clamped to the Bot API max"


def test_get_updates_preserves_limit_under_cap(monkeypatch):
    sent = {}
    client = _client()
    monkeypatch.setattr(client, "_post", lambda m, p: sent.update(p) or {"ok": True, "result": []})

    client.get_updates(limit=50)  # monitor's limit — must be untouched

    assert sent["limit"] == 50


# --------------------------------------------------------------------------- L15


def _photo_update(chat_id: str):
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": "group"},
            "from": {"id": 42, "first_name": "Ana"},
            "caption": "leak under sink",
            "photo": [{"file_id": "SMALL"}, {"file_id": "BIG"}],
        },
    }


def test_normalize_update_skips_media_download_for_unlinked_chat(monkeypatch):
    client = _client()
    calls = []
    monkeypatch.setattr(client, "download_file", lambda fid: calls.append(fid) or "/tmp/x.jpg")

    # chat_links is empty → this chat is not linked to any property
    msg = client.normalize_update(_photo_update("-100999"), chat_links={})

    assert msg is not None
    assert calls == [], "must not download media for an unlinked chat"
    assert msg["hasMedia"] is False
    assert msg["mediaPath"] is None


def test_normalize_update_downloads_media_for_linked_chat(monkeypatch):
    client = _client()
    calls = []
    monkeypatch.setattr(client, "download_file", lambda fid: calls.append(fid) or "/tmp/x.jpg")

    msg = client.normalize_update(
        _photo_update("-100999"), chat_links={"-100999": "beach-house"}
    )

    assert calls == ["BIG"], "largest photo downloaded once for a linked chat"
    assert msg["propertySlug"] == "beach-house"
    assert msg["hasMedia"] is True
    assert msg["mediaPath"] == "/tmp/x.jpg"
