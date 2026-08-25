"""G11 · §6 Step 11 — overage behaviour (A14, A15, A26).

`PRICING` §5.3's rationale is worth keeping in view while reading these, because the shape of the
policy is not obvious from the code alone:

> A hard wall at exactly the limit punishes the most engaged users (our best upgrade candidates)
> and creates a bad moment. A soft cap keeps them happy while making the value of upgrading
> concrete. The hard ceiling exists purely to bound worst-case Claude spend.

So there are **three** regions, not two: under the cap, over the cap but inside the buffer (works,
with a banner), and past the ceiling (denied). A test asserting only "denied at the limit" would
be testing a policy the product deliberately rejected.
"""

from __future__ import annotations

import pytest

from mihomes.entitlements.limits import PLAN_LIMITS
from mihomes.entitlements.service import Allowed, Denied, usage
from mihomes.models.account import Account
from mihomes.models.ai_usage import AIUsageRollup
from mihomes.services.metering.meter import (
    billing_period,
    check_and_reserve,
    hard_ceiling,
    record_usage,
)


def _spend(session, account, calls: int) -> None:
    """Drive the counter to `calls` by writing the rollup directly.

    Calling `record_usage` 200 times would work and would make this file's runtime absurd. The
    rollup *is* the counter (D18), so setting it is equivalent — and `test_the_counter_moves` in
    `test_ai_usage.py` already proves `record_usage` maintains it.
    """
    start, end = billing_period(account)
    rollup = session.query(AIUsageRollup).filter(
        AIUsageRollup.account_id == account.id,
        AIUsageRollup.period_start == start,
    ).one_or_none()
    if rollup is None:
        rollup = AIUsageRollup(
            account_id=account.id, period_start=start, period_end=end, calls_used=0,
        )
        session.add(rollup)
    rollup.calls_used = calls
    session.commit()


@pytest.fixture
def free_account(session, account_a) -> Account:
    account = session.get(Account, account_a)
    account.plan = "free"
    account.subscription_status = None
    session.commit()
    return account


@pytest.fixture
def pro_account(session, account_a) -> Account:
    account = session.get(Account, account_a)
    account.plan = "pro"
    account.subscription_status = "active"
    session.commit()
    return account


class TestTheThreeRegions:
    def test_under_the_cap_is_allowed(self, session, pro_account):
        _spend(session, pro_account, 10)
        assert isinstance(
            check_and_reserve(session, pro_account, entry_point="web.agent"), Allowed
        )

    def test_ceiling_and_nudges(self, session, pro_account):
        """**A14** — the ceiling denies, the soft cap does not, and each nudge fires once.

        Pro's buffer is 20%, so 3,000 calls is the soft cap and 3,600 the ceiling. The middle
        region is the one worth asserting: at 3,000 the customer is *over their plan* and AI
        still works, which is the policy §5.3 chose over a hard wall.
        """
        cap = PLAN_LIMITS["pro"]["ai_calls_per_month"]
        ceiling = hard_ceiling(pro_account)
        assert ceiling > cap, "Pro's 20% buffer must put the ceiling above the soft cap"

        # At the soft cap: over the plan, still working.
        _spend(session, pro_account, cap)
        assert isinstance(
            check_and_reserve(session, pro_account, entry_point="web.agent"), Allowed
        ), "the soft cap must not deny — §5.3 chose a buffer over a hard wall"

        # Inside the buffer: still working.
        _spend(session, pro_account, cap + 100)
        assert isinstance(
            check_and_reserve(session, pro_account, entry_point="web.agent"), Allowed
        )

        # At the ceiling: denied.
        _spend(session, pro_account, ceiling)
        decision = check_and_reserve(session, pro_account, entry_point="web.agent")
        assert isinstance(decision, Denied), (
            "past the hard ceiling every request is denied before the provider call (§5.3)"
        )
        assert decision.upgrade_target == "estate"

    def test_free_has_no_buffer(self, session, free_account):
        """Free's buffer is **0%**, so its cap and ceiling coincide at 200.

        A deliberate asymmetry, not an oversight: *"there's no revenue to offset cost."* Asserted
        because a reader seeing the buffer logic elsewhere would reasonably expect Free to get one
        too, and "borrow 20% from a plan that pays nothing" is exactly the generous-looking bug
        that costs money.
        """
        assert hard_ceiling(free_account) == PLAN_LIMITS["free"]["ai_calls_per_month"]

        _spend(session, free_account, PLAN_LIMITS["free"]["ai_calls_per_month"])
        assert isinstance(
            check_and_reserve(session, free_account, entry_point="web.agent"), Denied
        )

    def test_the_denial_names_the_reset_date(self, session, free_account):
        """§5.3: the user sees *"AI paused until &lt;resets_at&gt;"* with their meter and reset
        date. A bare "limit reached" leaves them with no idea whether to wait an hour or a month.
        """
        _spend(session, free_account, 500)
        decision = check_and_reserve(session, free_account, entry_point="web.agent")

        _start, end = billing_period(free_account)
        assert end.isoformat() in decision.reason
        assert str(PLAN_LIMITS["free"]["ai_calls_per_month"]) in decision.reason


class TestNudges:
    def test_each_nudge_fires_once(self, session, pro_account):
        """§5.1: *"nudges at 80% and 100%"*, §5.3: *"once per cycle"*.

        The columns exist because "have we told them yet" is a fact about what was **sent**.
        Deriving it from `used >= threshold` would re-notify on every call past the threshold —
        a notification storm aimed precisely at the heaviest users, who are the best upgrade
        candidates.
        """
        cap = PLAN_LIMITS["pro"]["ai_calls_per_month"]
        start, _end = billing_period(pro_account)

        _spend(session, pro_account, int(cap * 0.8))
        check_and_reserve(session, pro_account, entry_point="web.agent")

        rollup = session.query(AIUsageRollup).filter(
            AIUsageRollup.account_id == pro_account.id,
            AIUsageRollup.period_start == start,
        ).one()
        first_marked = rollup.warned_80_at
        assert first_marked is not None
        assert rollup.warned_100_at is None, "80% must not mark the 100% nudge"

        # Several more calls in the same region must not re-mark.
        for _ in range(3):
            check_and_reserve(session, pro_account, entry_point="web.agent")
        session.refresh(rollup)
        assert rollup.warned_80_at == first_marked, "the 80% nudge must fire once per cycle"

        # **And the same for the 100% marker**, which the first version of this test omitted —
        # a mutation that re-marked `warned_100_at` on every call passed all twelve tests.
        # Both markers need the assertion: they are independent columns with independent guards,
        # and testing one proves nothing about the other.
        _spend(session, pro_account, cap)
        check_and_reserve(session, pro_account, entry_point="web.agent")
        session.refresh(rollup)
        hundred_marked = rollup.warned_100_at
        assert hundred_marked is not None

        for _ in range(3):
            check_and_reserve(session, pro_account, entry_point="web.agent")
        session.refresh(rollup)
        assert rollup.warned_100_at == hundred_marked, (
            "the 100% nudge must fire once per cycle — re-marking on every call is a "
            "notification storm aimed at the heaviest users"
        )

    def test_crossing_both_thresholds_at_once_marks_both(self, session, pro_account):
        """**Not `elif`**, and this is why.

        A burst — or a plan downgrade that lowers the cap under existing usage — can jump an
        account past both thresholds between two checks. Marking only 100% would leave the 80%
        marker unset, so the 80% nudge would fire in a *later* period and read as a false alarm
        about usage that had already been billed.
        """
        cap = PLAN_LIMITS["pro"]["ai_calls_per_month"]
        start, _end = billing_period(pro_account)

        _spend(session, pro_account, cap)
        check_and_reserve(session, pro_account, entry_point="web.agent")

        rollup = session.query(AIUsageRollup).filter(
            AIUsageRollup.account_id == pro_account.id,
            AIUsageRollup.period_start == start,
        ).one()
        assert rollup.warned_80_at is not None
        assert rollup.warned_100_at is not None


class TestSystemCallsAreExempt:
    def test_system_calls_exempt(self, session, free_account):
        """**A15** — a system-initiated call is never metered or denied (D11/N10).

        > A limit that trips a scheduled job is a bug: the user cannot upgrade their way out of
        > something they did not do.

        Exercised at the wrapper, which is where the exemption actually lives: a provider built
        with no bound account neither checks nor records, so the nightly automation runs at any
        usage level.
        """
        from mihomes.services.metering.ai_wrapper import MeteredProvider

        class _Stub:
            def complete(self, *a, **k):
                return "ok"

        _spend(session, free_account, 10_000)  # far past any ceiling

        system_provider = MeteredProvider(
            _Stub(), session_factory=None, account=None, entry_point="system.nightly",
        )

        assert system_provider.complete("system", "user") == "ok", (
            "a background job must not be denied by a household's quota (N10)"
        )

    def test_a_user_call_at_the_same_usage_is_denied(self, session, free_account):
        """The control for A15. Without it, a wrapper that never denied *anything* would pass —
        and the exemption would be indistinguishable from the ceiling being broken."""
        _spend(session, free_account, 10_000)
        assert isinstance(
            check_and_reserve(session, free_account, entry_point="web.agent"), Denied
        )


class TestConcurrentAtTheCap:
    def test_concurrent_at_cap(self, _pg_engine, account_a):
        """**A26** — two concurrent calls at the cap: exactly one proceeds.

        The arbitration is the row lock in `record_usage` (`with_for_update`), not the check:
        `check_and_reserve` deliberately does not reserve (see its docstring), so the guarantee
        being asserted is that **the counter cannot lose an increment** under concurrency. Two
        threads both incrementing a stale read would leave `calls_used` at +1 instead of +2, and
        the account would drift under its true usage forever.

        Real threads on real connections, because a lock is not observable any other way — the
        lesson A27 cost two rewrites to learn.
        """
        import threading

        from sqlalchemy.orm import Session as OrmSession

        with OrmSession(_pg_engine) as setup:
            account = setup.get(Account, account_a)
            account.plan = "pro"
            account.subscription_status = "active"
            setup.commit()

        errors: list[BaseException] = []
        both_ready = threading.Barrier(2, timeout=10)

        def call() -> None:
            try:
                # **`account_context` inside the thread, not around the threads.** Tenant scoping
                # is a `ContextVar`, which is thread-local: a binding made on the main thread is
                # invisible to a worker, and the writes fail with a bare `LookupError` — the
                # fail-closed direction working exactly as SPEC-002 D3 intends.
                from mihomes.tenancy import account_context

                with account_context(account_a), OrmSession(_pg_engine) as worker:
                    acct = worker.get(Account, account_a)
                    both_ready.wait()
                    record_usage(worker, acct, entry_point="web.agent",
                                 provider="Claude", method="complete")
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # The verification block reads tenant tables too, so it needs its own binding — the
        # threads' contexts died with them.
        from mihomes.tenancy import account_context

        with account_context(account_a), OrmSession(_pg_engine) as check:
            acct = check.get(Account, account_a)
            start, _end = billing_period(acct)
            rollup = check.query(AIUsageRollup).filter(
                AIUsageRollup.account_id == account_a,
                AIUsageRollup.period_start == start,
            ).one()
            used = rollup.calls_used

            check.query(AIUsageRollup).filter(
                AIUsageRollup.account_id == account_a
            ).delete()
            from mihomes.models.ai_usage import AIUsageEvent
            check.query(AIUsageEvent).filter(
                AIUsageEvent.account_id == account_a
            ).delete()
            acct.plan = "free"
            acct.subscription_status = None
            check.commit()

        assert not errors, f"a concurrent metered call raised: {errors}"
        assert used == 2, (
            f"the counter lost an increment under concurrency ({used} != 2) — two threads read "
            "the same calls_used and both wrote back +1"
        )


class TestUsageReport:
    def test_usage_returns_a_real_measurement(self, session, pro_account):
        """P3-b closed: `usage()` no longer returns `limit=None`.

        Phase 2 returned `None` deliberately — reporting a limit while nothing counted toward it
        would render a bar that is always empty and read as "0 of 3000 used" rather than "not
        measured". The meter exists now, so the number is a measurement.
        """
        _spend(session, pro_account, 42)
        report = usage(pro_account, "ai_calls", session=session)

        assert report.used == 42
        assert report.limit == PLAN_LIMITS["pro"]["ai_calls_per_month"]
        assert report.resets_at is not None

    def test_the_two_argument_signature_still_works(self, pro_account):
        """SPEC-003 §5.3 requires this signature *"character-for-character"*, so `session` is
        keyword-only and optional. Without one the report is honest rather than wrong: the real
        limit, and `used=0` because nothing was read."""
        report = usage(pro_account, "ai_calls")
        assert report.limit == PLAN_LIMITS["pro"]["ai_calls_per_month"]
        assert report.used == 0

    def test_an_unknown_meter_is_not_measured(self, session, pro_account):
        """An unknown meter must not silently borrow the AI limit — `None` means "not
        measured", which is a different claim from "no usage"."""
        assert usage(pro_account, "carrier_pigeons", session=session).limit is None
