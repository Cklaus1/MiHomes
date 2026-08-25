"""Tests for AI room-scan asset extraction (services/ai/assessors.parse_room_scan)."""

import pytest

from mihomes.services.ai import assessors
from mihomes.services.ai.provider import AIProviderError, get_provider


class _StubProvider:
    # H13: an image-capable provider advertises the capability flag.
    supports_images = True

    def __init__(self, items):
        self._items = items
        self.calls = []

    def structured_output(self, system, user, schema, context_data=None, attachments=None):
        self.calls.append({"system": system, "attachments": attachments})
        return {"items": self._items}


class _BlindProvider(_StubProvider):
    """A provider that cannot forward images to the model."""
    supports_images = False


def test_parse_room_scan_forwards_images_and_returns_items(session, monkeypatch):
    items = [
        {"name": "Sofa", "asset_type": "valuable", "condition": "good", "estimated_value": 1200},
        {"name": "TV", "asset_type": "appliance", "condition": "excellent"},
    ]
    stub = _StubProvider(items)
    monkeypatch.setattr(assessors, "get_ai_provider_name", lambda s: "claude")
    monkeypatch.setattr(assessors, "get_ai_api_key", lambda s, n: "key")
    monkeypatch.setattr(assessors, "get_ai_model", lambda s, n: "claude-sonnet-4-20250514")
    monkeypatch.setattr(assessors, "get_provider", lambda *a, **k: stub)

    out = assessors.parse_room_scan(session, attachments=["IMG"], room_name="Living Room")

    assert out == items
    assert stub.calls[0]["attachments"] == ["IMG"]  # images reach the model
    assert "Living Room" in stub.calls[0]["system"]  # room context in the prompt


def test_parse_room_scan_works_with_any_provider(session, monkeypatch):
    """Scan is no longer Claude-only — any vision-capable provider should work."""
    items = [{"name": "Desk", "asset_type": "equipment", "condition": "good"}]
    stub = _StubProvider(items)
    monkeypatch.setattr(assessors, "get_ai_provider_name", lambda s: "nim")
    monkeypatch.setattr(assessors, "get_ai_api_key", lambda s, n: "key")
    monkeypatch.setattr(assessors, "get_ai_model", lambda s, n: "meta/llama-3.2-90b-vision-instruct")
    monkeypatch.setattr(assessors, "get_provider", lambda *a, **k: stub)

    out = assessors.parse_room_scan(session, attachments=["IMG"])
    assert out == items


def test_parse_room_scan_raises_on_blind_provider(session, monkeypatch):
    """H13 — a provider that can't send images must raise, not silently
    hallucinate an inventory from photos the model never saw."""
    stub = _BlindProvider([{"name": "Ghost Chair", "asset_type": "equipment"}])
    monkeypatch.setattr(assessors, "get_ai_provider_name", lambda s: "openai")
    monkeypatch.setattr(assessors, "get_ai_api_key", lambda s, n: "key")
    monkeypatch.setattr(assessors, "get_ai_model", lambda s, n: "gpt-4o")
    monkeypatch.setattr(assessors, "get_provider", lambda *a, **k: stub)

    with pytest.raises(AIProviderError):
        assessors.parse_room_scan(session, attachments=["IMG"])
    assert stub.calls == []  # never called the model


def test_image_capable_providers_advertise_flag():
    """Claude and NIM forward real images; OpenAI and Ollama flatten to text."""
    assert get_provider("claude", api_key="k").supports_images is True
    assert get_provider("nim", api_key="nvapi-k").supports_images is True
    assert get_provider("openai", api_key="k").supports_images is False
    assert get_provider("ollama").supports_images is False
