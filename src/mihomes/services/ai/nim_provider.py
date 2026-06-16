"""NVIDIA NIM AI provider — OpenAI-compatible API for open-weight models."""

import json
import os

from mihomes.services.ai.provider import AIAuthError, AIProviderError, AIRateLimitError

try:
    import openai
except ImportError:
    openai = None

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "meta/llama-3.2-11b-vision-instruct"


class NIMProvider:
    """AI provider using NVIDIA NIM's OpenAI-compatible API.

    Supports any model available in the NVIDIA NIM catalog.
    Default: qwen/qwen3.5-122b-a10b (free tier, 128k context).

    API keys start with 'nvapi-' and are obtained at build.nvidia.com.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        if openai is None:
            raise AIProviderError(
                "OpenAI SDK not installed. Install with: pip install openai>=1.0"
            )
        self.api_key = api_key or os.environ.get("NVIDIA_API_KEY")
        if not self.api_key:
            raise AIAuthError(
                "NVIDIA API key not found. Set NVIDIA_API_KEY environment variable "
                "or run: mihomes ai setup"
            )
        self.model = model or os.environ.get("MIHOMES_AI_MODEL", DEFAULT_MODEL)
        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=NIM_BASE_URL,
            timeout=120.0,
        )

    def _build_content(self, text: str, attachments=None) -> list | str:
        """Build multimodal content list when images present, plain string otherwise."""
        if not attachments:
            return text
        images = [a for a in attachments if getattr(a, "is_image", False) and a.base64_data]
        if not images:
            return text
        content = []
        for a in images:
            mime = a.content_type if hasattr(a, "content_type") and a.content_type else "image/jpeg"
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{a.base64_data}"},
            })
        content.append({"type": "text", "text": text})
        return content

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments=None,
    ) -> str:
        """Send a completion request to NVIDIA NIM."""
        text = user_message
        if context_data:
            text = f"{text}\n\n<estate_data>\n{context_data}\n</estate_data>"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": self._build_content(text, attachments)},
                ],
            )
            return response.choices[0].message.content
        except openai.AuthenticationError as e:
            raise AIAuthError(f"Invalid NVIDIA API key: {e}") from e
        except openai.RateLimitError as e:
            raise AIRateLimitError(f"NIM rate limited: {e}") from e
        except openai.APIError as e:
            raise AIProviderError(f"NVIDIA NIM API error: {e}") from e

    def stream(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments=None,
    ):
        """Stream tokens from NVIDIA NIM."""
        text = user_message
        if context_data:
            text = f"{text}\n\n<estate_data>\n{context_data}\n</estate_data>"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": self._build_content(text, attachments)},
                ],
                stream=True,
            )
            for chunk in response:
                if chunk.choices:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
        except openai.AuthenticationError as e:
            raise AIAuthError(f"Invalid NVIDIA API key: {e}") from e
        except openai.RateLimitError as e:
            raise AIRateLimitError(f"NIM rate limited: {e}") from e
        except openai.APIError as e:
            raise AIProviderError(f"NVIDIA NIM API error: {e}") from e

    def structured_output(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        context_data: str | None = None,
        attachments=None,
    ) -> dict:
        """Request structured JSON output from NIM."""
        text = user_message
        if context_data:
            text = f"{text}\n\n<estate_data>\n{context_data}\n</estate_data>"

        json_system = (
            f"{system_prompt}\n\n"
            "IMPORTANT: Respond with ONLY valid JSON matching this schema — no other text:\n"
            f"{json.dumps(schema, indent=2)}"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": json_system},
                    {"role": "user", "content": self._build_content(text, attachments)},
                ],
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content.strip()
            return json.loads(text)
        except openai.AuthenticationError as e:
            raise AIAuthError(f"Invalid NVIDIA API key: {e}") from e
        except openai.RateLimitError as e:
            raise AIRateLimitError(f"NIM rate limited: {e}") from e
        except openai.APIError as e:
            raise AIProviderError(f"NVIDIA NIM API error: {e}") from e
        except json.JSONDecodeError as e:
            raise AIProviderError("NIM did not return valid JSON for structured output") from e
