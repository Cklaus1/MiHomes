"""OpenRouter provider: NIM's OpenAI-compatible client pointed at openrouter.ai."""

import pytest

from mihomes.services.ai.nim_provider import NIM_BASE_URL, NIMProvider
from mihomes.services.ai.openrouter_provider import OpenRouterProvider
from mihomes.services.ai.provider import AIAuthError, _construct


def test_factory_builds_openrouter_with_its_own_endpoint():
    p = _construct("openrouter", "sk-or-test", "anthropic/claude-haiku-5.5")
    assert isinstance(p, OpenRouterProvider)
    assert str(p.client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
    assert p.model == "anthropic/claude-haiku-5.5"
    assert p.supports_images is True


def test_openrouter_reads_its_own_env_key(monkeypatch):
    monkeypatch.delenv("MIHOMES_AI_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-should-not-be-used")
    p = OpenRouterProvider()
    assert p.api_key == "sk-or-env"
    assert p.model == "anthropic/claude-haiku-5.5"


def test_openrouter_missing_key_names_openrouter(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(AIAuthError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider()


def test_nim_unchanged():
    p = NIMProvider(api_key="nvapi-test", model="m")
    assert str(p.client.base_url).rstrip("/") == NIM_BASE_URL
    assert p.supports_images is True
