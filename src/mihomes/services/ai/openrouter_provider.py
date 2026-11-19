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

    # H13: vision callers (room scans) check this flag. Haiku 5.5 accepts images on
    # OpenRouter, but the image path through this client is not verified yet, and the
    # model is operator-configurable (a text-only slug would break), so it stays off.
    supports_images: bool = False
