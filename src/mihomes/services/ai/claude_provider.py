"""Claude AI provider implementation."""

import json
import os

import anthropic

from mihomes.services.ai.provider import AIAuthError, AIProviderError, AIRateLimitError


class ClaudeProvider:
    """AI provider using Anthropic's Claude API."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise AIAuthError(
                "Anthropic API key not found. Set ANTHROPIC_API_KEY environment variable "
                "or run: mihomes ai setup"
            )
        self.model = model or os.environ.get("MIHOMES_AI_MODEL", "claude-sonnet-4-20250514")
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
    ) -> str:
        """Send a completion request to Claude."""
        message_content = user_message
        if context_data:
            message_content = f"{user_message}\n\n<estate_data>\n{context_data}\n</estate_data>"

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": message_content}],
            )
            return response.content[0].text
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
    ) -> dict:
        """Request structured output from Claude using tool_use."""
        message_content = user_message
        if context_data:
            message_content = f"{user_message}\n\n<data>\n{context_data}\n</data>"

        tool = {
            "name": "structured_response",
            "description": "Return the structured response",
            "input_schema": schema,
        }

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": message_content}],
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
        except json.JSONDecodeError:
            raise AIProviderError("Failed to parse structured output from Claude")
