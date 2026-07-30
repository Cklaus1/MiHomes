"""Regression tests for model threading through the provider factory (spec H8/H9/Q1).

H8: the factory `get_provider()` previously dropped the caller's `model`, so a
    configured model never reached the provider instance.
H9: the old default `claude-sonnet-4-20250514` reached its announced retirement
    on 2026-06-15; the default must be the current-generation `claude-sonnet-5`.
Q1: there must be exactly one source of truth for that default — `DEFAULT_MODEL`
    in `ai_config.py`.

These tests instantiate providers with a fake API key. No network call happens at
construction time (the SDK client is built lazily / does not dial out), so no real
key is required.
"""

import pytest

from mihomes.services.ai.ai_config import DEFAULT_MODEL
from mihomes.services.ai.provider import get_provider


CONFIGURED = "claude-opus-5"  # any non-default id proves the value threads through


# ---------------------------------------------------------------------------
# Q1 — single source of truth
# ---------------------------------------------------------------------------

def test_default_model_is_current_generation():
    """The one canonical default must be the current, non-retired model id."""
    assert DEFAULT_MODEL == "claude-sonnet-5"
    # The retired dated pin must never be the live default again.
    assert DEFAULT_MODEL != "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# H8 — a configured model reaches the provider instance
# ---------------------------------------------------------------------------

def test_get_provider_threads_model_to_claude():
    provider = get_provider("claude", api_key="test-key", model=CONFIGURED)
    assert provider.model == CONFIGURED


def test_get_provider_threads_model_to_openai():
    provider = get_provider("openai", api_key="test-key", model=CONFIGURED)
    assert provider.model == CONFIGURED


def test_get_provider_threads_model_to_nim():
    provider = get_provider("nim", api_key="test-key", model=CONFIGURED)
    assert provider.model == CONFIGURED


def test_get_provider_threads_model_to_ollama():
    # Ollama's `model` is keyword-only with a non-None default; the factory must
    # still honour an explicitly requested model.
    provider = get_provider("ollama", model=CONFIGURED)
    assert provider.model == CONFIGURED


# ---------------------------------------------------------------------------
# H9 — the default resolves to DEFAULT_MODEL when no model is configured
# ---------------------------------------------------------------------------

def test_claude_defaults_to_default_model(monkeypatch):
    # Ensure no env override leaks in from the host environment.
    monkeypatch.delenv("MIHOMES_AI_MODEL", raising=False)
    provider = get_provider("claude", api_key="test-key")
    assert provider.model == DEFAULT_MODEL


def test_ollama_defaults_when_no_model_configured():
    # With no model requested the factory takes the no-arg construction path,
    # yielding Ollama's own built-in default rather than crashing.
    provider = get_provider("ollama")
    assert provider.model == "llama3.1"
