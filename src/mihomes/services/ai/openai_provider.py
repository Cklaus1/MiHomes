"""OpenAI AI provider implementation."""

import json
import os

from mihomes.services.ai.provider import (
    AIAuthError,
    AIProviderError,
    AIRateLimitError,
    token_usage,
)

try:
    import openai
except ImportError:
    openai = None


class OpenAIProvider:
    """AI provider using OpenAI's API."""

    # H13: this provider flattens attachments to a text block (see complete /
    # structured_output) — it never sends real image data, so it must not be
    # used for vision tasks like room scans.
    supports_images: bool = False

    #: `(tokens_in, tokens_out)` of the most recent call, read by the AI meter (see NIMProvider).
    last_usage: tuple[int, int] | None = None

    def __init__(self, api_key: str | None = None, model: str | None = None):
        if openai is None:
            raise AIProviderError(
                "OpenAI SDK not installed. Install with: pip install openai>=1.0"
            )
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise AIAuthError(
                "OpenAI API key not found. Set OPENAI_API_KEY environment variable "
                "or run: mihomes ai setup"
            )
        self.model = model or os.environ.get("MIHOMES_AI_MODEL", "gpt-4o")
        self.client = openai.OpenAI(api_key=self.api_key)

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments=None,
    ) -> str:
        self.last_usage = None
        message_content = user_message
        if attachments:
            from mihomes.services.ai.file_processor import attachments_to_text_block
            message_content = attachments_to_text_block(attachments) + "\n\n" + message_content
        if context_data:
            message_content = f"{message_content}\n\n<estate_data>\n{context_data}\n</estate_data>"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message_content},
                ],
            )
            self.last_usage = token_usage(response)
            # L10: message.content is Optional per the OpenAI schema (None on a
            # refusal/tool-only turn); the -> str contract must not leak None.
            return response.choices[0].message.content or ""
        except openai.AuthenticationError as e:
            raise AIAuthError(f"Invalid API key: {e}") from e
        except openai.RateLimitError as e:
            raise AIRateLimitError(f"Rate limited: {e}") from e
        except openai.APIError as e:
            raise AIProviderError(f"OpenAI API error: {e}") from e

    def stream(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments=None,
    ):
        """Stream tokens from OpenAI."""
        self.last_usage = None
        message_content = user_message
        if attachments:
            from mihomes.services.ai.file_processor import attachments_to_text_block
            message_content = attachments_to_text_block(attachments) + "\n\n" + message_content
        if context_data:
            message_content = f"{message_content}\n\n<estate_data>\n{context_data}\n</estate_data>"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message_content},
                ],
                stream=True,
                stream_options={"include_usage": True},
            )
            for chunk in response:
                if getattr(chunk, "usage", None) is not None:
                    self.last_usage = token_usage(chunk)
                if chunk.choices:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
        except openai.AuthenticationError as e:
            raise AIAuthError(f"Invalid API key: {e}") from e
        except openai.RateLimitError as e:
            raise AIRateLimitError(f"Rate limited: {e}") from e
        except openai.APIError as e:
            raise AIProviderError(f"OpenAI API error: {e}") from e

    def structured_output(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        context_data: str | None = None,
        attachments=None,
    ) -> dict:
        self.last_usage = None
        message_content = user_message
        if context_data:
            message_content = f"{user_message}\n\n<data>\n{context_data}\n</data>"

        tool = {
            "type": "function",
            "function": {
                "name": "structured_response",
                "description": "Return the structured response",
                "parameters": schema,
            },
        }

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message_content},
                ],
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "structured_response"}},
            )
            self.last_usage = token_usage(response)

            msg = response.choices[0].message
            if msg.tool_calls:
                return json.loads(msg.tool_calls[0].function.arguments)
            if msg.content:
                return json.loads(msg.content)

            raise AIProviderError("No structured output in OpenAI response")
        except openai.AuthenticationError as e:
            raise AIAuthError(f"Invalid API key: {e}") from e
        except openai.RateLimitError as e:
            raise AIRateLimitError(f"Rate limited: {e}") from e
        except openai.APIError as e:
            raise AIProviderError(f"OpenAI API error: {e}") from e
        except json.JSONDecodeError as e:
            raise AIProviderError("Failed to parse structured output from OpenAI") from e
