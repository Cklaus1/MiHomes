"""ResendProvider — the production transport (D12: send.mihomes.ai, verified in Resend).

Transport only. It receives an already-rendered subject/html/text and hands them
to the vendor; it never selects or renders a template. See provider.py for why.
"""

from __future__ import annotations

import logging
import os

from mihomes.services.email.provider import (
    EmailAuthError,
    EmailResult,
    EmailSendError,
)

logger = logging.getLogger(__name__)


class ResendProvider:
    """Sends via the Resend HTTP API."""

    provider_name = "resend"

    def __init__(self, api_key: str | None = None, from_address: str | None = None) -> None:
        key = api_key or os.environ.get("RESEND_API_KEY")
        if not key:
            # Fail at construction, not at send: a provider that silently no-ops
            # would give us a launch where signups succeed and no mail arrives.
            raise EmailAuthError(
                "RESEND_API_KEY is not set. Set it, or use EMAIL_PROVIDER=console "
                "for local development."
            )
        self.api_key = key
        self.from_address = from_address or os.environ.get("EMAIL_FROM")

    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        *,
        text: str | None = None,
        reply_to: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> EmailResult:
        import resend

        resend.api_key = self.api_key

        params: dict = {
            "from": self.from_address,
            "to": to if isinstance(to, list) else [to],
            "subject": subject,
            "html": html,
        }
        if text is not None:
            params["text"] = text
        if reply_to:
            params["reply_to"] = reply_to
        if headers:
            # Resend's own key for per-message SMTP headers. Omitted entirely when empty
            # so a transactional send carries no List-Unsubscribe (SPEC-005 A18).
            params["headers"] = dict(headers)

        try:
            sent = resend.Emails.send(params)
        except Exception as exc:  # vendor SDK raises its own hierarchy
            # Narrow to auth so a misconfigured key is not swallowed by
            # EmailService's EmailSendError handler (§5.3).
            message = str(exc).lower()
            if "unauthorized" in message or "api key" in message or "401" in message:
                raise EmailAuthError(f"Resend rejected the API key: {exc}") from exc
            raise EmailSendError(f"Resend send failed: {exc}") from exc

        message_id = (sent or {}).get("id")
        if not message_id:
            raise EmailSendError(f"Resend returned no message id: {sent!r}")

        logger.info("resend email sent: id=%s subject=%s", message_id, subject)
        return EmailResult(provider_message_id=message_id, provider=self.provider_name)
