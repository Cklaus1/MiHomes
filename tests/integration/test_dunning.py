"""G10 · §6 Step 10 — the dunning ladder (A23, A24).

Phase 3 sends **one** `payment_failed` and stops (SPEC-004 B2). This is the escalating
remainder: three further rungs on a schedule, cancelled the moment the customer pays.

**No new table.** `EmailOutbox` already carries `next_attempt_at`, `klass`, `template` and
`context` — a row due in seven days *is* a scheduled send. So the ladder enqueues four rows and
`drain-outbox` sends each as it comes due, which makes A23 close to structural.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from mihomes.models.email_outbox import EmailOutbox
from mihomes.services.billing.dunning import (
    CANCELLED_MARKER,
    LADDER,
    cancel_ladder,
    pending_rungs,
    start_ladder,
)
from mihomes.services.email import EmailService
from mihomes.services.email.provider import EmailResult
from mihomes.tenancy.context import account_context

NOW = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)

#: The schedule, written out rung by rung.
#:
#: **"Four rows exist" is not the criterion.** That assertion passes with all four due
#: immediately — four near-identical warnings in one minute, which reads as a bug to the
#: customer and as a burst to their mailbox provider. A23 is about *when*, so the offsets are
#: pinned here and compared exactly, the same discipline as the outbox's backoff ladder.
EXPECTED_SCHEDULE = [
    ("payment_failed", timedelta(0)),
    ("dunning_2", timedelta(days=3)),
    ("dunning_3", timedelta(days=7)),
    ("dunning_final", timedelta(days=14)),
]


class RecordingProvider:
    provider_name = "recording"

    def __init__(self):
        self.sent = []

    def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
        self.sent.append({"subject": subject, "headers": headers})
        return EmailResult(provider_message_id="rec-1", provider=self.provider_name)


def _start(session, account_id, *, now=NOW):
    return start_ladder(
        session,
        to=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        account_id=account_id,
        plan="pro",
        billing_url="https://app.mihomes.ai/billing",
        now=now,
    )


def _queued(session, account_id):
    return list(
        session.execute(
            sa.select(EmailOutbox)
            .where(EmailOutbox.account_id == account_id)
            .order_by(EmailOutbox.next_attempt_at)
        ).scalars()
    )


# --- A23: one immediately, the rest on schedule ---------------------------------------------


def test_ladder_schedule(session, account_a):
    """**A23** — one `invoice.payment_failed` produces one email immediately and the rest on
    schedule.

    Compared against `EXPECTED_SCHEDULE` pair by pair, so a ladder that fired everything at once
    — or reordered the rungs, or lost the last one — fails on the specific rung rather than on a
    count.
    """
    with account_context(account_a):
        _start(session, account_a)

    rows = _queued(session, account_a)
    assert len(rows) == len(EXPECTED_SCHEDULE)

    for row, (template, offset) in zip(rows, EXPECTED_SCHEDULE, strict=True):
        assert row.template == template
        assert row.next_attempt_at == NOW + offset, (
            f"{template} is due at {row.next_attempt_at}, expected {NOW + offset}"
        )


def test_only_the_first_rung_is_due_immediately(session, account_a):
    """The half that makes "immediately" mean something.

    A drain at the moment of failure must send exactly one message. Without this, a ladder that
    scheduled every rung for `now` would still satisfy a test that only checked the stored
    offsets in the row.
    """
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        _start(session, account_a)
        service.drain(now=NOW)

    assert len(provider.sent) == 1, (
        f"exactly one email at the moment of failure; got {len(provider.sent)}"
    )


def test_the_later_rungs_send_when_they_come_due(session, account_a):
    """And the rest arrive. A ladder that queued three rows nothing ever sent would pass every
    assertion above."""
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        _start(session, account_a)
        for _, offset in EXPECTED_SCHEDULE:
            service.drain(now=NOW + offset)

    assert len(provider.sent) == len(EXPECTED_SCHEDULE)


def test_the_ladder_escalates(session, account_a):
    """Each rung says something the previous one could not.

    Asserted on the rendered subjects, because the escalation is the product: rung 2 reports the
    retry already happened, rung 3 names the consequence, rung 4 reports a state that has
    changed. Four copies of the same email would pass every structural test in this file.
    """
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        _start(session, account_a)
        for _, offset in EXPECTED_SCHEDULE:
            service.drain(now=NOW + offset)

    subjects = [call["subject"] for call in provider.sent]
    assert len(set(subjects)) == len(subjects), f"rungs must differ: {subjects}"
    assert "read-only" in subjects[-1].lower(), (
        f"the final rung reports the state change: {subjects[-1]!r}"
    )


# --- A24: recovery stops it ------------------------------------------------------------------


def test_recovery_stops_ladder(session, account_a):
    """**A24** — recovery mid-ladder stops the sequence.

    **The drain runs past every remaining rung's due time.** Draining at `now` proves nothing:
    those rows were not due anyway, so "stopped" and "not yet due" would be indistinguishable.
    The clock is advanced beyond the final rung, where an uncancelled ladder would send three
    more emails.
    """
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        _start(session, account_a)
        service.drain(now=NOW)          # rung 1 goes out
        assert len(provider.sent) == 1

        cancelled = cancel_ladder(session, account_a, now=NOW + timedelta(hours=1))
        assert cancelled == 3, "the three unsent rungs must be cancelled"

        # Well past the last rung. An uncancelled ladder sends three more here.
        service.drain(now=NOW + timedelta(days=30))

    assert len(provider.sent) == 1, (
        f"recovery must stop the sequence; {len(provider.sent)} emails went out"
    )


def test_a_cancelled_rung_is_kept_and_says_why(session, account_a):
    """The outbox's rule is that a dead row is **kept** — "why did the customer not get the
    final notice" is a support question, and a deleted row answers it with silence.

    `sent_at` alone cannot distinguish "we chose not to send this" from "this was sent", which
    is what the marker is for. Same shape as the drain's `"suppressed at send time"`.
    """
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        _start(session, account_a)
        # Rung 1 goes out first, so the three that remain are the ones recovery cancels —
        # without the drain all four are unsent and the count is 4, which is a different claim.
        service.drain(now=NOW)
        cancel_ladder(session, account_a, now=NOW + timedelta(hours=1))

    rows = _queued(session, account_a)
    assert len(rows) == len(EXPECTED_SCHEDULE), "cancelled rungs are kept, never deleted"

    cancelled = [r for r in rows if r.last_error == CANCELLED_MARKER]
    assert len(cancelled) == 3
    assert all(r.sent_at is not None for r in cancelled), (
        "a cancelled rung must stop being selected"
    )


def test_cancelling_twice_cancels_nothing_the_second_time(session, account_a):
    """Two recovery signals for one payment is an ordinary case: Stripe sends `invoice.paid`
    and a `subscription.updated` for the same recovery."""
    with account_context(account_a):
        _start(session, account_a)
        # Nothing drained, so all four rungs are still pending.
        assert cancel_ladder(session, account_a, now=NOW) == 4
        assert cancel_ladder(session, account_a, now=NOW) == 0


def test_cancelling_does_not_recall_what_already_went_out(session, account_a):
    """What stops is everything still queued — the only part still in our control.

    A customer who paid after the second rung should not receive the third; they have already
    received the first two, and pretending otherwise is not a thing software can do.
    """
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        _start(session, account_a)
        service.drain(now=NOW + timedelta(days=3))   # rungs 1 and 2
        assert len(provider.sent) == 2

        assert cancel_ladder(session, account_a, now=NOW + timedelta(days=4)) == 2

    assert len(provider.sent) == 2


def test_pending_rungs_reports_what_would_be_cancelled(session, account_a):
    """The accessor the recovery path uses. Empty for an account with no ladder."""
    with account_context(account_a):
        assert pending_rungs(session, account_a) == []
        _start(session, account_a)
        assert len(pending_rungs(session, account_a)) == 4


# --- the class, which is where this departs from the spec ------------------------------------


def test_every_rung_is_transactional(session, account_a):
    """**§5.2 lists `send_dunning` as lifecycle. This ships transactional** — harness D15.

    D13's own criterion is *"a receipt for money taken is not marketing … a drip is"*, and the
    discriminating question is whether a **suppressed** address needs rungs 2–4. It does: under
    D13 suppression is absolute for lifecycle mail, so an unsubscribed customer would be told
    once that their card had failed and then silenced while their access lapsed.

    It also has to match rung 1, which SPEC-005 G2 classified transactional for that exact
    reason. One sequence, one class.
    """
    with account_context(account_a):
        _start(session, account_a)

    rows = _queued(session, account_a)
    assert {row.klass for row in rows} == {"transactional"}


def test_no_rung_carries_an_unsubscribe_header(session, account_a):
    """The consequence of the class, asserted where a customer would see it (A18).

    `List-Unsubscribe` on "your payment failed" invites someone to opt out of a warning. It is
    also counted against the sender by mailbox providers, so it is wrong and costly at once.
    """
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        _start(session, account_a)
        for _, offset in EXPECTED_SCHEDULE:
            service.drain(now=NOW + offset)

    assert all(not call["headers"] for call in provider.sent), (
        "a dunning notice must not offer to unsubscribe from dunning notices"
    )


def test_a_suppressed_address_still_receives_the_ladder(session, account_a):
    """The point of the classification, stated as behaviour.

    Someone who unsubscribed from marketing must still learn their payment failed — otherwise
    they lose access having been told nothing, which is the failure the transactional class
    exists to prevent.
    """
    from mihomes.services.email.suppression import suppress

    address = f"unsub-{uuid.uuid4().hex[:8]}@example.com"
    provider = RecordingProvider()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        suppress(session, address, reason="unsubscribe")
        start_ladder(
            session,
            to=address,
            account_id=account_a,
            plan="pro",
            billing_url="https://app.mihomes.ai/billing",
            now=NOW,
        )
        service.drain(now=NOW)

    assert len(provider.sent) == 1, (
        "an unsubscribed customer must still be told their payment failed"
    )


@pytest.mark.parametrize("template", [t for t, _ in EXPECTED_SCHEDULE])
def test_every_rung_has_both_templates(template):
    """A missing `.txt` is a mail that renders as an empty body in a text-only client."""
    from mihomes.services.email.render import render_template

    subject, html, text = render_template(
        template, {"plan": "pro", "billing_url": "https://x/", "grace_days": 7}
    )
    assert subject and html and text
    assert "https://x/" in html and "https://x/" in text


def test_the_ladder_constant_and_the_test_schedule_agree():
    """The test's expectations are written out (see `EXPECTED_SCHEDULE`), so they must be
    checked against the implementation rather than copied from it at run time."""
    assert list(LADDER) == EXPECTED_SCHEDULE


# --- the webhook seam: a real event starts and stops the ladder -------------------------------


def _payment_event(customer_id: str, event_type: str, event_id: str, status: str = "past_due"):
    """A `NormalizedEvent` as the adapter would produce one."""
    from mihomes.services.billing.provider import NormalizedEvent, SubscriptionState

    return NormalizedEvent(
        type=event_type,
        provider_customer_id=customer_id,
        subscription=SubscriptionState(
            provider_subscription_id="sub_dunning",
            plan="pro",
            status=status,
            current_period_end=datetime(2026, 12, 1, tzinfo=UTC),
            cancel_at_period_end=False,
        ),
        raw_event_id=event_id,
        occurred_at=NOW,
    )


@pytest.fixture
def billing_account(session, account_a):
    """An account with a Stripe customer id and an owner, so events resolve and can be mailed."""
    from sqlalchemy import text as sa_text

    from mihomes.models.account import Account

    customer_id = f"cus_dunning_{uuid.uuid4().hex[:8]}"
    account = session.get(Account, account_a)
    account.stripe_customer_id = customer_id

    user_id = uuid.uuid4()
    session.execute(
        sa_text(
            "INSERT INTO users (id, google_sub, email, name, created_at) "
            "VALUES (:id, :sub, :email, 'Dunning Owner', now())"
        ),
        {"id": user_id, "sub": str(user_id), "email": f"{user_id}@example.com"},
    )
    session.execute(
        sa_text(
            "INSERT INTO memberships (id, account_id, user_id, role, status, created_at) "
            "VALUES (:id, :a, :u, 'owner', 'active', now())"
        ),
        {"id": uuid.uuid4(), "a": account_a, "u": user_id},
    )
    session.flush()
    return customer_id


def test_a_payment_failure_webhook_starts_the_ladder(session, account_a, billing_account):
    """The seam SPEC-004 left open.

    Its `send_payment_failed` existed with no caller — no email was wired to any billing event.
    A23 says *"a single `invoice.payment_failed` produces one email immediately and the rest on
    schedule"*, which needs the webhook to actually start something.
    """
    from mihomes.services.billing.service import handle_verified_event

    with account_context(account_a):
        handle_verified_event(
            session,
            _payment_event(billing_account, "invoice.payment_failed", "evt_dun_1"),
        )

    rows = _queued(session, account_a)
    assert len(rows) == len(EXPECTED_SCHEDULE), (
        f"a payment failure must start the whole ladder; queued {len(rows)}"
    )
    assert rows[0].template == "payment_failed"


def test_a_paid_invoice_cancels_the_ladder(session, account_a, billing_account):
    """**A24 at the webhook** — recovery arrives as an event, not a function call."""
    from mihomes.services.billing.service import handle_verified_event

    with account_context(account_a):
        handle_verified_event(
            session,
            _payment_event(billing_account, "invoice.payment_failed", "evt_dun_2"),
        )
        assert len(pending_rungs(session, account_a)) == 4

        handle_verified_event(
            session,
            _payment_event(billing_account, "invoice.paid", "evt_dun_3", status="active"),
        )

    assert pending_rungs(session, account_a) == []


def test_a_subscription_still_past_due_does_not_cancel(session, account_a, billing_account):
    """A customer mid-grace has not recovered.

    Stripe emits `subscription.updated` for reasons that have nothing to do with payment, and
    one arriving while the status is still `past_due` must leave the remaining rungs alone —
    otherwise a routine update silently cancels the sequence and the customer is never told
    again.
    """
    from mihomes.services.billing.service import handle_verified_event

    with account_context(account_a):
        handle_verified_event(
            session,
            _payment_event(billing_account, "invoice.payment_failed", "evt_dun_4"),
        )
        handle_verified_event(
            session,
            _payment_event(
                billing_account,
                "customer.subscription.updated",
                "evt_dun_5",
                status="past_due",
            ),
        )

    assert len(pending_rungs(session, account_a)) == 4, (
        "a subscription still in past_due has not recovered"
    )


def test_a_mail_failure_does_not_fail_the_webhook(session, account_a, billing_account, monkeypatch):
    """The billing state is already committed; a mail problem must not cost the ack.

    Stripe stops redelivering an acked event, so a webhook that raises after applying state
    leaves the account's plan and Stripe's view of it out of step — over an email.
    """
    from mihomes.services.billing import service as billing_service
    from mihomes.services.billing.service import handle_verified_event

    def boom(*args, **kwargs):
        raise RuntimeError("mail is down")

    monkeypatch.setattr(billing_service, "_billing_email", boom)

    with account_context(account_a):
        handle_verified_event(
            session,
            _payment_event(billing_account, "invoice.payment_failed", "evt_dun_6"),
        )

    assert _queued(session, account_a) == [], "nothing queued, and no exception either"


def test_the_ladder_does_not_outlive_the_subscription(session, account_a, billing_account):
    """Step 10's **third** verify clause: *"the ladder never outlives the subscription that
    started it."*

    **No §8 criterion covers this.** A23 is the schedule and A24 is recovery; a customer who
    cancels mid-ladder is neither, so Step 10 would have shipped with this clause unbuilt and
    F.3a green — the same gap G7's owner-only route had. Recorded as harness D16.

    Two more weeks of "update your card to keep MiHomes" after someone has cancelled is dunning
    a person who is no longer a customer.
    """
    from mihomes.services.billing.service import handle_verified_event

    with account_context(account_a):
        handle_verified_event(
            session,
            _payment_event(billing_account, "invoice.payment_failed", "evt_dun_7"),
        )
        assert len(pending_rungs(session, account_a)) == 4

        handle_verified_event(
            session,
            _payment_event(
                billing_account, "subscription.cancelled", "evt_dun_8", status="canceled"
            ),
        )

    assert pending_rungs(session, account_a) == [], (
        "a cancelled subscription must end the ladder that its failure started"
    )
