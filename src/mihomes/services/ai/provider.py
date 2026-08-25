"""AI provider abstraction — Protocol, exceptions, factory."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mihomes.services.ai.file_processor import Attachment


# M34: the Situation Report alone demands 17 sections; 4096 output tokens
# truncated it mid-report and returned it as if complete. Give completions a
# real budget, and mark any response the model still had to cut short.
MAX_OUTPUT_TOKENS = 16384
TRUNCATION_MARKER = (
    "\n\n[⚠️ Response truncated — the model hit its output limit before finishing. "
    "Narrow the request or split it into parts to get the full report.]"
)


class AIProviderError(Exception):
    """Base exception for AI provider errors."""
    pass


class AIAuthError(AIProviderError):
    """API key missing or invalid."""
    pass


class AIRateLimitError(AIProviderError):
    """Rate limit exceeded."""
    pass


class AIProvider(Protocol):
    """Protocol for AI provider implementations."""

    # H13: whether this provider forwards real image attachments to the model.
    # Vision tasks (e.g. room scans) must refuse providers where this is False.
    supports_images: bool

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> str:
        """Send a completion request. Returns the AI response text."""
        ...

    def structured_output(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        context_data: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> dict:
        """Request structured JSON output conforming to schema."""
        ...

    def stream(
        self,
        system_prompt: str,
        user_message: str,
        context_data: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> Iterator[str]:
        """Stream response tokens.

        **Declared as of SPEC-004 Step 9 (F8).** All four implementations have had this method
        since Phase 1 and `agent.py:44` has been calling it — but the Protocol declared only
        `complete` and `structured_output`, so the second-highest-token path in the app was
        formally outside the interface it travels through. An undeclared method is one a wrapper
        has no reason to proxy, which is precisely how streaming would have escaped Step 10's
        meter.
        """
        ...


def get_provider(
    provider_name: str = "claude",
    api_key: str | None = None,
    model: str | None = None,
    *,
    entry_point: str | None = None,
) -> AIProvider:
    """Factory: returns the configured AIProvider instance, **metered** (SPEC-004 D17).

    Every AI dispatch in the tree passes through here — Step 9 closed the last bypass — so
    wrapping the return value is what makes A11 achievable at all: *"the meter binds at every
    entry point, or it bounds nothing."*

    `entry_point` names the dispatch path (`"web.agent"`, `"cli.ai"`, `"gateway.telegram"`, …).
    It is **optional and defaults to unmetered**, which is deliberate rather than lax:

    - `PRICING` §5.2 and N10 exempt **system-initiated** calls. A nightly recurring-task sweep or
      a weather job must never consume a household's quota — *"the user cannot upgrade their way
      out of something they did not do"* — and those paths have no request and no account bound.
    - Requiring the argument everywhere would mean the background jobs pass a sentinel meaning
      "do not count this", which is the same decision written less clearly, and one a future
      caller could copy onto a user-facing path by accident.

    A11's test enumerates dispatch modules **from the tree** and asserts each user-facing one
    passes an `entry_point`, so a new metered path cannot be forgotten — the omission fails the
    suite rather than reading as an exemption.
    """
    provider = _construct(provider_name, api_key, model)
    if entry_point is None:
        return provider

    from mihomes.db import get_session
    from mihomes.models.account import Account
    from mihomes.services.metering.ai_wrapper import MeteredProvider
    from mihomes.tenancy import current_account

    account_id = current_account.get(None)
    if account_id is None:
        # No tenant bound: an operator CLI invocation (SPEC-002 D1) or a background job. Nothing
        # to bill, and fabricating an account to bill would be worse than not counting.
        return provider

    with get_session() as session:
        account = session.get(Account, account_id)

    return MeteredProvider(
        provider, session_factory=get_session, account=account, entry_point=entry_point,
    )


def _construct(provider_name: str, api_key: str | None, model: str | None) -> AIProvider:
    """The original dispatch — string match, lazy import, explicit `else: raise`."""
    if provider_name == "claude":
        from mihomes.services.ai.claude_provider import ClaudeProvider
        return ClaudeProvider(api_key=api_key, model=model)
    elif provider_name == "openai":
        from mihomes.services.ai.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key, model=model)
    elif provider_name == "ollama":
        from mihomes.services.ai.ollama_provider import OllamaProvider
        # Ollama's model is keyword-only with a non-None default; only override
        # when a model was actually requested.
        return OllamaProvider(model=model) if model else OllamaProvider()
    elif provider_name == "nim":
        from mihomes.services.ai.nim_provider import NIMProvider
        return NIMProvider(api_key=api_key, model=model)
    else:
        raise AIProviderError(f"Unknown AI provider: {provider_name}. Supported: claude, openai, ollama, nim")
