"""AI provider abstraction — Protocol, exceptions, factory."""

from typing import Protocol


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
    ) -> str:
        """Send a completion request. Returns the AI response text."""
        ...

    def structured_output(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        context_data: str | None = None,
    ) -> dict:
        """Request structured JSON output conforming to schema."""
        ...


def get_provider(provider_name: str = "claude", api_key: str | None = None) -> AIProvider:
    """Factory: returns the configured AIProvider instance."""
    if provider_name == "claude":
        from mihomes.services.ai.claude_provider import ClaudeProvider
        return ClaudeProvider(api_key=api_key)
    elif provider_name == "openai":
        from mihomes.services.ai.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key)
    elif provider_name == "ollama":
        from mihomes.services.ai.ollama_provider import OllamaProvider
        return OllamaProvider()
    else:
        raise AIProviderError(f"Unknown AI provider: {provider_name}")
