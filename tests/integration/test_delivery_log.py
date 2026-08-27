"""G3 · §6 Step 3 — the delivery log (A19).

`SAAS_PRD:168`'s "email delivery tracking" (D7). One row per message the provider accepted,
carrying the vendor's own message id so *"we never got the receipt"* can be answered with an
identifier rather than a shrug.

**Why "exactly one" is the load-bearing word.** Step 4 moves the `provider.send()` call out of
`EmailService._send` and into the outbox's `drain`, where a message may be attempted five times
before it succeeds. The write is placed immediately after the successful send so it travels with
that call unchanged — four failed rungs produce no rows, the attempt that succeeds produces one.
Writing at enqueue time instead would log a delivery for mail that never sent, which is worse
than no log: it reports success for the exact case someone is investigating.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from mihomes.models.email_delivery import EmailDelivery
from mihomes.services.email import EmailService
from mihomes.services.email.provider import EmailResult
from mihomes.tenancy.context import account_context

WELCOME = {"account_name": "Belle", "dashboard_url": "https://x/", "name": None}


class RecordingProvider:
    """Accepts the widened Protocol (D11), so a dropped kwarg cannot hide here."""

    provider_name = "recording"

    def __init__(self, message_id: str = "prov-1"):
        self.calls = []
        self.message_id = message_id

    def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
        self.calls.append(to)
        return EmailResult(
            provider_message_id=self.message_id, provider=self.provider_name
        )


class FailingProvider:
    provider_name = "failing"

    def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
        from mihomes.services.email.provider import EmailSendError

        raise EmailSendError("provider is down")


def _deliveries(session, account_id) -> list[EmailDelivery]:
    return list(
        session.execute(
            select(EmailDelivery).where(EmailDelivery.account_id == account_id)
        ).scalars()
    )


def test_one_row_per_send(session, account_a):
    """**A19** — every send writes exactly one `EmailDelivery` row, carrying the provider id."""
    provider = RecordingProvider(message_id="resend-abc123")
    service = EmailService(provider, session=session)

    with account_context(account_a):
        service._send("alex@example.com", "welcome", WELCOME, klass="transactional")

    rows = _deliveries(session, account_a)
    assert len(rows) == 1
    row = rows[0]
    assert row.to_address == "alex@example.com"
    assert row.template == "welcome"
    assert row.provider == "recording"
    assert row.provider_message_id == "resend-abc123"
    assert row.sent_at is not None
    # NULL is the normal terminal state — "accepted, no further signal from the provider" —
    # not an error and not a missing value to be filled with a default.
    assert row.status is None
    assert row.status_at is None


def test_two_sends_write_two_rows(session, account_a):
    """The counting half. Without it, a `_send` that wrote a row only on the first call —
    or one that upserted by address — would pass `test_one_row_per_send` unchanged."""
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        service._send("a@example.com", "welcome", WELCOME, klass="transactional")
        service._send("b@example.com", "welcome", WELCOME, klass="transactional")

    assert len(_deliveries(session, account_a)) == 2


def test_a_failed_send_writes_no_row(session, account_a):
    """A delivery log that records attempts is a different table (the outbox, Step 4).

    This is the assertion that keeps A19 meaning *per send*: if the row were written before
    the provider call, a provider outage would fill the log with deliveries that never
    happened — and the log's only job is answering what actually went out.
    """
    service = EmailService(FailingProvider(), session=session)

    with account_context(account_a):
        service._send("alex@example.com", "welcome", WELCOME, klass="transactional")

    assert _deliveries(session, account_a) == []


def test_a_suppressed_lifecycle_send_writes_no_row(session, account_a):
    """Suppressed mail is not sent, so there is nothing to record.

    Recording it would make the log disagree with reality in the direction that matters:
    a support query would show a message delivered to someone who never received it.
    """
    from mihomes.services.email.suppression import suppress

    address = f"user-{uuid.uuid4().hex[:12]}@example.com"
    suppress(session, address, reason="unsubscribe")
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        service._send(address, "welcome", WELCOME, klass="lifecycle")

    assert provider.calls == []
    assert _deliveries(session, account_a) == []


def test_a_template_fault_writes_no_row(session, account_a):
    """Render failures happen before the provider call, so nothing was sent."""
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        service._send("alex@example.com", "no_such_template", {}, klass="transactional")

    assert provider.calls == []
    assert _deliveries(session, account_a) == []


def test_recording_failure_does_not_fail_the_send(session, account_a, monkeypatch):
    """The message has already left; losing its record must not raise to the caller.

    Same discipline as `_send`'s own error handling (§5.3, BILLING §2.4) — turning a delivered
    email into a 500 for the request that triggered it helps nobody, and no retry can un-send
    the mail.
    """
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    def boom(*args, **kwargs):
        raise RuntimeError("delivery table is on fire")

    monkeypatch.setattr(session, "add", boom)

    with account_context(account_a):
        service._send("alex@example.com", "welcome", WELCOME, klass="transactional")

    assert provider.calls == ["alex@example.com"]


def test_no_account_bound_records_nothing_and_still_sends(session):
    """The landing app has no product session or account (SPEC-001 D1/D3).

    `current_account` raises `LookupError` when unset — the module's deliberate fail-closed
    shape — and the send must survive it, because the waitlist confirmation is the one piece
    of mail that exists before any account does.
    """
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    service._send("alex@example.com", "welcome", WELCOME, klass="transactional")

    assert provider.calls == ["alex@example.com"]
