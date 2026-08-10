"""ConsoleProvider — dev/CI transport that logs and sends nothing.

`EMAIL_PROVIDER=console` is the setting for local work and the whole test suite.
It prints both parts, which is what makes SPEC-001 Step 7's verification possible:
"submit → token in console → GET confirm → confirmed_at set". A provider that
only summarised the message would break that loop.
"""

from __future__ import annotations

import logging

from mihomes.services.email.provider import EmailResult, new_message_id

logger = logging.getLogger(__name__)


class ConsoleProvider:
    """Writes the message to stdout and the log. Performs no network I/O."""

    provider_name = "console"

    def __init__(self, from_address: str) -> None:
        self.from_address = from_address

    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        *,
        text: str | None = None,
        reply_to: str | None = None,
    ) -> EmailResult:
        recipients = ", ".join(to) if isinstance(to, list) else to
        message_id = new_message_id()

        lines = [
            "",
            "=" * 72,
            "EMAIL (console provider — nothing was sent)",
            "=" * 72,
            f"From:     {self.from_address}",
            f"To:       {recipients}",
        ]
        if reply_to:
            lines.append(f"Reply-To: {reply_to}")
        lines += [
            f"Subject:  {subject}",
            f"Id:       {message_id}",
            "-" * 72,
            "TEXT PART:",
            text if text is not None else "(none)",
            "-" * 72,
            "HTML PART:",
            html,
            "=" * 72,
            "",
        ]
        print("\n".join(lines))

        logger.info(
            "console email: to=%s subject=%s id=%s", recipients, subject, message_id
        )
        return EmailResult(provider_message_id=message_id, provider=self.provider_name)
