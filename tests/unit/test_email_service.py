"""EmailService delivery semantics (SPEC-001 §5.3, BILLING §2.4).

The load-bearing rule: a call from a request handler must not block or fail the
caller. A failed confirmation email must never roll back the signup row — the
user can request a resend, but a lost signup is unrecoverable.

The counterpart matters just as much: an auth fault must NOT be swallowed, or a
launch looks healthy while no mail ever arrives. A10 tests the route-level
behaviour; these tests pin the service contract underneath it.
"""

import inspect
import logging

import pytest

from mihomes.services.email import EmailService
from mihomes.services.email.provider import (
    EmailAuthError,
    EmailResult,
    EmailSendError,
)

CONFIRM_URL = "https://mihomes.ai/waitlist/confirm?token=t"


class RecordingProvider:
    provider_name = "recording"

    def __init__(self):
        self.calls = []

    def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
        self.calls.append(
            {"to": to, "subject": subject, "html": html, "text": text,
             "headers": headers}
        )
        return EmailResult(provider_message_id="rec-1", provider=self.provider_name)


class FailingProvider:
    provider_name = "failing"

    def send(self, *args, **kwargs):
        raise EmailSendError("upstream refused the message")


class AuthFailingProvider:
    provider_name = "auth-failing"

    def send(self, *args, **kwargs):
        raise EmailAuthError("API key rejected")


def test_send_waitlist_confirmation_renders_and_dispatches():
    provider = RecordingProvider()
    EmailService(provider).send_waitlist_confirmation(
        "alex@example.com", confirm_url=CONFIRM_URL, position=42
    )

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["to"] == "alex@example.com"
    assert call["subject"], "a subject must be rendered from the template block"
    assert CONFIRM_URL in call["html"]
    assert call["text"] is not None, "the text part must always be supplied"
    assert CONFIRM_URL in call["text"]


def test_send_failure_does_not_raise_to_the_caller(caplog):
    """The signup must survive a dead mail provider (BILLING §2.4)."""
    with caplog.at_level(logging.ERROR):
        EmailService(FailingProvider()).send_waitlist_confirmation(
            "alex@example.com", confirm_url=CONFIRM_URL
        )

    assert any(
        "email send failed" in r.message or "email send failed" in r.getMessage()
        for r in caplog.records
    ), "a swallowed failure must still be logged with the template key and recipient"


def test_send_failure_logs_template_and_recipient(caplog):
    """Swallowing without context would make delivery problems undiagnosable."""
    with caplog.at_level(logging.ERROR):
        EmailService(FailingProvider()).send_waitlist_confirmation(
            "alex@example.com", confirm_url=CONFIRM_URL
        )

    blob = " ".join(r.getMessage() for r in caplog.records)
    assert "waitlist_confirmation" in blob
    assert "alex@example.com" in blob


def test_auth_error_is_not_swallowed():
    """A rejected API key is a deployment fault, not a transient delivery failure.

    If this were swallowed, every signup would succeed and no mail would arrive —
    the failure mode would only surface as a mysteriously flat confirmation rate.
    """
    with pytest.raises(EmailAuthError):
        EmailService(AuthFailingProvider()).send_waitlist_confirmation(
            "alex@example.com", confirm_url=CONFIRM_URL
        )


def test_template_fault_does_not_raise_to_the_caller(caplog):
    """A bad template is our bug and is not fixed by retrying — log and return."""

    class BadTemplateService(EmailService):
        def send_broken(self, to):
            self._send(to, "definitely_not_a_template", {}, klass="transactional")

    with caplog.at_level(logging.ERROR):
        BadTemplateService(RecordingProvider()).send_broken("alex@example.com")

    assert any("render failed" in r.getMessage() for r in caplog.records)


def test_position_is_optional():
    """O4 default: compute it, do not display it. Absence must not break the send."""
    provider = RecordingProvider()
    EmailService(provider).send_waitlist_confirmation(
        "alex@example.com", confirm_url=CONFIRM_URL
    )
    assert len(provider.calls) == 1


# --- SPEC-005 Step 2 (D13/A3): `klass` is required at the choke point --------------------


def test_klass_required():
    """A3 — `_send` cannot be called without an explicit `klass`.

    Asserted as a `TypeError` from the signature itself, not a runtime check inside the body.
    A default would silently pick a suppression policy for every future call site, and there is
    no safe default to pick: a suppressed receipt is a billing dispute, an unsuppressed drip is
    a CAN-SPAM violation. Keyword-only as well as required, so it can never be passed
    positionally into `data`'s slot by a caller who miscounted arguments.
    """
    service = EmailService(RecordingProvider())

    with pytest.raises(TypeError, match="klass"):
        service._send("a@b.com", "welcome", {})  # type: ignore[call-arg]

    params = inspect.signature(EmailService._send).parameters
    assert params["klass"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["klass"].default is inspect.Parameter.empty


def test_an_unknown_klass_is_refused_rather_than_treated_as_transactional():
    """A third class must not fail open into the unsuppressed branch.

    `if klass == "lifecycle"` alone would treat any typo — `"life_cycle"`, `"marketing"` — as
    transactional and send it, which is the wrong direction to be wrong in. The membership check
    against MESSAGE_CLASSES is what makes a typo loud.
    """
    service = EmailService(RecordingProvider())

    with pytest.raises(ValueError, match="unknown message class"):
        service._send("a@b.com", "welcome", {}, klass="marketing")


def test_every_send_method_declares_a_class():
    """Every public `send_*` reaches `_send` with a `klass`, enumerated from the class.

    Derived rather than listed: a ninth `send_*` added without a class fails here instead of
    at whatever call site first exercises it in production. The check is that the method runs
    to completion against a recording provider — `_send` raises `TypeError` when `klass` is
    missing, so a method that forgot it cannot pass.
    """
    service = EmailService(RecordingProvider(), session=None)
    senders = [
        name for name in dir(EmailService)
        if name.startswith("send_") and callable(getattr(EmailService, name))
    ]
    assert len(senders) >= 8, senders

    for name in senders:
        method = getattr(service, name)
        kwargs = {
            p.name: _dummy_for(p.name)
            for p in inspect.signature(method).parameters.values()
            if p.name != "to" and p.default is inspect.Parameter.empty
        }
        method("someone@example.com", **kwargs)


def _dummy_for(name: str):
    """A plausible value for a required kwarg, by name."""
    if name in {"position", "days_left", "step", "home_count", "max_homes"}:
        return 1
    return f"{name}-value"
