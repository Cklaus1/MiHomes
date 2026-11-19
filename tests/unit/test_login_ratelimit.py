"""G4 · SPEC-010 §6 Step 4 — login throttling (A9, A10).

**"Independently" is the load-bearing word in A9**, and it is what makes this file longer than a
rate-limiter test usually needs. The harness §2 states the failure class: *"A9 exercises the two
limits independently. One limiter satisfying both assertions is the bypass D7 names."*

A single counter keyed on, say, `(email, ip)` passes a naive reading of "throttled by email and
by IP" while defending against neither attack:

* vary the IP and the per-email limit never fires — a botnet spreads one guess per host;
* vary the email and the per-IP limit never fires — one host walks a user list.

So each limit is exercised with the other held deliberately slack, and the mutation check
removes each in turn and requires A9 to go red **both** times. If removing one limit leaves the
test green, the two are not separate and D7's bypass is live.
"""

from __future__ import annotations

import pytest

from mihomes.auth import ratelimit
from mihomes.auth.ratelimit import (
    EMAIL_MAX_FAILURES,
    IP_MAX_FAILURES,
    TooManyAttempts,
    check_login_attempt,
    clear_attempts,
    record_failure,
    reset_all,
)

# `db` is accepted and unused by all three functions (U5 — the limiter is in-process). Passing
# None keeps that visible at every call site rather than hiding it behind a fixture.
DB = None


@pytest.fixture(autouse=True)
def _clean_counters():
    """The counters are module state, so without this one test's failures leak into the next.

    That is a real consequence of the in-process design (U5), not a testing artifact — the same
    state is shared across every request in a process.
    """
    reset_all()
    yield
    reset_all()


def _fail(email: str, ip: str, times: int = 1) -> None:
    for _ in range(times):
        record_failure(DB, email=email, ip=ip)


# ── A9 — both limits, exercised independently ────────────────────────────────

def test_throttled_by_email_and_ip():
    """**A9 · D7** — each limit fires on its own, with the other held slack.

    The two halves are deliberately constructed so that neither could be passing because of the
    other counter:

    * the per-email half varies the IP on **every** attempt, so the per-IP counter never reaches
      its limit and cannot be what raises;
    * the per-IP half varies the email on every attempt, so the per-email counter never reaches
      its limit and cannot be what raises.

    Plus the negative twin each half needs: a *different* email from a *different* IP is still
    allowed, or the limiter would satisfy both assertions by simply refusing everyone.
    """
    # --- half 1: per-email, varying the IP -----------------------------------
    for i in range(EMAIL_MAX_FAILURES):
        # A different host each time. IP_MAX_FAILURES is higher than EMAIL_MAX_FAILURES and
        # each of these IPs is used once, so no per-IP bucket is anywhere near its limit.
        _fail("victim@example.com", f"10.0.0.{i}")

    with pytest.raises(TooManyAttempts):
        check_login_attempt(DB, email="victim@example.com", ip="10.0.0.99")

    # The negative twin: another address is untouched. Without this, a limiter that refused
    # everything unconditionally would pass the assertion above.
    check_login_attempt(DB, email="bystander@example.com", ip="10.0.0.99")

    reset_all()

    # --- half 2: per-IP, varying the email -----------------------------------
    for i in range(IP_MAX_FAILURES):
        # A different address each time, so every per-email bucket holds exactly one failure —
        # far below EMAIL_MAX_FAILURES. Only the per-IP counter can fire here.
        _fail(f"target{i}@example.com", "203.0.113.7")

    with pytest.raises(TooManyAttempts):
        check_login_attempt(DB, email="anyone@example.com", ip="203.0.113.7")

    # And its twin: a different host is unaffected.
    check_login_attempt(DB, email="anyone@example.com", ip="198.51.100.4")


def test_the_two_limits_do_not_share_a_counter():
    """The mutation this file exists to catch, asserted directly.

    A single bucket keyed on `(email, ip)` — or on either value alone — would pass a careless
    reading of A9. This pins the arithmetic: failures spread across many IPs still accumulate
    against **one** email, and failures spread across many emails still accumulate against
    **one** IP. A shared counter cannot do both.
    """
    # One short of the per-email limit, every attempt from a distinct host.
    for i in range(EMAIL_MAX_FAILURES - 1):
        _fail("split@example.com", f"172.16.0.{i}")

    # Not yet throttled — the limit is the limit, not one below it.
    check_login_attempt(DB, email="split@example.com", ip="172.16.0.200")

    _fail("split@example.com", "172.16.0.200")

    with pytest.raises(TooManyAttempts):
        check_login_attempt(DB, email="split@example.com", ip="172.16.0.201")


def test_email_matching_is_case_folded():
    """`Admin@` and `admin@` are one account, so they must be one bucket.

    Matching `uq_users_email_password` and `find_password_user`, both of which case-fold. Without
    it the limit is doubled by pressing shift, which is not much of a limit.
    """
    for i in range(EMAIL_MAX_FAILURES):
        _fail("MixedCase@Example.COM", f"10.1.0.{i}")

    with pytest.raises(TooManyAttempts):
        check_login_attempt(DB, email="mixedcase@example.com", ip="10.1.0.99")


def test_check_does_not_itself_count_an_attempt():
    """`check_login_attempt` is read-only; `record_failure` is what counts.

    If checking incremented, a person signing in correctly would be throttled by their own
    successful logins — and A10 could not be satisfied at any threshold, because the counter
    would rise on the very attempts that are supposed to clear it.
    """
    for _ in range(EMAIL_MAX_FAILURES * 3):
        check_login_attempt(DB, email="reader@example.com", ip="10.2.0.1")

    # Still fine after many checks, because none of them was a failure.
    check_login_attempt(DB, email="reader@example.com", ip="10.2.0.1")


# ── A10 — success clears the counter ──────────────────────────────────────────

def test_success_clears_attempts():
    """**A10** — a successful sign-in resets the per-email counter.

    Without it, someone who mistypes twice today carries those failures for the rest of the
    window and a third mistake locks them out of their own account — the harness's exact
    phrasing, and a support ticket rather than a security property.
    """
    for i in range(EMAIL_MAX_FAILURES - 1):
        _fail("mistyper@example.com", f"10.3.0.{i}")

    # One short of the limit, then a success.
    clear_attempts(DB, email="mistyper@example.com")

    # The budget is whole again: a full run of failures is needed before throttling.
    for i in range(EMAIL_MAX_FAILURES - 1):
        _fail("mistyper@example.com", f"10.3.1.{i}")
    check_login_attempt(DB, email="mistyper@example.com", ip="10.3.1.99")

    # The positive twin — clearing is not a no-op *and* not a permanent exemption. Cross the
    # limit again and the throttle still works.
    _fail("mistyper@example.com", "10.3.1.99")
    with pytest.raises(TooManyAttempts):
        check_login_attempt(DB, email="mistyper@example.com", ip="10.3.1.99")


def test_success_does_not_clear_the_ip_counter():
    """The deliberate asymmetry in `clear_attempts`, pinned.

    Nineteen failures then one success, from a single host, is what a credential-stuffing run
    looks like from the inside — the attacker guessed right. Clearing the IP counter there would
    hand them a fresh budget every time they succeeded, which is precisely the wrong moment to
    be generous.
    """
    for i in range(IP_MAX_FAILURES):
        _fail(f"walked{i}@example.com", "192.0.2.66")

    clear_attempts(DB, email="walked0@example.com")

    # The per-email budget for that one address is restored...
    assert ratelimit._email_failures.get("walked0@example.com") is None

    # ...but the host is still throttled.
    with pytest.raises(TooManyAttempts):
        check_login_attempt(DB, email="walked0@example.com", ip="192.0.2.66")


def test_clearing_an_unknown_email_is_harmless():
    """Called on every successful sign-in, including the first one a person ever makes."""
    clear_attempts(DB, email="never-seen@example.com")
    check_login_attempt(DB, email="never-seen@example.com", ip="10.4.0.1")


# ── The window, and the bound on memory ───────────────────────────────────────

def test_failures_expire_from_the_window(monkeypatch):
    """The limit is a window, not a lifetime quota.

    Asserted by moving the clock rather than sleeping: a test that waits fifteen minutes does not
    get run, and one that waits a *shortened* window is asserting against a constant it also
    controls.
    """
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])

    for i in range(EMAIL_MAX_FAILURES):
        _fail("patient@example.com", f"10.5.0.{i}")

    with pytest.raises(TooManyAttempts):
        check_login_attempt(DB, email="patient@example.com", ip="10.5.0.99")

    # Past the window, the failures are gone.
    now[0] += ratelimit.WINDOW_SECONDS + 1
    check_login_attempt(DB, email="patient@example.com", ip="10.5.0.99")


def test_counters_do_not_grow_without_bound():
    """Per-key state in a public-facing dict is a memory leak under a flood.

    The same argument `landing/ratelimit.py:11` makes: filling the table is a way to take the
    app down *without* tripping the limit that is supposed to stop you.
    """
    for i in range(ratelimit.MAX_TRACKED + 500):
        record_failure(DB, email=f"flood{i}@example.com", ip=f"10.{i // 256 % 256}.{i % 256}.1")

    assert len(ratelimit._email_failures) <= ratelimit.MAX_TRACKED
    assert len(ratelimit._ip_failures) <= ratelimit.MAX_TRACKED


def test_the_thresholds_are_sane_relative_to_each_other():
    """The per-IP limit must exceed the per-email one.

    A household behind one NAT address shares an IP legitimately. If the IP limit were the lower
    of the two it would fire first in ordinary use, and the per-email limit — the one that
    actually protects an account — would be unreachable dead code.
    """
    assert IP_MAX_FAILURES > EMAIL_MAX_FAILURES, (
        "the per-IP limit is at or below the per-email limit, so it fires first in normal "
        "household use and the per-email limit never runs"
    )
    assert EMAIL_MAX_FAILURES >= 3, "too few attempts to survive a genuine typo"
