"""G5 · §6 Step 4 — THE fail-closed route harness (A4, A5).

F1: 146 route decorators across 24 router files, and *"nothing enforces that they have"* an
action declaration. N1: *"Build the harness first (Step 4) or the edits are hopeful rather than
verified. Under-scoping this is the most likely way the phase slips."*

**Two allowlists, and they are not the same kind of thing.**

- `PERMANENT_ALLOWLIST` — genuinely unauthenticated routes (health, OIDC callback, static).
  Each entry carries a one-line justification, because an unauthenticated route is a decision.
- `UNDECLARED_MODULES` — the *temporary* list, so the harness is enforceable **during** the
  migration rather than red until it finishes. It only ever shrinks (A5).

**Why the temporary list is per-module rather than per-route (C15).** G6 works file by file, so
the module is the unit of work: 24 entries that go to 0, instead of 146 that go to 0. Partial
work *within* a module still fails, which is the right granularity — a half-declared router is
not a safe state. And a 24-line literal stays readable in a diff, where a 146-line one would not.

**Monotonicity needs a pinned ceiling, not history.** A test sees only the current tree, so
"only ever shrinks" is unprovable from inside one run. `CEILING` is the committed high-water
mark; lowering it is a visible act in the diff. G6's final task asserts `CEILING == 0` *and* the
list is empty — otherwise the list could be emptied while the ceiling sat at 24 and this test
would pass forever without having constrained anything.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from mihomes.authz.declare import declared_action

# ---------------------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------------------

#: Routes that are genuinely unauthenticated. Each needs a reason — this list is reviewed.
PERMANENT_ALLOWLIST: dict[str, str] = {
    "mihomes.web.routes.auth": (
        "Sign-in and the OIDC callback run *before* an identity exists; requiring a declared "
        "action would make authentication depend on being authenticated. Signout is included "
        "because refusing to let a revoked user sign out is a worse failure than the check."
    ),
    "mihomes.web.routes.webhooks": (
        "Stripe is not a user. There is no cookie, no principal and no account on this request, "
        "so there is no role for the matrix to consult — the caller is authenticated by an "
        "HMAC signature over the raw request body (N3), which is strictly stronger than a "
        "session. Distinct from `auth`, which is excused because identity does not *yet* exist; "
        "here identity is irrelevant and always will be."
    ),
    "mihomes.web.routes.unsubscribe": (
        "A mail client is not a user either. RFC 8058's one-click POST arrives from the "
        "provider's infrastructure with no cookie and no principal, and the recipient may not "
        "have an account at all — an invited staff member's address can be suppressed before "
        "they ever sign in. Authenticated by an HMAC token over the address (N9); requiring a "
        "session would make opt-out available only to people who are already customers, which "
        "is precisely backwards."
    ),
    "mihomes.web.errors": (
        "`/healthz` is the platform's liveness probe, not a user's request. Fly calls it before "
        "any session exists — and must be able to call it when the *database* is down, which is "
        "precisely when a declared action could not be resolved anyway. It is the one route "
        "that is authenticated by nothing, which is why it returns nothing: a status string, no "
        "DSN, no version, no counts. Everything it says is public because anyone can ask."
    ),
}

#: How each permanently-allowlisted module authenticates instead of a declared action.
#:
#: **The list became load-bearing when it stopped having one entry.** `PERMANENT_ALLOWLIST` is
#: reviewed by a human reading the reason strings, and `test_permanent_allowlist_entries_are_still_live`
#: catches a *stale* entry — but nothing catches an entry added to silence the harness, which is
#: the cheapest way to make a route's authorisation disappear: one line, and the route is exempt
#: from the check that would have caught it.
#:
#: So each entry names its mechanism as data, and the test below asserts the naming is complete.
#: Same construction as `UNFILTERED_CLASSES` and SPEC-004's `GLOBAL_TABLES_WITH_ACCOUNT_ID`: a
#: correct exemption and a forgotten one are byte-identical in code unless the correct one is
#: made to say so.
ALLOWLIST_MECHANISMS: dict[str, str] = {
    "mihomes.web.routes.auth": (
        "the OIDC provider's own flow — state/nonce on the callback, and a signed session "
        "cookie thereafter"
    ),
    "mihomes.web.routes.webhooks": (
        "HMAC signature verification over the raw request body, against STRIPE_WEBHOOK_SECRET"
    ),
    "mihomes.web.routes.unsubscribe": (
        "HMAC token over the normalized email address, against MIHOMES_SECRET_KEY — verified "
        "with `compare_digest` before anything is written"
    ),
    "mihomes.web.errors": (
        "nothing, deliberately — and the route is safe to leave open because it reads no input "
        "and returns no data: one of two fixed strings, chosen by whether `SELECT 1` succeeds"
    ),
}

#: Router modules whose endpoints have not been declared yet. **Only ever shrinks** (A5).
#: Each entry was removed by its own G6 sub-task. **Now empty — G6 is complete.**
UNDECLARED_MODULES: set[str] = set()

#: The committed high-water mark. **Never raise it.**
#: 23 at G5 (every router but `auth`), 22 once `dashboard` proved the mechanism end to end, then
#: 21 `tasks`, 20 `assets`, 19 `work_orders`, 18 `properties`, 17 `ai`, and down to **0** at G6.9.
#: From here the temporary allowlist is a closed door: `test_ceiling_is_not_slack` pins the two
#: together, so re-opening it requires raising the ceiling in the same diff — which this comment
#: forbids and a reviewer can see.
CEILING = 0


# ---------------------------------------------------------------------------------------


def _app_routes():
    from mihomes.web.app import create_app

    app = create_app()
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _module_of(route: APIRoute) -> str:
    return getattr(route.endpoint, "__module__", "")


def _label(route: APIRoute) -> str:
    methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
    return f"{methods} {route.path}"


class TestNoUndeclaredRoutes:
    def test_no_undeclared_routes(self):
        """A4 — every endpoint declares `(action, route_class)`, or is explicitly excused.

        The failure message names the offending routes and their module, because the fix is
        almost always "add this module's declarations" and a bare count would send the reader
        hunting.
        """
        offenders = []
        for route in _app_routes():
            module = _module_of(route)
            if module in PERMANENT_ALLOWLIST or module in UNDECLARED_MODULES:
                continue
            if declared_action(route.endpoint) is None:
                offenders.append(f"{_label(route)}  ({module})")

        assert not offenders, (
            "every route must declare an action and a route class — an undeclared action is a "
            "deploy-time error, not a silent allow (§9.2). Undeclared:\n  "
            + "\n  ".join(sorted(offenders))
        )

    def test_scratch_route_is_caught(self):
        """**The harness must have teeth**, and this is the only test that proves it.

        A4 passing tells you nothing on its own while every module sits in the temporary
        allowlist — an implementation that returned `[]` unconditionally would look identical.
        Mounting a genuinely undeclared route and requiring the check to catch it is what
        separates a gate from a decoration. §6 Step 4's own verify clause asks for exactly this:
        *"adding a new undeclared route to a scratch module makes the suite fail."*
        """
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/__scratch__/undeclared")
        def _scratch():  # pragma: no cover - never called
            return {}

        scratch_routes = [r for r in app.routes if isinstance(r, APIRoute)]
        undeclared = [r for r in scratch_routes if declared_action(r.endpoint) is None]
        assert undeclared, "the declaration check failed to notice an undeclared route"

    def test_declared_route_is_accepted(self):
        """The positive control — a check that rejected *everything* would also pass above."""
        from fastapi import FastAPI

        from mihomes.authz.declare import declares

        app = FastAPI()

        @app.get("/__scratch__/declared")
        @declares("task.manage")
        def _scratch():  # pragma: no cover - never called
            return {}

        route = next(r for r in app.routes if isinstance(r, APIRoute))
        assert declared_action(route.endpoint) is not None

    def test_unknown_action_key_is_rejected_at_import_time(self):
        """§9.2 — a typo'd key must fail when the module loads, not when a user clicks it."""
        from mihomes.authz.declare import declares

        with pytest.raises(ValueError, match="not a MATRIX action key"):
            declares("task.mange")


class TestAllowlistDiscipline:
    def test_allowlist_monotonic(self):
        """A5 — the temporary allowlist only ever shrinks.

        Monotonicity is a claim about history and a test sees only the present, so `CEILING` is
        the committed high-water mark that stands in for it (C15).
        """
        assert len(UNDECLARED_MODULES) <= CEILING, (
            f"the temporary allowlist grew: {len(UNDECLARED_MODULES)} entries against a "
            f"ceiling of {CEILING}. It may only shrink (A5)."
        )

    def test_ceiling_is_not_slack(self):
        """The ceiling must track the list, or it stops constraining anything.

        Without this, someone could remove five modules and leave `CEILING` at 23, and the next
        person could add five back while `test_allowlist_monotonic` stayed green.
        """
        assert CEILING == len(UNDECLARED_MODULES), (
            "lower CEILING in the same commit that shrinks UNDECLARED_MODULES"
        )

    def test_every_permanent_entry_has_a_justification(self):
        """*"Each entry needs a one-line justification in the file."* — §6 Step 4.

        An allowlist without reasons is a list of things nobody has to defend.
        """
        for module, reason in PERMANENT_ALLOWLIST.items():
            assert reason and len(reason) > 20, f"{module} needs a real justification"

    def test_every_allowlisted_module_names_its_mechanism(self):
        """**C9b's derived gate** — an exemption must say what authenticates instead.

        `test_every_permanent_entry_has_a_justification` asks for prose a human reviews;
        `test_allowlists_name_only_real_modules` catches an entry that has gone stale. Neither
        catches the failure that matters most: **an entry added to silence the harness.** That is
        one line, and it removes a route from the only check that would have noticed it has no
        authorisation at all.

        A reason string cannot be asserted on — it is prose. A *mechanism* can: every allowlisted
        module must appear in `ALLOWLIST_MECHANISMS`, so exempting a route means answering "then
        what authenticates it?" in a second place, where its absence fails the suite. Not proof
        the mechanism works — proof that someone had to name one.
        """
        missing = sorted(set(PERMANENT_ALLOWLIST) - set(ALLOWLIST_MECHANISMS))
        assert not missing, (
            "a permanently-allowlisted module is exempt from the action check, so it must name "
            "what authenticates it instead — add an ALLOWLIST_MECHANISMS entry for: "
            f"{missing}"
        )

        extra = sorted(set(ALLOWLIST_MECHANISMS) - set(PERMANENT_ALLOWLIST))
        assert not extra, (
            f"ALLOWLIST_MECHANISMS names modules that are not allowlisted: {extra} — a stale "
            "entry would pre-authorise the next module to reuse the name"
        )

        for module, mechanism in ALLOWLIST_MECHANISMS.items():
            assert mechanism and len(mechanism) > 20, (
                f"{module}'s mechanism must actually describe one"
            )

    def test_allowlists_name_only_real_modules(self):
        """A stale entry silently excuses nothing while hiding that the work is done.

        If a router is deleted or renamed and its entry stays, the list stops shrinking for a
        reason that has nothing to do with the migration.
        """
        live = {_module_of(r) for r in _app_routes()}
        stale = sorted((UNDECLARED_MODULES | set(PERMANENT_ALLOWLIST)) - live)
        assert not stale, f"allowlists name modules that mount no routes: {stale}"

    def test_the_harness_sees_the_whole_router_table(self):
        """A guard on the guard.

        If `create_app()` ever stopped mounting the routers — or this walk stopped finding them —
        every test above would pass against an empty list. Pre-flight measured 146 route
        decorators across 24 files; the app also mounts a handful of framework routes.
        """
        assert len(_app_routes()) >= 140, (
            f"the harness sees only {len(_app_routes())} routes; it is not walking the real "
            "router table"
        )
