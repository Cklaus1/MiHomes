"""AI provider abstraction — Protocol, exceptions, factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mihomes.services.ai.file_processor import Attachment


class AIProviderError(Exception):
    """Base exception for AI provider errors."""
    pass


class AIAuthError(AIProviderError):
    """API key missing or invalid."""
    pass


class AIRateLimitError(AIProviderError):
    """Rate limit exceeded."""
    pass


class AIProvider(Protocol):
    """Protocol for AI provider implementations."""

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> str:
        """Send a completion request. Returns the AI response text."""
        ...

    def structured_output(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        context_data: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> dict:
        """Request structured JSON output conforming to schema."""
        ...


def get_provider(provider_name: str = "claude", api_key: str | None = None, model: str | None = None) -> AIProvider:
    """Factory: returns the configured AIProvider instance."""
    if provider_name == "claude":
        from mihomes.services.ai.claude_provider import ClaudeProvider
        return ClaudeProvider(api_key=api_key, model=model)
    elif provider_name == "openai":
        from mihomes.services.ai.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key, model=model)
    elif provider_name == "ollama":
        from mihomes.services.ai.ollama_provider import OllamaProvider
        # Ollama's model is keyword-only with a non-None default; only override
        # when a model was actually requested.
        return OllamaProvider(model=model) if model else OllamaProvider()
    elif provider_name == "nim":
        from mihomes.services.ai.nim_provider import NIMProvider
        return NIMProvider(api_key=api_key, model=model)
    else:
        raise AIProviderError(f"Unknown AI provider: {provider_name}. Supported: claude, openai, ollama, nim")
