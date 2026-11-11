"""OpenRouter AI provider — same OpenAI-compatible client as NIM, different endpoint.

OpenRouter routes one key to many vendors' models (e.g. ``deepseek/deepseek-chat``).
Model slugs are ``<vendor>/<model>`` as listed at https://openrouter.ai/models.
"""

from mihomes.services.ai.nim_provider import NIMProvider


class OpenRouterProvider(NIMProvider):
    BASE_URL = "https://openrouter.ai/api/v1"
    KEY_ENV = "OPENROUTER_API_KEY"
    LABEL = "OpenRouter"
    DEFAULT_MODEL = "deepseek/deepseek-chat"

    # H13: DeepSeek chat models are text-only. Vision callers (room scans) check this
    # flag, so leave it off unless a vision model is configured and verified.
    supports_images: bool = False
