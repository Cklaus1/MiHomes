"""Regression tests for provider content handling (spec M34 / M35 / M37).

M34 — Situation Report demanded 17 sections at max_tokens=4096 with no
      stop_reason check, so a truncated report was returned as if complete.
      Fix: raise the output budget and surface truncation with a visible marker.
M35 — claude_provider.complete read `response.content[0].text` unguarded (a
      refusal or a leading thinking block has no `.text` / is not the answer);
      structured_output silently dropped text/PDF attachments; NIM read a
      nonexistent `content_type` attribute (Attachment has `media_type`) so
      every image was mislabelled image/jpeg, and text attachments were dropped.
M37 — file_processor applied no size cap, so a 200 MB log became a full text
      attachment. Fix: cap and truncate with a marker.

These tests use fake SDK clients, so no network call and no real key is needed.
"""

from types import SimpleNamespace

import pytest

from mihomes.services.ai.file_processor import (
    MAX_IMAGE_BYTES,
    MAX_TEXT_CHARS,
    Attachment,
    process_upload,
)
from mihomes.services.ai.provider import (
    MAX_OUTPUT_TOKENS,
    TRUNCATION_MARKER,
    AIProviderError,
)


# ── fake Anthropic client ───────────────────────────────────────────────────

class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.captured: dict | None = None

    def create(self, **kwargs):
        self.captured = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _thinking_block(thinking):
    # A thinking/redacted block has no `.text` attribute — content[0].text blows up.
    return SimpleNamespace(type="thinking", thinking=thinking)


def _tool_block(payload):
    return SimpleNamespace(type="tool_use", input=payload)


def _make_claude(response):
    from mihomes.services.ai.claude_provider import ClaudeProvider
    provider = ClaudeProvider(api_key="test-key")
    provider.client = _FakeClient(response)
    return provider


# ── M35a: content[0] guard ──────────────────────────────────────────────────

def test_complete_skips_leading_thinking_block():
    """A leading non-text block must not crash or be returned as the answer."""
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[_thinking_block("hmm..."), _text_block("the real answer")],
    )
    provider = _make_claude(response)
    assert provider.complete("sys", "hi") == "the real answer"


def test_complete_raises_on_no_text_content():
    """A response with no text block (e.g. a bare refusal) must raise, not IndexError."""
    response = SimpleNamespace(stop_reason="end_turn", content=[_thinking_block("...")])
    provider = _make_claude(response)
    with pytest.raises(AIProviderError):
        provider.complete("sys", "hi")


# ── M34: truncation surfaced + budget raised ────────────────────────────────

def test_complete_surfaces_truncation():
    response = SimpleNamespace(stop_reason="max_tokens", content=[_text_block("partial report")])
    provider = _make_claude(response)
    out = provider.complete("sys", "long report please")
    assert "partial report" in out
    assert TRUNCATION_MARKER.strip() in out


def test_complete_uses_raised_token_budget():
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("ok")])
    provider = _make_claude(response)
    provider.complete("sys", "hi")
    assert provider.client.messages.captured["max_tokens"] == MAX_OUTPUT_TOKENS
    assert MAX_OUTPUT_TOKENS > 4096


def test_complete_no_marker_when_complete():
    response = SimpleNamespace(stop_reason="end_turn", content=[_text_block("done")])
    provider = _make_claude(response)
    assert provider.complete("sys", "hi") == "done"


# ── M35b: structured_output forwards text/PDF attachments ────────────────────

def test_structured_output_forwards_text_attachments():
    response = SimpleNamespace(content=[_tool_block({"ok": True})])
    provider = _make_claude(response)
    att = Attachment(filename="quote.pdf", is_image=False, text_content="LINE ITEM: $500")
    result = provider.structured_output("sys", "analyze", {"type": "object"}, attachments=[att])
    assert result == {"ok": True}
    sent = provider.client.messages.captured["messages"][0]["content"]
    serialized = str(sent)
    assert "quote.pdf" in serialized
    assert "LINE ITEM: $500" in serialized


# ── M35c: NIM uses media_type and forwards text attachments ──────────────────

def _make_nim():
    from mihomes.services.ai.nim_provider import NIMProvider
    return NIMProvider(api_key="nvapi-test")


def test_nim_build_content_uses_media_type():
    provider = _make_nim()
    img = Attachment(filename="room.png", is_image=True, base64_data="QUJD", media_type="image/png")
    content = provider._build_content("scan this", [img])
    assert isinstance(content, list)
    image_blocks = [c for c in content if c.get("type") == "image_url"]
    assert image_blocks, "image should be forwarded"
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_nim_build_content_forwards_text_attachments():
    provider = _make_nim()
    att = Attachment(filename="log.txt", is_image=False, text_content="ERROR at line 5")
    content = provider._build_content("summarize", [att])
    assert "ERROR at line 5" in str(content)
    assert "log.txt" in str(content)


# ── M37: file_processor size caps ────────────────────────────────────────────

def test_process_upload_truncates_oversized_text():
    huge = ("x" * (MAX_TEXT_CHARS + 50_000)).encode()
    att = process_upload("big.log", huge, "text/plain")
    assert att is not None
    assert len(att.text_content) < len(huge)
    assert "truncated" in att.text_content.lower()


def test_process_upload_keeps_small_text_intact():
    small = b"just a little log line"
    att = process_upload("small.log", small, "text/plain")
    assert att.text_content == "just a little log line"


def test_process_upload_rejects_oversized_image():
    huge_image = b"\xff" * (MAX_IMAGE_BYTES + 1)
    att = process_upload("huge.png", huge_image, "image/png")
    assert att is None


def test_process_upload_keeps_normal_image():
    small_image = b"\x89PNG" + b"\x00" * 1000
    att = process_upload("ok.png", small_image, "image/png")
    assert att is not None
    assert att.is_image
    assert att.media_type == "image/png"
