"""OpenRouter AI provider — same OpenAI-compatible client as NIM, different endpoint.

OpenRouter routes one key to many vendors' models (e.g. ``anthropic/claude-haiku-5.5``).
Model slugs are ``<vendor>/<model>`` as listed at https://openrouter.ai/models.
"""

from mihomes.services.ai.nim_provider import NIMProvider


class OpenRouterProvider(NIMProvider):
    BASE_URL = "https://openrouter.ai/api/v1"
    KEY_ENV = "OPENROUTER_API_KEY"
    LABEL = "OpenRouter"
    DEFAULT_MODEL = "anthropic/claude-haiku-5.5"

    # H13: vision callers (room scans) check this flag. The model is a developer choice
    # (Claude Haiku), which reads images; switch this off if it moves to a text-only model.
    supports_images: bool = True
