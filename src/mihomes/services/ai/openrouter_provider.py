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

    # H13: vision callers (room scans) check this flag. It follows the configured model,
    # because OpenRouter fronts both vision models (Anthropic's Claude family) and text-only
    # ones (DeepSeek chat): pointing `ai.openrouter_model` at a text-only slug must turn room
    # scans off, or the model "lists the assets in these photos" without seeing any.
    @property
    def supports_images(self) -> bool:
        return self.model.startswith("anthropic/")
