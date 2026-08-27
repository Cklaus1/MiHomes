"""G3 · §6 Step 3 — the delivery log (A19).

`SAAS_PRD:168`'s "email delivery tracking" (D7). One row per message the provider accepted,
carrying the vendor's own message id so *"we never got the receipt"* can be answered with an
identifier rather than a shrug.

**Why "exactly one" is the load-bearing word.** Step 4 has now moved the `provider.send()`
call out of `EmailService._send` and into the outbox's `drain`, where a message may be attempted
five times before it succeeds. The write travelled with it unchanged, exactly as this file said
it would — four failed rungs produce no rows, the attempt that succeeds produces one.

So every test here drains. `_send` alone writes nothing now, and a test that asserted on the
delivery table straight after `_send` would be asserting that an enqueue does not write a
delivery row — true, and not what A19 is about.
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
        service.drain()

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
        service.drain()

    assert len(_deliveries(session, account_a)) == 2


def test_a_failed_send_writes_no_row(session, account_a):
    """A delivery log that records attempts is a different table (the outbox).

    This is the assertion that keeps A19 meaning *per send*: if the row were written before
    the provider call, a provider outage would fill the log with deliveries that never
    happened — and the log's only job is answering what actually went out.
    """
    service = EmailService(FailingProvider(), session=session)

    with account_context(account_a):
        service._send("alex@example.com", "welcome", WELCOME, klass="transactional")
        service.drain()

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
        service.drain()

    assert provider.calls == []
    assert _deliveries(session, account_a) == []


def test_a_template_fault_writes_no_row(session, account_a):
    """Render failures happen before the provider call, so nothing was sent."""
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        service._send("alex@example.com", "no_such_template", {}, klass="transactional")
        service.drain()

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

    # Patched on the CLASS, not the instance: `drain` builds its callback from
    # `self._record_delivery` at call time, so an instance attribute set beforehand would be
    # shadowed by the bound method the lambda resolves.
    monkeypatch.setattr(EmailService, "_record_delivery", boom)

    with account_context(account_a):
        service._send("alex@example.com", "welcome", WELCOME, klass="transactional")
        service.drain()

    # The message left. The record of it did not — and that must not raise to the caller.
    assert provider.calls == ["alex@example.com"]


def test_no_session_sends_inline_and_records_nothing():
    """The landing app has no product session at all (SPEC-001 D1/D3).

    Its waitlist confirmation is the one piece of mail that exists before any account does,
    so it cannot be queued — an outbox row with no owner could never be drained, because RLS
    would never select it. `_send` takes the inline path and the message still goes out.

    **The first version of this test took the `session` fixture and asserted "no account
    bound".** That fixture binds `account_a` for the whole test body, so the branch it meant
    to exercise was unreachable and the test was measuring the wrong thing entirely.
    """
    provider = RecordingProvider()
    service = EmailService(provider, session=None)

    service._send("alex@example.com", "welcome", WELCOME, klass="transactional")

    assert provider.calls == ["alex@example.com"]


def test_four_failed_attempts_and_one_success_write_exactly_one_row(session, account_a):
    """A19 across the whole ladder — the case that only exists once Step 4 lands.

    Four rungs fail, the fifth succeeds, and the log holds one row. A write placed on each
    *attempt* rather than each *send* would put four phantom deliveries here, each looking to
    a support engineer like mail the customer should have received.
    """
    from mihomes.services.email.provider import EmailSendError

    class FlakyProvider:
        provider_name = "flaky"

        def __init__(self):
            self.attempts = 0

        def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
            self.attempts += 1
            if self.attempts <= 4:
                raise EmailSendError("still down")
            return EmailResult(provider_message_id="late-1", provider=self.provider_name)

    from mihomes.models.email_outbox import EmailOutbox

    provider = FlakyProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        service._send("alex@example.com", "welcome", WELCOME, klass="transactional")
        now = None
        for _ in range(5):
            service.drain(now=now)
            row = session.execute(select(EmailOutbox)).scalars().first()
            now = row.next_attempt_at

    assert provider.attempts == 5
    rows = _deliveries(session, account_a)
    assert len(rows) == 1
    assert rows[0].provider_message_id == "late-1"
