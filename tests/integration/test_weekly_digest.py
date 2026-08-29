"""SPEC-005 §6 Step 13 — the weekly digest, gated as a SEND (A14, A14b).

D16 is the whole point of this file, and it is a distinction that either half alone hides:

- **A14** — the *scheduled* send is Estate-only. A Pro account is not mailed.
- **A14b** — the *on-request* route works on **every** plan. Estate buys the schedule, not the
  feature (N8), and gating `generate_estate_digest` would paywall a button that works today for
  everyone — SPEC-004 N9's mistake, delivered worse.

Either criterion passing alone is compatible with the feature being wrong in the other
direction, which is why §8 gives them two rows and two names rather than one cell.

**The fixture trap this file has to step around.** `tests/conftest.py` sets
`DEFAULT_FIXTURE_PLAN = "estate"`, so an account created without an explicit plan is *already*
entitled. A14's "a Pro account does not receive it" written against a default-plan account
would assert nothing at all — it would be testing Estate twice. Every account here names its
plan.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from mihomes.cli.jobs import DIGEST_WINDOW_DAYS
from mihomes.entitlements import can
from mihomes.models.email_outbox import EmailOutbox

pytestmark = pytest.mark.usefixtures("_pg_engine")


# ── helpers ────────────────────────────────────────────────────────────────────


class _Acct:
    """The two attributes `can()` reads. See `test_estate_gates.py` on why not a real row."""

    def __init__(self, plan: str) -> None:
        self.plan = plan
        self.subscription_status = None
        self.id = uuid.uuid4()


class _NullSession:
    """A session the job may `commit()` but never reads.

    The gate decides before anything touches the database, so the sweep's plumbing is what is
    under test here, not persistence — and a real session would drag account rows in for three
    plans to prove one boolean.
    """

    def commit(self) -> None:
        pass


def _queued_digests(session) -> list[EmailOutbox]:
    """Outbox rows for the digest template, under whatever account is bound."""
    from sqlalchemy import select

    return list(
        session.execute(
            select(EmailOutbox).where(EmailOutbox.template == "weekly_digest")
        ).scalars()
    )


# ── A14 — the scheduled send is gated ──────────────────────────────────────────


def test_scheduled_send_gated():
    """A14 — Estate is entitled to the scheduled digest and Pro is not.

    Asserted at the decision the job actually consults. The job's own loop is exercised by
    `test_the_job_skips_an_unentitled_account_before_generating` below; this is the criterion
    D16 states, and it fails the moment the plan table drifts.
    """
    assert can(_Acct("estate"), "report.weekly_ai"), "Estate must receive the weekly digest"
    assert not can(_Acct("pro"), "report.weekly_ai"), "Pro must not receive it"
    assert not can(_Acct("free"), "report.weekly_ai"), "Free must not receive it"

    # And the denial has somewhere to send them — `PRICING` rule 4. A gate that denies with no
    # upgrade path is a dead end rather than a paywall.
    denied = can(_Acct("pro"), "report.weekly_ai")
    assert denied.upgrade_target == "estate"


@pytest.mark.parametrize(
    ("plan", "expect_sent"),
    [("estate", True), ("pro", False), ("free", False)],
)
def test_the_job_sends_only_to_entitled_accounts(monkeypatch, plan, expect_sent):
    """A14 **at the job**, which is where the gate can actually be removed.

    The first version of this test stubbed `_send_one_digest` with a raising function and
    asserted "does not raise" — which proves the *ordering* and nothing else. Deleting the gate
    from `weekly_digest` left it green: with the stub never called for Estate either, "did not
    raise" was true whether or not the gate existed. Caught by mutation, not by reading.

    So the stub now *records* instead of raising, and the assertion is on what it recorded.
    That makes the gate's removal observable: without it, the Pro and Free rows send too.
    """
    from mihomes.cli import jobs

    calls: list[str] = []

    def _record(session, account, start, end, period):
        calls.append(account.plan)
        return 1

    monkeypatch.setattr(jobs, "_send_one_digest", _record)
    monkeypatch.setattr(jobs, "_all_accounts", lambda: [(uuid.uuid4(), f"{plan}-acct")])

    class _Ctx:
        def __enter__(self):
            return (_NullSession(), _Acct(plan))

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(jobs, "_account_session", lambda _id: _Ctx())

    jobs.weekly_digest(dry_run=False)

    assert calls == ([plan] if expect_sent else []), (
        f"{plan}: expected {'a send' if expect_sent else 'no send'}, got {calls!r}"
    )


def test_the_gate_runs_before_generation(monkeypatch):
    """The gate fires **before** `generate_estate_digest`, not after.

    Reversed, a Pro account's digest would be generated at full inference cost and then
    discarded — a paywall that costs more to enforce than to ignore. Distinct from the
    parametrized test above: that one proves *whether* mail is sent, this one proves the
    expensive call is never reached.
    """
    from mihomes.cli import jobs

    def _explode(*args, **kwargs):  # pragma: no cover - only runs on a regression
        raise AssertionError("generate_estate_digest ran for an unentitled account")

    monkeypatch.setattr(jobs, "_send_one_digest", _explode)
    monkeypatch.setattr(jobs, "_all_accounts", lambda: [(uuid.uuid4(), "pro-acct")])

    class _Ctx:
        def __enter__(self):
            return (_NullSession(), _Acct("pro"))

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(jobs, "_account_session", lambda _id: _Ctx())

    jobs.weekly_digest(dry_run=False)  # must not raise


def test_the_digest_window_matches_the_schedule():
    """A weekly job must look back a week.

    Not decoration: `SCHEDULE["weekly-digest"]` is `0 8 * * 1` and the window is a separate
    constant, so the two can drift apart silently — a digest reporting 3 days on a 7-day cadence
    loses four days of estate activity every week and nothing fails.
    """
    from mihomes.cli.jobs import SCHEDULE

    cron, _rationale = SCHEDULE["weekly-digest"]
    assert cron.split()[4] != "*", (
        f"weekly-digest is scheduled {cron!r}, which is not weekly; "
        f"DIGEST_WINDOW_DAYS={DIGEST_WINDOW_DAYS} assumes a weekly cadence"
    )
    assert DIGEST_WINDOW_DAYS == 7


# ── A14b — the on-request route is NOT gated ───────────────────────────────────


def test_on_request_ungated():
    """A14b — `generate_estate_digest` and its route carry **no** plan gate (N8, D16).

    Asserted against the source rather than by driving the route, and deliberately so: driving
    it needs an LLM provider, and a test that mocks the provider would prove the mock is
    ungated. What must remain true is that *the function and its route never consult
    entitlements* — a property of the code, which is what is read here.

    A gate added through a decorator or a helper import is what this catches; that is why it
    checks the module for the entitlement machinery by name rather than for one call shape.
    """
    import inspect

    from mihomes.services.ai import reports
    from mihomes.web.routes import ai as ai_routes

    digest_src = inspect.getsource(reports.generate_estate_digest)
    for forbidden in ("can(", "check_entitlement", "entitlements", "report.weekly_ai"):
        assert forbidden not in digest_src, (
            f"generate_estate_digest consults {forbidden!r} — N8 forbids gating the function; "
            "Estate buys the schedule, not the feature"
        )

    route_src = inspect.getsource(ai_routes.estate_digest)
    for forbidden in ("can(", "check_entitlement", "report.weekly_ai"):
        assert forbidden not in route_src, (
            f"POST /ai/estate-digest consults {forbidden!r} — A14b requires it on every plan"
        )

    # The module as a whole must not have grown an entitlement import that a future edit could
    # reach for. RBAC (`declares`) is a *different* gate and is expected — D10 keeps them apart.
    assert "from mihomes.entitlements" not in inspect.getsource(reports)


def test_the_route_is_reachable_on_a_non_estate_plan(web_client_as, _pg_engine):
    """A14b, at the surface rather than in the source.

    The source check above cannot see a gate applied by a dependency or middleware, and this
    cannot see one applied inside the LLM call — together they cover the route.

    A Pro account must reach the handler. What it gets back depends on whether an AI provider is
    configured, so the assertion is *not a 402/403*: the plan must never be the thing that stops
    it. A 500 from a missing provider is a different failure and is allowed here.
    """
    with _pg_engine.begin() as conn:
        conn.execute(
            text("UPDATE accounts SET plan = 'pro' WHERE slug LIKE 'acct-a%'")
        )

    client = web_client_as("owner")
    response = client.post("/ai/estate-digest", data={"days": "7"})

    assert response.status_code not in (402, 403), (
        f"POST /ai/estate-digest returned {response.status_code} on a Pro account; "
        "A14b requires the on-request digest on every plan (N8)"
    )
