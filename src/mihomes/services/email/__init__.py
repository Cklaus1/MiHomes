"""Email package — transport-only providers plus template rendering.

Ships in Phase 0 and is reused **verbatim** in Phases 2–4 (BILLING §1): welcome,
invites, receipts and dunning all ride on it. SPEC-005 D11 makes the set's only
widening — one additive `headers` kwarg for RFC 8058 unsubscribe — so treat the
`EmailProvider` Protocol here as a stable contract, not a Phase-0 sketch.
"""

from mihomes.services.email.provider import (
    EmailAuthError,
    EmailProvider,
    EmailProviderError,
    EmailResult,
    EmailSendError,
    get_email_provider,
)
from mihomes.services.email.render import TemplateNotFoundError, render_template
from mihomes.services.email.service import EmailService

__all__ = [
    "EmailAuthError",
    "EmailProvider",
    "EmailProviderError",
    "EmailResult",
    "EmailSendError",
    "EmailService",
    "TemplateNotFoundError",
    "get_email_provider",
    "render_template",
]
