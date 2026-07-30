"""AI configuration helpers — API key resolution, model selection."""

import os

from sqlalchemy.orm import Session

from mihomes.services.ai.provider import AIAuthError
from mihomes.services.config_service import get_config

# Single source of truth for the default Claude model (spec H9/Q1). The prior
# default `claude-sonnet-4-20250514` reached its announced retirement on
# 2026-06-15, so fresh installs' AI calls 404'd. `claude-sonnet-5` is the
# current-generation balanced model and carries no dated-pin retirement trap.
DEFAULT_MODEL = "claude-sonnet-5"


def get_ai_api_key(session: Session, provider: str = "claude") -> str:
    """Resolve API key from env then config DB. Raises AIAuthError if not found."""
    env_vars = {
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "nim": "NVIDIA_API_KEY",
    }

    # Try env var first
    env_var = env_vars.get(provider, f"{provider.upper()}_API_KEY")
    key = os.environ.get(env_var)
    if key:
        return key

    # Try config DB
    config_key = f"ai.{provider}_api_key"
    key = get_config(session, config_key)
    if key:
        return key

    raise AIAuthError(
        f"API key for {provider} not found. "
        f"Set {env_var} environment variable or run: mihomes ai setup"
    )


def get_ai_provider_name(session: Session) -> str:
    """Get configured AI provider name."""
    return get_config(session, "ai.provider", "claude")


def get_ai_model(session: Session, provider: str = "claude") -> str:
    """Get configured model name with sensible defaults.

    Checks ai.<provider>_model first, then ai.model, then built-in defaults.
    """
    provider_specific = get_config(session, f"ai.{provider}_model")
    if provider_specific:
        return provider_specific
    stored = get_config(session, "ai.model")
    if stored:
        return stored
    defaults = {
        "claude": DEFAULT_MODEL,
        "openai": "gpt-4o",
        "ollama": "llama3.1",
        "nim": "meta/llama-3.2-11b-vision-instruct",
    }
    return defaults.get(provider, DEFAULT_MODEL)
