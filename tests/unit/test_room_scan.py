"""Tests for AI room-scan asset extraction (services/ai/assessors.parse_room_scan)."""

import pytest

from mihomes.services.ai import assessors


class _StubProvider:
    def __init__(self, items):
        self._items = items
        self.calls = []

    def structured_output(self, system, user, schema, context_data=None, attachments=None):
        self.calls.append({"system": system, "attachments": attachments})
        return {"items": self._items}


def test_parse_room_scan_forwards_images_and_returns_items(session, monkeypatch):
    items = [
        {"name": "Sofa", "asset_type": "valuable", "condition": "good", "estimated_value": 1200},
        {"name": "TV", "asset_type": "appliance", "condition": "excellent"},
    ]
    stub = _StubProvider(items)
    monkeypatch.setattr(assessors, "get_ai_provider_name", lambda s: "claude")
    monkeypatch.setattr(assessors, "get_ai_api_key", lambda s, n: "key")
    monkeypatch.setattr(assessors, "get_provider", lambda n, k: stub)

    out = assessors.parse_room_scan(session, attachments=["IMG"], room_name="Living Room")

    assert out == items
    assert stub.calls[0]["attachments"] == ["IMG"]  # images reach the model
    assert "Living Room" in stub.calls[0]["system"]  # room context in the prompt


def test_parse_room_scan_requires_claude(session, monkeypatch):
    monkeypatch.setattr(assessors, "get_ai_provider_name", lambda s: "openai")
    with pytest.raises(ValueError, match="Claude"):
        assessors.parse_room_scan(session, attachments=["IMG"])
