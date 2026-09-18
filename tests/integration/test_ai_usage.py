"""G10 · §6 Step 10 — the meter, against a real database (A13).

Complements `tests/unit/test_ai_metering.py`, which is static: A10/A11/A12 assert *structure* —
which modules construct clients, which pass an `entry_point`, what is cached — and those are AST
questions. This file asserts **behaviour**: does the counter actually move, and does it survive
what the tree does to it.

**A13 is the one that justifies D18's existence.** *"Archiving does not reduce `calls_used`."*
`archive.py:191-199` DELETEs `ai_conversations` rows, so a counter derived from that table would
silently reset a customer's usage the moment they tidied up — a billing defect that looks like
generosity until someone reconciles the invoice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from mihomes.models.account import Account
from mihomes.models.ai_conversation import AIConversation
from mihomes.models.ai_usage import AIUsageEvent, AIUsageRollup
from mihomes.services.metering.meter import billing_period, current_usage, record_usage


class TestTheCeilingReachesTheUser:
    """The ceiling denies, and the denial arrives as words a user can act on.

    The gate itself was never the problem — `check_and_reserve` returns a `Denied` carrying
    *"AI paused until <date> — N of N calls used this period"* and an `upgrade_target`. But
    every AI route wraps its call in `except Exception` and renders `_ai_error(str(e))`, whose
    fallback is *"AI request failed: …"* — the phrasing it uses for a broken provider. So
    reaching your monthly limit read as the AI being down, and the upgrade target was discarded.
    """

    def test_the_ceiling_denies_at_the_cap(self, session, account_a):
        """Reachable at all — the meter fails *open* on an infrastructure error, so a message
        wired for a denial that never arrives would be coverage-shaped dead code.

        Moves the fixture's own account to `free` rather than creating a second one: the
        `session` fixture is bound to `account_a`, so a rollup written for any other account is
        invisible to it (measured — the first version of this test wrote 200 calls somewhere the
        meter could not see and then failed looking for them).
        """
        from mihomes.entitlements.limits import PLAN_LIMITS
        from mihomes.entitlements.service import Denied
        from mihomes.services.metering.meter import check_and_reserve

        account = session.get(Account, account_a)
        account.plan = "free"
        account.subscription_status = "active"
        session.flush()

        cap = PLAN_LIMITS["free"]["ai_calls_per_month"]

        assert not isinstance(
            check_and_reserve(session, account, entry_point="web.agent"), Denied
        ), "a fresh Free account must be allowed to use AI"

        for _ in range(cap):
            record_usage(session, account, entry_point="web.agent", provider="Claude",
                         method="complete")

        decision = check_and_reserve(session, account, entry_point="web.agent")
        assert isinstance(decision, Denied), f"{cap} of {cap} used and still allowed"
        assert decision.upgrade_target == "pro"

    def test_the_message_names_the_limit_and_the_plan(self):
        """What the user actually reads in the chat bubble.

        Asserts against `_plan_message` rather than the raw `Denied`, because the bug was in the
        rendering, not the decision: the route had the right object and said the wrong thing.
        """
        from mihomes.entitlements.service import Denied
        from mihomes.services.property import EntitlementError
        from mihomes.web.routes.ai import _ai_error, _plan_message

        decision = Denied(
            reason="AI paused until 2026-09-30 — 200 of 200 calls used this period.",
            upgrade_target="pro",
            limit=200,
        )
        message = _plan_message(EntitlementError(decision))

        assert "200 of 200" in message, "the user needs the numbers to know what happened"
        assert "Pro" in message, "PRICING rule 4 — name the plan that would allow it"
        assert "/billing" in message, "and give them somewhere to go"

        # The regression in one line: the old path sent this through `_ai_error`, which has no
        # case for a plan denial and falls through to its provider-failure wording.
        assert _ai_error(str(decision.reason)).startswith("AI request failed"), (
            "if this no longer holds, `_ai_error` has grown a plan case and `_plan_message` "
            "may be redundant"
        )


class TestRecordUsage:
    def test_the_counter_moves(self, session, account_a):
        account = session.get(Account, account_a)

        record_usage(session, account, entry_point="web.agent", provider="Claude",
                     method="complete")

        assert current_usage(session, account) == 1

    def test_each_call_writes_an_event(self, session, account_a):
        """The event log is the audit trail A11 rests on — `entry_point` is how a later reader
        proves which paths were metered, so the row has to exist per call, not per period."""
        account = session.get(Account, account_a)

        record_usage(session, account, entry_point="cli.ai", provider="Claude", method="stream")
        record_usage(session, account, entry_point="gateway.review", provider="Claude",
                     method="structured_output")

        events = session.query(AIUsageEvent).filter(
            AIUsageEvent.account_id == account.id
        ).all()
        assert {e.entry_point for e in events} == {"cli.ai", "gateway.review"}
        assert current_usage(session, account) == 2

    def test_one_rollup_per_period(self, session, account_a):
        """Many calls, one counter row — the point of materializing (D18)."""
        account = session.get(Account, account_a)

        for _ in range(5):
            record_usage(session, account, entry_point="web.agent", provider="Claude",
                         method="complete")

        rollups = session.query(AIUsageRollup).filter(
            AIUsageRollup.account_id == account.id
        ).all()
        assert len(rollups) == 1
        assert rollups[0].calls_used == 5

    def test_reading_usage_creates_nothing(self, session, account_a):
        """`current_usage` must not write.

        A dashboard render would otherwise provision a rollup row for every account that never
        made a call — and those rows would then be indistinguishable from real periods with zero
        usage, which matters the moment anyone counts active accounts.
        """
        account = session.get(Account, account_a)

        assert current_usage(session, account) == 0
        assert session.query(AIUsageRollup).filter(
            AIUsageRollup.account_id == account.id
        ).count() == 0


class TestArchiveDoesNotResetUsage:
    def test_archive_preserves_usage(self, session, account_a):
        """**A13** — the criterion that justifies materializing the counter.

        Deletes `ai_conversations` rows exactly as `archive.py` does, and requires the counter to
        be untouched. A derived count would drop to zero here, and the customer would get their
        quota back for free every time they archived.
        """
        account = session.get(Account, account_a)

        session.add(AIConversation(
            session_id="s1", role="general", user_message="q", ai_response="a",
            provider="claude", model="claude-sonnet-5",
        ))
        session.commit()

        record_usage(session, account, entry_point="web.agent", provider="Claude",
                     method="complete")
        record_usage(session, account, entry_point="web.agent", provider="Claude",
                     method="complete")
        before = current_usage(session, account)
        assert before == 2

        # What archive.py does (`:191-199`): select, copy, DELETE.
        session.query(AIConversation).delete()
        session.commit()

        # **The guard on A13**, inline rather than as its own test: if the DELETE silently did
        # nothing, the assertion below would pass while proving the opposite of what it claims.
        assert session.query(AIConversation).count() == 0, (
            "the archive DELETE did not run — A13's assertion would then be vacuous"
        )
        assert current_usage(session, account) == before, (
            "archiving must not reduce calls_used — a derived counter would reset here, "
            "handing the customer their quota back (D18/F10/A13)"
        )


class TestBillingPeriod:
    def test_free_account_uses_the_calendar_month(self, session, account_a):
        """D4 — no subscription object, so no anniversary to anchor to."""
        account = session.get(Account, account_a)
        account.current_period_end = None

        start, end = billing_period(account, today=date(2026, 8, 15))
        assert start == date(2026, 8, 1)
        assert end == date(2026, 8, 31)

    def test_a_subscription_anchors_to_its_billing_day(self, session, account_a):
        """§5.1 — a Pro customer who subscribed on the 20th resets on the 20th.

        Anchoring to the calendar month instead would give them a partial first period and a free
        stretch at every boundary, which is a real revenue leak rather than a rounding detail.
        """
        account = session.get(Account, account_a)
        account.current_period_end = datetime(2026, 9, 20, tzinfo=UTC)

        start, end = billing_period(account, today=date(2026, 8, 25))
        assert start == date(2026, 8, 20)
        assert end == date(2026, 9, 19)

    def test_before_the_anchor_the_period_began_last_month(self, session, account_a):
        account = session.get(Account, account_a)
        account.current_period_end = datetime(2026, 9, 20, tzinfo=UTC)

        start, end = billing_period(account, today=date(2026, 8, 5))
        assert start == date(2026, 7, 20)
        assert end == date(2026, 8, 19)

    def test_a_31st_anchor_survives_february(self, session, account_a):
        """Month arithmetic without `dateutil`, which is not a dependency.

        A naive `date(year, month, 31)` raises in February — and it would raise inside
        `record_usage`, on the hot path of every AI call, for one customer in twelve every
        February. Clamped to the month's length instead.
        """
        account = session.get(Account, account_a)
        account.current_period_end = datetime(2026, 3, 31, tzinfo=UTC)

        start, end = billing_period(account, today=date(2026, 2, 15))
        assert start == date(2026, 1, 31)
        assert end == date(2026, 2, 27)

    def test_usage_is_scoped_to_the_period(self, session, account_a):
        """A counter that ignored the period would bill a customer for last month forever."""
        account = session.get(Account, account_a)
        account.current_period_end = None

        record_usage(session, account, entry_point="web.agent", provider="Claude",
                     method="complete")

        start, _end = billing_period(account)
        rollup = session.query(AIUsageRollup).filter(
            AIUsageRollup.account_id == account.id
        ).one()
        assert rollup.period_start == start
