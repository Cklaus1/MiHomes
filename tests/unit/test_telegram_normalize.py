"""One Telegram normalizer for both transports.

The poller and the webhook used to normalize separately and drifted: the webhook processed member
joins and bot messages the poller skips, keyed dedup on `update_id` where the poller used
`chat:message_id` (so the shared processed-id store could never match across transports), and
emitted an epoch int where the poller emitted an ISO string.
"""

from __future__ import annotations

import json

import pytest

from mihomes.services.gateways.telegram.client import TelegramClient, normalize_message
from mihomes.services.gateways.webhook import normalize_telegram_update


def _update(**message_overrides) -> dict:
    message = {
        "message_id": 42,
        "from": {"id": 7001, "first_name": "Ana", "last_name": "Diaz", "username": "ana", "is_bot": False},
        "chat": {"id": -100555, "type": "supergroup"},
        "date": 1756500000,
        "text": "  The pool pump is leaking  ",
    }
    message.update(message_overrides)
    return {"update_id": 900, "message": message}


def test_webhook_and_poller_produce_the_same_envelope():
    update = _update()
    from_webhook = normalize_telegram_update(json.dumps(update).encode())
    from_poller = TelegramClient("t").normalize_update(update, chat_links={})
    assert from_webhook == from_poller
    assert from_webhook == {
        "id": "-100555:42",
        "timestamp": "2025-08-29T20:40:00+00:00",
        "jid": "-100555",
        "isGroup": True,
        "sender": "7001",
        "senderName": "Ana Diaz",
        "senderUsername": "ana",
        "text": "The pool pump is leaking",
        "hasMedia": False,
        "mediaPath": None,
        "propertySlug": None,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"new_chat_members": [{"id": 1, "first_name": "New"}], "text": None},
        {"left_chat_member": {"id": 1, "first_name": "Gone"}, "text": None},
        {"from": {"id": 99, "first_name": "OtherBot", "is_bot": True}},
    ],
    ids=["member-joined", "member-left", "another-bot"],
)
def test_webhook_skips_what_the_poller_skips(overrides):
    update = _update(**overrides)
    assert normalize_telegram_update(json.dumps(update).encode()) is None
    assert TelegramClient("t").normalize_update(update, chat_links={}) is None


def test_redelivery_repeats_the_id_and_a_new_update_id_does_not_change_it():
    """Dedup keys on the message, not on the update wrapper."""
    first = normalize_message(_update())
    again = normalize_message({**_update(), "update_id": 901})
    assert first["id"] == again["id"] == "-100555:42"


def test_poller_fills_property_from_chat_links():
    msg = TelegramClient("t").normalize_update(_update(), chat_links={"-100555": "belle-estate"})
    assert msg["propertySlug"] == "belle-estate"


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"edited_message": {}}'])
def test_unusable_bodies_are_none(body):
    assert normalize_telegram_update(body) is None
