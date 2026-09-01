"""Login throttling — per-email AND per-IP (SPEC-010 §5.2, Step 4, D7).

**Both limits, not either, and the word "independently" in A9 is the whole design.** Each alone
has a trivial bypass:

* **per-email only** — a botnet spreads one guess per host. Ten thousand hosts each try `admin@`
  once, the per-email counter never fires because it is keyed on a value the attacker varies
  slowly, and the account falls.
* **per-IP only** — one host walks a user list. A single machine tries one password against ten
  thousand *different* addresses; the per-IP counter is the only thing that could see it, and if
  the design keyed only on email it sees nothing at all.

So there are two counters, they are consulted separately, and A9 exercises each with the other
held slack. A single counter that happens to satisfy both assertions is exactly the bypass D7
names — which is why the mutation check removes each limit in turn and requires A9 to go red
both times.

## Failures count, attempts do not (A10)

`record_failure` is called after a *failed* verification; `clear_attempts` after a successful
one. Incrementing on every attempt instead would be simpler and wrong: a person who signs in
successfully ten times would be throttled on the eleventh, and A10 — "a successful sign-in
clears the counter" — could not be satisfied by that shape at any threshold. The spec's decision
to split `check` from `record` is what makes the distinction expressible.

## In-process, and `db` is accepted but unused

§5.2 gives all three functions a `db` parameter, which reads as a table. **There is no
`login_attempts` table and this does not add one.** The harness's U5 states the intended
design — *"The rate limiter is in-process. On multiple instances the effective limit multiplies
by instance count — adequate single-instance, needs a shared store before scaling out"* — and a
launch-gate entry that describes the limiter as in-process is the authority over a signature
that implies otherwise.

`db` is kept in the signature so the call sites do not change when that gate is closed and the
counters move to a shared store. It is deliberately unused today.

**The consequence, stated plainly:** on N app instances the effective limit is N× what these
constants say, and a restart clears every counter. That is adequate for one instance and is
recorded as U5 rather than hidden here.

## The landing app's bucket is reusable in shape, not in instance

`landing/ratelimit.py:57`'s `TokenBucket` is per-IP only and refills continuously. This is a
different problem: a fixed window with a hard cap, because a login limit is a burst limit — you
want "five wrong passwords and then stop", not a drip that lets a patient attacker continue
forever at the refill rate.
"""

from __future__ import annotations

import time
from collections import OrderedDict

__all__ = [
    "EMAIL_MAX_FAILURES",
    "IP_MAX_FAILURES",
    "WINDOW_SECONDS",
    "TooManyAttempts",
    "check_login_attempt",
    "clear_attempts",
    "record_failure",
    "reset_all",
]

#: Five wrong passwords for one address, then stop. Generous for someone who has two passwords
#: in their head and tries both; far below what an online dictionary attack needs.
EMAIL_MAX_FAILURES = 5

#: Higher than the per-email limit on purpose. A household behind one NAT address, or an office,
#: shares an IP legitimately — several people mistyping on the same evening must not lock the
#: address out. It is still low enough to stop one host walking a user list.
IP_MAX_FAILURES = 20

#: Fifteen minutes. Long enough that an attacker cannot simply wait it out at speed, short
#: enough that a locked-out person is not stuck for the evening.
WINDOW_SECONDS = 15 * 60

#: Bounded, oldest-evicted-first, for the reason `landing/ratelimit.py` gives: per-key state in a
#: public-facing dict is a memory leak under a flood, which is a way to take the app down
#: *without* tripping the limit it is protecting.
MAX_TRACKED = 10_000


class TooManyAttempts(Exception):
    """Raised when either bucket is exhausted.

    Carries no indication of *which* limit fired and no email address. The message reaches a
    login form, and "too many attempts for that account" would confirm the account exists —
    reopening the oracle A7 closes. See `web/routes/password.py`.
    """

    def __init__(self, retry_after_seconds: int = WINDOW_SECONDS) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            "Too many sign-in attempts. Please wait a few minutes and try again."
        )


# key -> list of failure timestamps inside the window. Two namespaces, never one: the whole
# point of D7 is that the limits are separate.
_email_failures: OrderedDict[str, list[float]] = OrderedDict()
_ip_failures: OrderedDict[str, list[float]] = OrderedDict()


def _normalise(email: str) -> str:
    """Case-folded, matching `uq_users_email_password` and `find_password_user`.

    Without it `Admin@` and `admin@` are separate buckets and the limit is trivially doubled by
    varying capitalisation.
    """
    return email.strip().lower()


def _live(store: OrderedDict[str, list[float]], key: str, now: float) -> list[float]:
    """Failures still inside the window, pruning as it goes."""
    cutoff = now - WINDOW_SECONDS
    stamps = [t for t in store.get(key, ()) if t > cutoff]
    if stamps:
        store[key] = stamps
        store.move_to_end(key)
    else:
        store.pop(key, None)
    return stamps


def _evict(store: OrderedDict[str, list[float]]) -> None:
    while len(store) > MAX_TRACKED:
        store.popitem(last=False)


def check_login_attempt(db, *, email: str, ip: str) -> None:
    """Raise `TooManyAttempts` when **either** bucket is exhausted.

    Called *before* verifying a password, so an exhausted bucket costs no KDF work.

    `db` is accepted and unused — see the module docstring. Read-only: this does not count the
    attempt. `record_failure` does that, and only on failure.
    """
    now = time.monotonic()

    if len(_live(_email_failures, _normalise(email), now)) >= EMAIL_MAX_FAILURES:
        raise TooManyAttempts()

    if len(_live(_ip_failures, ip, now)) >= IP_MAX_FAILURES:
        raise TooManyAttempts()


def record_failure(db, *, email: str, ip: str) -> None:
    """Count one failed sign-in against both buckets.

    **Called for every failure, including one against an address that does not exist.** Skipping
    the unknown-email case is the natural optimisation and it reopens A7's oracle: attempt six
    against a real address would be throttled while attempt six against a nonexistent one sailed
    through, and the difference is readable straight off the response.
    """
    now = time.monotonic()

    key = _normalise(email)
    _email_failures.setdefault(key, []).append(now)
    _email_failures.move_to_end(key)
    _evict(_email_failures)

    _ip_failures.setdefault(ip, []).append(now)
    _ip_failures.move_to_end(ip)
    _evict(_ip_failures)


def clear_attempts(db, *, email: str) -> None:
    """Reset the per-email counter after a **successful** sign-in (A10).

    Without this a legitimate user who mistypes twice carries those failures for the rest of the
    window, and a third mistake days later locks them out of their own account.

    **The per-IP counter is deliberately NOT cleared.** One successful sign-in from an address
    that has just produced nineteen failures is what a successful credential-stuffing run looks
    like from the inside; clearing the IP counter there would hand the attacker a fresh budget
    each time they guessed right.
    """
    _email_failures.pop(_normalise(email), None)


def reset_all() -> None:
    """Drop every counter. **Test-support only**, and the in-process design is why it exists.

    The counters are module state, so without this one test's failures leak into the next and
    the suite passes or fails on ordering. A shared store would be reset by its own fixture.
    """
    _email_failures.clear()
    _ip_failures.clear()
