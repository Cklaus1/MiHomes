"""G4 · §6 Step 4 — the outbox: enqueue, drain, and the backoff ladder (A4, A5, A16).

`now` is injected everywhere (N11). **No test here sleeps**, and none may: a ladder whose last
rung is twelve hours cannot be tested against a real clock, and a test that slept for even the
first rung would make the suite a minute slower per assertion.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mihomes.models.email_outbox import BACKOFF_LADDER, MAX_ATTEMPTS, EmailOutbox
from mihomes.services.email.outbox import DrainResult, drain, enqueue, next_attempt_after
from mihomes.services.email.provider import EmailResult, EmailSendError
from mihomes.tenancy.context import account_context

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
WELCOME = {"account_name": "Belle", "dashboard_url": "https://x/", "name": None}


class FakeProvider:
    """Records sends; fails on demand so the ladder is exercisable (§9's fixture note)."""

    provider_name = "fake"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
        if self.fail:
            raise EmailSendError("provider is down")
        self.calls.append({"to": to, "subject": subject, "headers": headers})
        return EmailResult(provider_message_id="fake-1", provider=self.provider_name)


def _enqueue(session, account_id, *, klass="transactional", template="welcome", now=NOW):
    return enqueue(
        session,
        to="alex@example.com",
        template=template,
        context=WELCOME,
        klass=klass,
        account_id=account_id,
        now=now,
    )


def _rows(session):
    return list(session.execute(select(EmailOutbox)).scalars())


# --- enqueue ------------------------------------------------------------------------------


def test_enqueue_writes_a_due_now_row(session, account_a):
    with account_context(account_a):
        row = _enqueue(session, account_a)

    assert row.attempts == 0
    assert row.next_attempt_at == NOW
    assert row.sent_at is None
    assert row.failed_at is None
    assert row.klass == "transactional"
    # The render CONTEXT, not rendered html — a template fix must repair queued mail (§4.1).
    assert json.loads(row.context) == WELCOME
    assert "<" not in row.context, "the outbox must not hold rendered markup"


def test_send_queues_the_callers_own_context(session, account_a):
    """The row `_send` writes must carry the caller's data, not a placeholder.

    `test_enqueue_writes_a_due_now_row` asserts this of a direct `enqueue()` call, which
    proves nothing about the path every real message takes. Found by mutation: replacing
    `context=data` in `_send` with a literal left all fifty tests green, because the only
    context assertion was on a hand-built row.
    """
    from mihomes.services.email import EmailService

    class Recorder:
        provider_name = "rec"

        def __init__(self):
            self.subjects = []

        def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
            self.subjects.append((subject, html))
            return EmailResult(provider_message_id="r-1", provider="rec")

    provider = Recorder()
    service = EmailService(provider, session=session)

    with account_context(account_a):
        service._send("alex@example.com", "welcome", WELCOME, klass="transactional")
        row = _rows(session)[0]
        assert json.loads(row.context) == WELCOME
        assert "<" not in row.context, "the queue must hold context, never rendered markup"

        # And the render at drain time uses it: the account name reaches the rendered body.
        #
        # `now=row.next_attempt_at`, not the module's fixed NOW: `_send` stamps the row from
        # the real clock, so a NOW in the past selects nothing and this test would assert on
        # an empty list. The rest of this file drives `enqueue` directly and can pin NOW.
        service.drain(now=row.next_attempt_at)

    assert "Belle" in provider.subjects[0][1]


def test_drain_on_an_empty_queue_is_a_no_op(session, account_a):
    provider = FakeProvider()
    with account_context(account_a):
        result = drain(session, provider, now=NOW)

    assert result.total == 0
    assert provider.calls == []


def test_drain_sends_and_stamps(session, account_a):
    provider = FakeProvider()
    with account_context(account_a):
        _enqueue(session, account_a)
        result = drain(session, provider, now=NOW)

    assert result.sent == 1
    assert len(provider.calls) == 1
    row = _rows(session)[0]
    assert row.sent_at == NOW
    assert row.attempts == 1


def test_a_second_consecutive_drain_sends_nothing(session, account_a):
    """D9/A17 — idempotent by construction, not by a guard.

    A sent row is stamped in the same transaction, so the second drain's query does not
    select it. Nothing needs to remember that it already ran.
    """
    provider = FakeProvider()
    with account_context(account_a):
        _enqueue(session, account_a)
        drain(session, provider, now=NOW)
        second = drain(session, provider, now=NOW + timedelta(hours=1))

    assert second.total == 0
    assert len(provider.calls) == 1


def test_a_row_that_is_not_yet_due_is_not_selected(session, account_a):
    provider = FakeProvider()
    with account_context(account_a):
        row = _enqueue(session, account_a)
        row.next_attempt_at = NOW + timedelta(minutes=10)
        session.flush()
        result = drain(session, provider, now=NOW)

    assert result.total == 0


# --- A4/A5: failure isolation --------------------------------------------------------------


def test_retry_preserves_message(session, account_a):
    """**A4** — a provider failure reschedules; the message is not lost.

    The whole reason the outbox is a table rather than an in-process retry (D12): an
    in-process retry dies with the request, so the message is gone the moment the process is.
    """
    with account_context(account_a):
        _enqueue(session, account_a)
        result = drain(session, FakeProvider(fail=True), now=NOW)

    assert result.failed == 1
    assert result.sent == 0

    row = _rows(session)[0]
    assert row.sent_at is None, "a failed attempt must not mark the row sent"
    assert row.failed_at is None, "one failure is not exhaustion"
    assert row.attempts == 1
    assert row.next_attempt_at == NOW + BACKOFF_LADDER[0]
    assert row.last_error
    # The message itself survives intact, which is the criterion's actual claim.
    assert json.loads(row.context) == WELCOME
    assert row.to_address == "alex@example.com"


def test_send_failure_does_not_rollback(session, account_a):
    """**A5** — an email failure never rolls back its caller's transaction.

    A failed confirmation email must not undo the signup that triggered it: the user can
    request a resend, but a lost signup is unrecoverable (SPEC-001 §5.3, BILLING §2.4).
    Asserted by doing real work in the same transaction and checking it survives.
    """
    from mihomes.models.email_suppression import EmailSuppression

    marker = EmailSuppression(
        address="caller-work@example.com",
        reason="manual",
        suppressed_at=NOW,
    )
    session.add(marker)

    with account_context(account_a):
        _enqueue(session, account_a)
        drain(session, FakeProvider(fail=True), now=NOW)

    # The caller's own row is still there and the session is still usable.
    found = session.execute(
        select(EmailSuppression).where(
            EmailSuppression.address == "caller-work@example.com"
        )
    ).scalar_one_or_none()
    assert found is not None
    assert len(_rows(session)) == 1


# --- A16: the backoff ladder ---------------------------------------------------------------

#: The ladder, written out attempt by attempt, because the spec's summary does not reconcile.
#:
#: §5.3 says *"1m, 5m, 30m, 2h, 12h — five attempts, then `failed_at` is set"*: five intervals
#: for five attempts. Five attempts have only **four** gaps between them, and the fifth failure
#: is terminal rather than scheduled — so one of the two readings has to give.
#:
#: Pinned here as `(attempt_number, expected_next_attempt_at_offset)` so the test fails under
#: the reading that was not chosen. A test asserting only "eventually `failed_at` is set" would
#: pass under both, which is what makes this table the point of A16 rather than decoration.
LADDER = [
    (1, timedelta(minutes=1)),
    (2, timedelta(minutes=5)),
    (3, timedelta(minutes=30)),
    (4, timedelta(hours=2)),
    (5, None),  # terminal: failed_at set, never rescheduled
]


def test_backoff_ladder(session, account_a):
    """**A16** — the ladder runs five rungs, then the row stops being selected."""
    provider = FakeProvider(fail=True)
    now = NOW

    with account_context(account_a):
        _enqueue(session, account_a, now=now)

        for attempt, gap in LADDER:
            result = drain(session, provider, now=now)
            row = _rows(session)[0]

            assert row.attempts == attempt, f"attempt {attempt}"
            if gap is None:
                assert result.exhausted == 1, "the fifth failure is terminal"
                assert row.failed_at == now
            else:
                assert result.failed == 1, f"attempt {attempt} should reschedule"
                assert row.failed_at is None
                assert row.next_attempt_at == now + gap, f"attempt {attempt} gap"
                now = row.next_attempt_at

        # THE criterion: it stops being selected. A dead row that kept being retried would
        # hammer a failing provider forever, and one that were deleted would leave "why did
        # the customer not get their receipt" unanswerable.
        after = drain(session, provider, now=now + timedelta(days=7))
        assert after.total == 0

    row = _rows(session)[0]
    assert row.attempts == MAX_ATTEMPTS
    assert row.sent_at is None
    assert row.last_error


def test_the_fifth_interval_is_unreachable_by_construction():
    """`12h` is in the tuple and cannot be reached — asserted, not left as a puzzle.

    Kept rather than deleted so a later reader who wants six attempts finds the value already
    chosen, and so the spec's discrepancy stays visible instead of being quietly resolved.
    """
    assert len(BACKOFF_LADDER) == 5
    assert BACKOFF_LADDER[4] == timedelta(hours=12)
    assert next_attempt_after(MAX_ATTEMPTS, NOW) is None
    reachable = [next_attempt_after(n, NOW) for n in range(1, MAX_ATTEMPTS)]
    assert reachable == [NOW + gap for gap in BACKOFF_LADDER[:4]]


def test_a_recovered_provider_sends_mid_ladder(session, account_a):
    """A row rescheduled twice still sends when the provider comes back.

    Without this, a ladder that corrupted the row on each failure — losing the context, say —
    would pass every assertion above, because nothing above ever succeeds.
    """
    failing = FakeProvider(fail=True)
    with account_context(account_a):
        _enqueue(session, account_a)
        drain(session, failing, now=NOW)
        row = _rows(session)[0]
        drain(session, failing, now=row.next_attempt_at)
        row = _rows(session)[0]

        working = FakeProvider()
        result = drain(session, working, now=row.next_attempt_at)

    assert result.sent == 1
    assert len(working.calls) == 1
    row = _rows(session)[0]
    assert row.sent_at is not None
    assert row.failed_at is None
    assert row.attempts == 3


# --- suppression is re-checked at send time ------------------------------------------------


def test_suppression_is_rechecked_at_drain_not_trusted_from_enqueue(session, account_a):
    """An address can be suppressed while the mail sits in the queue.

    A bounce webhook or an unsubscribe click between enqueue and drain must stop the send —
    "it was fine when we queued it" is exactly the reasoning that mails a complainer.
    """
    from mihomes.services.email.suppression import suppress

    provider = FakeProvider()
    with account_context(account_a):
        _enqueue(session, account_a, klass="lifecycle")
        suppress(session, "alex@example.com", reason="complaint", now=NOW)
        result = drain(session, provider, now=NOW)

    assert provider.calls == []
    assert result.suppressed == 1
    row = _rows(session)[0]
    # Stamped sent so it is not retried forever, with the reason recorded.
    assert row.sent_at == NOW
    assert row.last_error == "suppressed at send time"


def test_transactional_mail_still_sends_to_a_suppressed_address(session, account_a):
    """N3 at the drain, matching the choke point's rule."""
    from mihomes.services.email.suppression import suppress

    provider = FakeProvider()
    with account_context(account_a):
        _enqueue(session, account_a, klass="transactional")
        suppress(session, "alex@example.com", reason="unsubscribe", now=NOW)
        result = drain(session, provider, now=NOW)

    assert result.sent == 1
    assert len(provider.calls) == 1


# --- render faults --------------------------------------------------------------------------


def test_a_broken_template_fails_the_row_immediately(session, account_a):
    """A render fault is our bug and no amount of retrying fixes it.

    Walking the whole ladder to reach the same conclusion twelve hours later would delay every
    other message behind it and tell nobody anything new.
    """
    provider = FakeProvider()
    with account_context(account_a):
        _enqueue(session, account_a, template="definitely_not_a_template")
        result = drain(session, provider, now=NOW)

    assert result.exhausted == 1
    assert provider.calls == []
    row = _rows(session)[0]
    assert row.failed_at == NOW
    assert row.attempts == 0, "a render fault is not a delivery attempt"
    assert "render failed" in row.last_error


def test_drain_result_counts_are_disjoint(session, account_a):
    """`total` must not double-count — it is what the CLI job reports."""
    result = DrainResult(sent=1, failed=2, exhausted=3, suppressed=4)
    assert result.total == 10


@pytest.mark.parametrize("limit", [1, 2])
def test_limit_bounds_one_drain(session, account_a, limit):
    """A worker must not be pinned by a large queue; the rest waits for the next run."""
    provider = FakeProvider()
    with account_context(account_a):
        for _ in range(3):
            _enqueue(session, account_a)
        result = drain(session, provider, limit=limit, now=NOW)

    assert result.sent == limit
