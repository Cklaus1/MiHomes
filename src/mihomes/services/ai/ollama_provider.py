"""Ollama AI provider — local LLM integration via Ollama."""


class OllamaProvider:
    """AI provider using local Ollama models.

    This is a stub implementation. Full integration with the Ollama API
    will be implemented in a future phase.
    """

    def __init__(self, *, model: str = "llama3", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
    ) -> str:
        """Send a completion request to Ollama."""
        raise NotImplementedError(
            "Ollama provider is not yet implemented. "
            "Install Ollama and configure the base_url to enable local LLM support."
        )

    def structured_output(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        context_data: str | None = None,
    ) -> dict:
        """Request structured JSON output from Ollama."""
        raise NotImplementedError(
            "Ollama provider is not yet implemented. "
            "Install Ollama and configure the base_url to enable local LLM support."
        )
