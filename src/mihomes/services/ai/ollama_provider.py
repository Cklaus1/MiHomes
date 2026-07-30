"""Ollama AI provider — local LLM via Ollama HTTP API."""

import json
import urllib.request
import urllib.error

from mihomes.services.ai.provider import AIProviderError


class OllamaProvider:
    """AI provider using local Ollama models. Zero cloud dependency."""

    # H13: this provider flattens attachments to a text block (see complete) —
    # it never sends real image data, so it must not be used for vision tasks.
    supports_images: bool = False

    def __init__(self, *, model: str = "llama3.1", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments=None,
    ) -> str:
        message_content = user_message
        if attachments:
            from mihomes.services.ai.file_processor import attachments_to_text_block
            message_content = attachments_to_text_block(attachments) + "\n\n" + message_content
        if context_data:
            message_content = f"{message_content}\n\n<estate_data>\n{context_data}\n</estate_data>"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message_content},
            ],
            "stream": False,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["message"]["content"]
        except urllib.error.URLError as e:
            raise AIProviderError(
                f"Cannot connect to Ollama at {self.base_url}. "
                f"Is Ollama running? Install: https://ollama.ai — Error: {e}"
            )
        except (KeyError, json.JSONDecodeError) as e:
            raise AIProviderError(f"Unexpected Ollama response: {e}")

    def stream(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments=None,
    ):
        """Yield full response as one token (Ollama streaming not yet supported)."""
        result = self.complete(system_prompt, user_message, context_data=context_data, attachments=attachments)
        yield result

    def structured_output(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        context_data: str | None = None,
        attachments=None,
    ) -> dict:
        """Request structured output — instructs model to respond in JSON."""
        json_prompt = (
            f"{system_prompt}\n\n"
            "IMPORTANT: You MUST respond with ONLY valid JSON matching this schema:\n"
            f"{json.dumps(schema, indent=2)}\n"
            "No other text. Only JSON."
        )
        response_text = self.complete(json_prompt, user_message, context_data)

        # Try to extract JSON from response
        text = response_text.strip()
        # Handle markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise AIProviderError(f"Ollama did not return valid JSON. Response: {response_text[:200]}")
