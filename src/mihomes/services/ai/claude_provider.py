"""Claude AI provider implementation."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Iterator

import anthropic

from mihomes.services.ai.ai_config import DEFAULT_MODEL
from mihomes.services.ai.provider import (
    MAX_OUTPUT_TOKENS,
    TRUNCATION_MARKER,
    AIAuthError,
    AIProviderError,
    AIRateLimitError,
)


def _extract_text(response) -> str:
    """M35: pull the answer text out of a Messages response, skipping non-text
    blocks (thinking/redacted) instead of blindly reading `content[0].text`.

    M34: append a marker if the model stopped because it ran out of output
    tokens, so a truncated report is never shown as if it were complete.
    """
    parts = [
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    if not parts:
        raise AIProviderError(
            "Claude returned no text content (possible refusal or empty response)."
        )
    text = "".join(parts)
    if getattr(response, "stop_reason", None) == "max_tokens":
        text += TRUNCATION_MARKER
    return text

if TYPE_CHECKING:
    from mihomes.services.ai.file_processor import Attachment


class ClaudeProvider:
    """AI provider using Anthropic's Claude API."""

    # H13: Claude forwards image attachments to the model as real image blocks.
    supports_images: bool = True

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise AIAuthError(
                "Anthropic API key not found. Set ANTHROPIC_API_KEY environment variable "
                "or run: mihomes ai setup"
            )
        self.model = model or os.environ.get("MIHOMES_AI_MODEL", DEFAULT_MODEL)
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> str:
        """Send a completion request to Claude, with optional file attachments."""
        text = user_message
        if context_data:
            text = f"{text}\n\n<estate_data>\n{context_data}\n</estate_data>"

        if attachments:
            content: list[dict] = []
            for att in attachments:
                if att.is_image:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": att.media_type,
                            "data": att.base64_data,
                        },
                    })
                    content.append({"type": "text", "text": f"[Image: {att.filename}]"})
                elif att.text_content:
                    content.append({
                        "type": "text",
                        "text": f"--- Attached file: {att.filename} ---\n{att.text_content}\n---",
                    })
            content.append({"type": "text", "text": text})
        else:
            content = text  # type: ignore[assignment]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
            )
            return _extract_text(response)
        except anthropic.AuthenticationError as e:
            raise AIAuthError(f"Invalid API key: {e}")
        except anthropic.RateLimitError as e:
            raise AIRateLimitError(f"Rate limited: {e}")
        except anthropic.APIError as e:
            raise AIProviderError(f"Claude API error: {e}")

    def stream(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> Iterator[str]:
        """Stream tokens from Claude API."""
        text = user_message
        if context_data:
            text = f"{text}\n\n<estate_data>\n{context_data}\n</estate_data>"

        if attachments:
            content: list[dict] = []
            for att in attachments:
                if att.is_image:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": att.media_type,
                            "data": att.base64_data,
                        },
                    })
                    content.append({"type": "text", "text": f"[Image: {att.filename}]"})
                elif att.text_content:
                    content.append({
                        "type": "text",
                        "text": f"--- Attached file: {att.filename} ---\n{att.text_content}\n---",
                    })
            content.append({"type": "text", "text": text})
        else:
            content = text  # type: ignore[assignment]

        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
            ) as stream:
                for chunk in stream.text_stream:
                    yield chunk
        except anthropic.AuthenticationError as e:
            raise AIAuthError(f"Invalid API key: {e}")
        except anthropic.RateLimitError as e:
            raise AIRateLimitError(f"Rate limited: {e}")
        except anthropic.APIError as e:
            raise AIProviderError(f"Claude API error: {e}")

    def structured_output(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        context_data: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> dict:
        """Request structured output from Claude using tool_use."""
        message_content = user_message
        if context_data:
            message_content = f"{user_message}\n\n<data>\n{context_data}\n</data>"

        if attachments:
            content: list[dict] = []
            for att in attachments:
                if att.is_image:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": att.media_type,
                            "data": att.base64_data,
                        },
                    })
                    content.append({"type": "text", "text": f"[Image: {att.filename}]"})
                elif att.text_content:
                    # M35: mirror complete()'s handling — a text/PDF attachment
                    # (e.g. a contractor quote) was silently dropped here.
                    content.append({
                        "type": "text",
                        "text": f"--- Attached file: {att.filename} ---\n{att.text_content}\n---",
                    })
            content.append({"type": "text", "text": message_content})
        else:
            content = message_content  # type: ignore[assignment]

        tool = {
            "name": "structured_response",
            "description": "Return the structured response",
            "input_schema": schema,
        }

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "structured_response"},
            )

            for block in response.content:
                if block.type == "tool_use":
                    return block.input

            # Fallback: try to parse text as JSON
            for block in response.content:
                if block.type == "text":
                    return json.loads(block.text)

            raise AIProviderError("No structured output in Claude response")

        except anthropic.AuthenticationError as e:
            raise AIAuthError(f"Invalid API key: {e}")
        except anthropic.RateLimitError as e:
            raise AIRateLimitError(f"Rate limited: {e}")
        except anthropic.APIError as e:
            raise AIProviderError(f"Claude API error: {e}")
        except json.JSONDecodeError as e:
            # Include partial response for debugging
            text_blocks = [b.text for b in response.content if hasattr(b, 'text')] if 'response' in dir() else []
            preview = text_blocks[0][:200] if text_blocks else "no text"
            raise AIProviderError(f"Failed to parse structured output from Claude. Response: {preview}")
