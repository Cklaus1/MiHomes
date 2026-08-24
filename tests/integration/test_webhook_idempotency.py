"""G5 · §6 Step 5 — idempotency and out-of-order handling (A5, A7, A27).

**A27 is the one that needs a real database.** *"Two concurrent webhook deliveries of one event:
one applies."* A test that calls the handler twice in sequence proves replay-safety (A5) and says
nothing about the race — under `SELECT`-then-`INSERT` both sequential calls behave correctly and
both concurrent ones do not. So the concurrency test opens **two independent connections** and
interleaves them, which is the only arrangement where the unique constraint is what arbitrates
rather than the code's ordering.

These run against Postgres, not a mock. The dedup mechanism *is* a database constraint (N4), so a
mocked session would be testing the assertion rather than the guarantee.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from mihomes.models.account import Account
from mihomes.models.processed_webhook_event import ProcessedWebhookEvent
from mihomes.services.billing.provider import NormalizedEvent, SubscriptionState
from mihomes.services.billing.service import handle_verified_event

CUSTOMER_ID = "cus_idempotency_test"


def _event(
    event_id: str = "evt_1",
    event_type: str = "subscription.updated",
    occurred_at: datetime | None = None,
    customer_id: str = CUSTOMER_ID,
    plan: str = "pro",
) -> NormalizedEvent:
    return NormalizedEvent(
        type=event_type,
        provider_customer_id=customer_id,
        subscription=SubscriptionState(
            provider_subscription_id="sub_1",
            plan=plan,
            status="active",
            current_period_end=datetime(2026, 12, 1, tzinfo=UTC),
            cancel_at_period_end=False,
        ),
        raw_event_id=event_id,
        occurred_at=occurred_at or datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def billing_account(session, account_a):
    """An account with a Stripe customer id, so events resolve to it."""
    account = session.get(Account, account_a)
    account.stripe_customer_id = CUSTOMER_ID
    session.commit()
    return account


def _ledger_rows(session, event_id: str) -> list[ProcessedWebhookEvent]:
    return list(
        session.execute(
            select(ProcessedWebhookEvent).where(
                ProcessedWebhookEvent.provider_event_id == event_id
            )
        ).scalars()
    )


class TestIdempotency:
    def test_idempotent_replay(self, session, billing_account):
        """**A5** — the same event delivered twice applies exactly once.

        Stripe retries on any non-2xx and redelivers after network failures, so a duplicate is
        ordinary traffic. Asserted on the **ledger**, which is the thing that decides: one row
        means the second delivery was recognised, not merely that nothing crashed.
        """
        event = _event(event_id="evt_replay")

        handle_verified_event(session, event)
        handle_verified_event(session, event)

        assert len(_ledger_rows(session, "evt_replay")) == 1

    def test_two_different_events_both_apply(self, session, billing_account):
        """The positive control. Without it, a handler that ignored *everything* would pass A5.

        This is the failure mode a dedup test invites: prove that duplicates are rejected, and an
        implementation rejecting all events looks identical.
        """
        handle_verified_event(session, _event(event_id="evt_a"))
        handle_verified_event(
            session,
            _event(event_id="evt_b", occurred_at=datetime(2026, 8, 20, 13, 0, tzinfo=UTC)),
        )

        assert len(_ledger_rows(session, "evt_a")) == 1
        assert len(_ledger_rows(session, "evt_b")) == 1

    def test_unknown_customer_is_recorded_not_retried(self, session, account_a):
        """An event for a customer we cannot place is **recorded with a reason**.

        Not raising is the point: an exception would 500 the route, Stripe would retry for days,
        and the event would never become resolvable — the customer belongs to another
        environment, or the account was deleted. The row stops the redelivery; `error` explains
        why nothing was applied, so it is distinguishable from a successful application.
        """
        handle_verified_event(
            session, _event(event_id="evt_orphan", customer_id="cus_nobody"),
        )

        rows = _ledger_rows(session, "evt_orphan")
        assert len(rows) == 1
        assert rows[0].account_id is None
        assert rows[0].error is not None

    def test_applied_event_records_its_account(self, session, billing_account):
        """The resolved account is written back — this is what makes the ledger answer
        "what happened for this account", the first question in any billing incident."""
        handle_verified_event(session, _event(event_id="evt_mapped"))

        row = _ledger_rows(session, "evt_mapped")[0]
        assert row.account_id == billing_account.id
        assert row.error is None


class TestOutOfOrder:
    def test_out_of_order_dropped(self, session, billing_account):
        """**A7** — a stale `subscription.updated` does not resurrect an older state.

        The realistic shape is a retry: an event emitted at T1 fails, is retried, and lands after
        the T2 event that superseded it. Applying it would undo an upgrade or revive a cancelled
        subscription — and the account would look correct to everyone except the customer.
        """
        newer = _event(
            event_id="evt_newer",
            occurred_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
        )
        older = _event(
            event_id="evt_older",
            occurred_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        )

        handle_verified_event(session, newer)
        handle_verified_event(session, older)

        stale_row = _ledger_rows(session, "evt_older")[0]
        assert stale_row.error is not None, (
            "an event older than the last applied state must be dropped with a reason, not "
            "applied — Stripe does not guarantee ordering (BILLING §6)"
        )
        assert _ledger_rows(session, "evt_newer")[0].error is None

    def test_in_order_delivery_is_not_dropped(self, session, billing_account):
        """The other direction, and the one that would break the feature if the comparison
        were inverted — a bug a single-direction test cannot see."""
        first = _event(
            event_id="evt_first", occurred_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        )
        second = _event(
            event_id="evt_second", occurred_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
        )

        handle_verified_event(session, first)
        handle_verified_event(session, second)

        assert _ledger_rows(session, "evt_first")[0].error is None
        assert _ledger_rows(session, "evt_second")[0].error is None

    def test_a_receipt_cannot_make_a_plan_change_stale(self, session, billing_account):
        """Only state-bearing events are compared, and only against each other.

        An `invoice.paid` at 15:00 must not suppress a `subscription.updated` at 12:00: they
        answer different questions, and letting a receipt shadow a plan change would be a subtler
        version of the bug A7 guards against — one where the ordering rule itself causes the
        wrong state.
        """
        handle_verified_event(
            session,
            _event(
                event_id="evt_receipt",
                event_type="invoice.paid",
                occurred_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
            ),
        )
        handle_verified_event(
            session,
            _event(
                event_id="evt_plan",
                event_type="subscription.updated",
                occurred_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
            ),
        )

        assert _ledger_rows(session, "evt_plan")[0].error is None, (
            "an invoice event must not make a subscription event stale — different questions"
        )

    def test_a_dropped_event_does_not_become_the_reference_timestamp(
        self, session, billing_account
    ):
        """**The subtle one.** An errored row must not suppress later real events.

        Without excluding errored rows from the comparison, one stale delivery poisons the
        reference point: the dropped event's timestamp becomes "the newest applied state", and
        every subsequent event older than *it* is dropped too. The failure compounds silently and
        looks like webhooks having stopped working.
        """
        handle_verified_event(
            session,
            _event(event_id="evt_x", occurred_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC)),
        )
        # Dropped as stale, and errored.
        handle_verified_event(
            session,
            _event(event_id="evt_stale", occurred_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC)),
        )
        # Newer than everything applied. Must survive.
        handle_verified_event(
            session,
            _event(event_id="evt_latest", occurred_at=datetime(2026, 8, 20, 18, 0, tzinfo=UTC)),
        )

        assert _ledger_rows(session, "evt_latest")[0].error is None

    def test_identical_timestamps_are_not_stale(self, session, billing_account):
        """Ties are unordered by definition; dropping one would be an arbitrary choice dressed
        as a rule. Idempotency already guarantees each is applied at most once."""
        when = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        handle_verified_event(session, _event(event_id="evt_tie_a", occurred_at=when))
        handle_verified_event(session, _event(event_id="evt_tie_b", occurred_at=when))

        assert _ledger_rows(session, "evt_tie_b")[0].error is None


class TestConcurrentDelivery:
    def test_concurrent_delivery(self, _pg_engine, account_a):
        """**A27** — two concurrent deliveries of one event apply exactly once.

        **This test was written wrong the first time, and the mutation check caught it.** The
        first version called `handle_verified_event` twice across two sessions, sequentially:
        session one committed before session two began, so session two simply saw an existing row
        and no race ever occurred. Replacing insert-first with the `SELECT`-then-`INSERT` that N4
        forbids left all twelve tests green — the exact defect the criterion exists to prevent,
        undetected.

        The fix is genuine overlap: **both threads run the full handler at the same time**, past
        a barrier that guarantees neither has committed when the other starts. Postgres blocks
        the second insert until the first commits, then raises a unique violation. Under
        check-then-insert both threads read "not present" inside their own snapshots and both
        proceed.

        **Two threads alone were not enough, and that is the second thing this test records.**
        Racing two full handlers left the mutant green too: the window between one thread's
        `SELECT` and its `COMMIT` is microseconds, so the two serialize by luck almost every
        time. A race test that only *usually* races is a flaky test at best and a decorative one
        at worst.

        So the overlap is **forced rather than hoped for**. A second connection inserts the same
        `(provider, provider_event_id)` and holds the transaction open, uncommitted. The handler
        then runs while that row is invisible to it — exactly the state a concurrent delivery
        produces mid-flight. Insert-first collides with it, blocks, and resolves to a unique
        violation it handles. Check-then-insert sees nothing, decides to proceed, and hits the
        same violation **unhandled** — which is the failure this criterion is about.
        """
        import threading
        import time

        from sqlalchemy.orm import Session as OrmSession

        with OrmSession(_pg_engine) as setup:
            account = setup.get(Account, account_a)
            account.stripe_customer_id = "cus_concurrent"
            setup.commit()

        event = _event(event_id="evt_concurrent", customer_id="cus_concurrent")

        # **Two error lists, and the split is the whole correctness of this test.**
        #
        # The rival is a raw fixture with no dedup handling, so if *it* loses the race its commit
        # raises — which is expected, not a defect. Collecting both in one list made this test
        # fail in the full suite while passing alone: under load the handler sometimes wins, the
        # rival's commit then violates the constraint, and the assertion read that as "the
        # handler failed to deduplicate". The claim under test is only ever about the handler.
        handler_errors: list[BaseException] = []
        rival_errors: list[BaseException] = []
        rival_committed = threading.Event()
        handler_started = threading.Event()

        def rival_delivery() -> None:
            """The competing delivery: insert, hold the transaction open, then commit.

            Holding it open is what makes the race deterministic — the handler runs against a
            database where the row exists but is not yet visible to it, which is precisely the
            mid-flight state of a genuine concurrent delivery.
            """
            try:
                with OrmSession(_pg_engine) as rival:
                    rival.add(
                        ProcessedWebhookEvent(
                            provider="stripe",
                            provider_event_id=event.raw_event_id,
                            event_type=event.type,
                            occurred_at=event.occurred_at,
                            processed_at=datetime.now(UTC),
                        )
                    )
                    rival.flush()  # row exists in this transaction, invisible to others
                    handler_started.wait(timeout=10)
                    # **Hold past the handler's read.** Without this the rival commits the
                    # instant the handler starts, the row becomes visible, and the handler
                    # simply sees a duplicate — no race, and the mutant survives. The hold is
                    # what puts the handler's SELECT *inside* the uncommitted window.
                    #
                    # It cannot wait for the handler to *finish*: insert-first blocks on this
                    # very row and only unblocks when this commits, so waiting would deadlock.
                    # A fixed window is the one arrangement that works for both implementations.
                    time.sleep(0.5)
                    rival.commit()
                    rival_committed.set()
            except BaseException as exc:  # noqa: BLE001 - expected when the rival loses
                rival_errors.append(exc)
                rival_committed.set()

        rival = threading.Thread(target=rival_delivery)
        rival.start()

        try:
            with OrmSession(_pg_engine) as worker:
                handler_started.set()
                handle_verified_event(worker, event)
        except BaseException as exc:  # noqa: BLE001 - the mutant's failure lands here
            handler_errors.append(exc)

        rival.join(timeout=30)

        with OrmSession(_pg_engine) as check:
            count = check.execute(
                select(func.count())
                .select_from(ProcessedWebhookEvent)
                .where(ProcessedWebhookEvent.provider_event_id == "evt_concurrent")
            ).scalar_one()

            # Clean up — this test commits outside the rolled-back `session` fixture.
            check.query(ProcessedWebhookEvent).filter(
                ProcessedWebhookEvent.provider_event_id == "evt_concurrent"
            ).delete()
            account = check.get(Account, account_a)
            account.stripe_customer_id = None
            check.commit()

        assert not handler_errors, (
            "the handler must absorb a concurrent duplicate, not raise — insert-first treats "
            f"the unique violation as the dedup signal (N4). Raised: {handler_errors}"
        )
        assert count == 1, (
            "the (provider, provider_event_id) unique constraint must arbitrate concurrent "
            "deliveries — check-then-insert lets both through (N4)"
        )
        # `rival_errors` is deliberately not asserted on: whichever side loses the race is
        # arbitrary, and the raw fixture has no dedup handling by design. Exactly one row is the
        # invariant; who wrote it is not.


class TestLedgerSurvivesFailure:
    def test_the_ledger_row_is_committed_before_processing(self, session, billing_account):
        """Recording that we **saw** an event and recording what we **did** with it are
        different facts with different lifetimes.

        In one transaction, a rollback during processing takes the ledger row with it, and the
        retry that follows finds no record and reprocesses — reintroducing exactly what the
        ledger exists to prevent. Proven by the unknown-customer path: nothing is applied, and
        the row still persists with its reason.
        """
        handle_verified_event(
            session,
            _event(event_id="evt_survives", customer_id="cus_nobody_here"),
        )
        session.rollback()

        rows = _ledger_rows(session, "evt_survives")
        assert len(rows) == 1, (
            "the ledger row must be committed independently — a rollback in processing must "
            "not erase the record that the event was seen"
        )


class TestStaleWindow:
    def test_a_much_later_event_is_never_stale(self, session, billing_account):
        """A sanity anchor on the comparison: hours later, still fine."""
        handle_verified_event(
            session,
            _event(event_id="evt_base", occurred_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC)),
        )
        handle_verified_event(
            session,
            _event(
                event_id="evt_much_later",
                occurred_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC) + timedelta(days=30),
            ),
        )
        assert _ledger_rows(session, "evt_much_later")[0].error is None
