"""EmailService — renders a template key and dispatches it via a provider.

**Delivery semantics (BILLING §2.4): a call from a request handler must not block
or fail the caller.** `_send` catches `EmailSendError`, logs the template key and
recipient, and returns. A failed confirmation email must never roll back the
signup row — the user can request a resend, but a lost signup is unrecoverable.

`EmailAuthError` is deliberately NOT swallowed. A missing or rejected API key is a
deployment fault, not a transient delivery failure, and hiding it would produce a
launch where every signup succeeds and no mail ever arrives.
"""

from __future__ import annotations

import logging

from mihomes.services.email.provider import EmailProvider, EmailSendError
from mihomes.services.email.render import render_template

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self, provider: EmailProvider) -> None:
        self.provider = provider

    def _send(self, to: str, template: str, data: dict) -> None:
        """Render and dispatch. Catches EmailSendError, logs, never raises to the caller."""
        try:
            subject, html, text = render_template(template, data)
        except Exception:
            # A template fault is our bug, not the recipient's problem, and it is
            # not recoverable by retrying. Log with the key and move on rather
            # than taking down the request that triggered it.
            logger.exception("email template render failed: template=%s to=%s", template, to)
            return

        try:
            result = self.provider.send(to, subject, html, text=text)
        except EmailSendError:
            logger.exception("email send failed: template=%s to=%s", template, to)
            return

        logger.info(
            "email sent: template=%s to=%s provider=%s id=%s",
            template, to, result.provider, result.provider_message_id,
        )

    def send_waitlist_confirmation(
        self, to: str, *, confirm_url: str, position: int | None = None
    ) -> None:
        """Double opt-in confirmation (D7).

        `position` is computed but not displayed by default — O4 in §1.3 leaves
        showing it to the founder, and the template tolerates its absence.
        """
        self._send(
            to,
            "waitlist_confirmation",
            {"confirm_url": confirm_url, "position": position},
        )
