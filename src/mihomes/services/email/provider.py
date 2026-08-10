"""Email provider abstraction — Protocol, exceptions, factory.

Mirrors `services/ai/provider.py`: Protocol + exception hierarchy + factory with
lazy per-branch imports.

**The Protocol is deliberately narrow: a provider transports an already-rendered
message.** Template selection and rendering live in `EmailService` so they happen
once, identically, regardless of vendor. A provider that rendered its own
templates would break failover (BILLING §2.1) — which is the whole reason this
abstraction exists rather than calling Resend directly.

This package is the one Phase-0 artifact reused **verbatim** in Phases 2–4
(BILLING §1) — welcome, invites, receipts and dunning all ride on it. SPEC-005
D11 makes the set's only widening: one additive `headers` kwarg for RFC 8058
unsubscribe.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "EmailAuthError",
    "EmailProvider",
    "EmailProviderError",
    "EmailResult",
    "EmailSendError",
    "get_email_provider",
]

DEFAULT_FROM = "MiHomes <no-reply@send.mihomes.ai>"


class EmailProviderError(Exception):
    """Base exception for email provider errors."""


class EmailAuthError(EmailProviderError):
    """API key missing or invalid.

    Deliberately NOT a subclass of EmailSendError: EmailService swallows send
    failures by design (§5.3), and a swallowed auth error would mean a launch
    where signups succeed and no mail ever arrives.
    """


class EmailSendError(EmailProviderError):
    """The provider accepted the request but could not deliver it."""


@dataclass(frozen=True)
class EmailResult:
    provider_message_id: str
    provider: str  # "resend" | "console"


@runtime_checkable
class EmailProvider(Protocol):
    """Protocol for email provider implementations."""

    provider_name: str

    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        *,
        text: str | None = None,
        reply_to: str | None = None,
    ) -> EmailResult:
        """Send a pre-rendered message. Returns the provider message id."""
        ...


def get_email_provider(
    provider_name: str | None = None,
    api_key: str | None = None,
    from_address: str | None = None,
) -> EmailProvider:
    """Factory. Defaults: EMAIL_PROVIDER env or 'resend'; EMAIL_FROM env."""
    name = (provider_name or os.environ.get("EMAIL_PROVIDER") or "resend").lower()
    sender = from_address or os.environ.get("EMAIL_FROM") or DEFAULT_FROM

    if name == "console":
        from mihomes.services.email.console_provider import ConsoleProvider

        return ConsoleProvider(from_address=sender)
    if name == "resend":
        from mihomes.services.email.resend_provider import ResendProvider

        return ResendProvider(api_key=api_key, from_address=sender)

    raise EmailProviderError(
        f"Unknown email provider: {name}. Supported: resend, console"
    )


def new_message_id() -> str:
    """Local id for providers that do not return one (ConsoleProvider)."""
    return f"local-{uuid.uuid4().hex}"
