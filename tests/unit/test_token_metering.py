"""AI usage is capped in tokens (2026-11-19): providers report usage, the wrapper records it.

The integration suites (`test_ai_usage.py`, `test_overage.py`) cover the rollup and the cap
against Postgres. These cover the part that has no database: that a call's tokens actually reach
`record_usage`, for every dispatch shape, and that a call with no reported usage is never free.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from mihomes.entitlements.limits import PLAN_LIMITS
from mihomes.services.ai.provider import token_usage
from mihomes.services.metering import meter
from mihomes.services.metering.ai_wrapper import MeteredProvider
from mihomes.services.metering.meter import format_tokens

# ------------------------------------------------------------------ token_usage


def test_reads_openai_compatible_usage():
    resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300))
    assert token_usage(resp) == (1200, 300)


def test_reads_anthropic_usage():
    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=900, output_tokens=150))
    assert token_usage(resp) == (900, 150)


def test_no_usage_is_none_not_zero():
    assert token_usage(SimpleNamespace()) is None
    assert token_usage(SimpleNamespace(usage=None)) is None


# ------------------------------------------------------------------ format + plan cards


@pytest.mark.parametrize("n,text", [
    (950, "950"), (1_000, "1k"), (500_000, "500k"), (1_250_000, "1.2M"),
    (10_000_000, "10M"), (20_000_000, "20M"),
])
def test_format_tokens(n, text):
    assert format_tokens(n) == text


def test_the_decided_limits():
    """Decided 2026-11-19: Free 500k, Pro 10M, Estate 20M tokens a month."""
    assert PLAN_LIMITS["free"]["ai_tokens_per_month"] == 500_000
    assert PLAN_LIMITS["pro"]["ai_tokens_per_month"] == 10_000_000
    assert PLAN_LIMITS["estate"]["ai_tokens_per_month"] == 20_000_000


def test_plan_cards_say_words_then_the_number():
    from mihomes.services.billing.plans import _features

    labels = {plan: [label for _ok, label in _features(PLAN_LIMITS[plan])] for plan in PLAN_LIMITS}
    assert "Light AI use · 500k AI tokens a month" in labels["free"]
    assert "Standard AI use · 10M AI tokens a month" in labels["pro"]
    assert "Heavy AI use · 20M AI tokens a month" in labels["estate"]


# ------------------------------------------------------------------ the wrapper


class _FakeProvider:
    """Reports `usage` after each call, like the real providers' `last_usage`."""

    def __init__(self, usage=(100, 20), chunks=("Hel", "lo"), fail_at=None):
        self._usage = usage
        self._chunks = chunks
        self._fail_at = fail_at
        self.last_usage = None

    def complete(self, system_prompt, user_message, context_data=None, attachments=None):
        self.last_usage = None
        self.last_usage = self._usage
        return "answer"

    def structured_output(self, system_prompt, user_message, schema, context_data=None,
                          attachments=None):
        self.last_usage = None
        self.last_usage = self._usage
        return {"ok": True}

    def stream(self, system_prompt, user_message, context_data=None, attachments=None):
        self.last_usage = None
        for i, chunk in enumerate(self._chunks):
            if self._fail_at == i:
                raise RuntimeError("vendor went away")
            yield chunk
        self.last_usage = self._usage


@pytest.fixture
def recorded(monkeypatch):
    """Capture what reaches `record_usage`; let every ceiling check pass."""
    from mihomes.entitlements.service import Allowed

    calls: list[dict] = []
    monkeypatch.setattr(meter, "check_and_reserve", lambda *a, **k: Allowed(limit=1))
    monkeypatch.setattr(meter, "record_usage", lambda session, account, **kw: calls.append(kw))
    return calls


def _wrap(provider):
    return MeteredProvider(
        provider, session_factory=lambda: contextlib.nullcontext(None),
        account=SimpleNamespace(id="acct", plan="pro"), entry_point="web.stream",
    )


def test_complete_records_the_vendor_tokens(recorded):
    assert _wrap(_FakeProvider(usage=(1200, 300))).complete("sys", "hi") == "answer"
    assert [(c["tokens_in"], c["tokens_out"]) for c in recorded] == [(1200, 300)]


def test_structured_output_records_the_vendor_tokens(recorded):
    _wrap(_FakeProvider(usage=(50, 7))).structured_output("sys", "hi", {"type": "object"})
    assert [(c["tokens_in"], c["tokens_out"]) for c in recorded] == [(50, 7)]


def test_missing_usage_is_estimated_never_free(recorded):
    """A vendor that reports no usage must still use allowance, or the cap would mean nothing."""
    _wrap(_FakeProvider(usage=None)).complete("s" * 400, "u" * 400, context_data="c" * 4000)
    (call,) = recorded
    assert call["tokens_in"] >= 1_200, "about 4 characters a token over everything sent"
    assert call["tokens_out"] >= 1


def test_images_count_toward_the_estimate(recorded):
    image = SimpleNamespace(is_image=True, text_content="")
    _wrap(_FakeProvider(usage=None)).complete("s", "u", attachments=[image])
    (call,) = recorded
    assert call["tokens_in"] >= 1_000


def test_stream_records_once_after_it_ends(recorded):
    stream = _wrap(_FakeProvider(usage=(800, 40))).stream("sys", "hi")
    assert recorded == [], "nothing is known about a stream's tokens until it ends"
    assert "".join(stream) == "Hello"
    assert [(c["tokens_in"], c["tokens_out"]) for c in recorded] == [(800, 40)]
    assert recorded[0]["method"] == "stream"


def test_an_abandoned_stream_is_still_counted(recorded):
    stream = _wrap(_FakeProvider(usage=(800, 40))).stream("sys", "hi")
    next(stream)
    stream.close()
    (call,) = recorded
    assert call["tokens_in"] >= 1 and call["tokens_out"] >= 1, "estimated, since usage never came"


def test_a_stream_that_fails_before_any_output_is_not_counted(recorded):
    stream = _wrap(_FakeProvider(fail_at=0)).stream("sys", "hi")
    with pytest.raises(RuntimeError):
        list(stream)
    assert recorded == []


def test_a_denied_stream_raises_before_streaming(monkeypatch):
    """The check stays eager: the caller learns about the cap when it calls `stream()`."""
    from mihomes.entitlements.service import Denied
    from mihomes.services.property import EntitlementError

    monkeypatch.setattr(
        meter, "check_and_reserve",
        lambda *a, **k: Denied(reason="AI paused", upgrade_target="estate", limit=1),
    )
    with pytest.raises(EntitlementError):
        _wrap(_FakeProvider()).stream("sys", "hi")


# ------------------------------------------------------------------ NIM / OpenRouter streaming


def test_openrouter_stream_asks_for_usage_and_keeps_it(monkeypatch):
    """Usage arrives on a final chunk with empty `choices`; it must be requested and kept."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from mihomes.services.ai.openrouter_provider import OpenRouterProvider

    provider = OpenRouterProvider()
    sent: dict = {}

    def chunk(text):
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None
        )

    def create(**kwargs):
        sent.update(kwargs)
        return iter([
            chunk("Hi"), chunk(" there"),
            SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=640, completion_tokens=12)),
        ])

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    assert "".join(provider.stream("sys", "hi")) == "Hi there"
    assert sent["stream_options"] == {"include_usage": True}
    assert provider.last_usage == (640, 12)
