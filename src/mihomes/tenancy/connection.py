"""G9 · §6 Step 9 — connection hygiene: getting the tenant onto the connection (A11).

RLS reads `app.current_account` from the **connection**, while the application knows the
tenant as a **ContextVar**. This module is the bridge, and every part of it is shaped by one
fact: connections are pooled and reused across tenants.

**N3 — transaction-local, never session-level.** `set_config(name, value, is_local=true)` is
`SET LOCAL`: Postgres discards it when the transaction ends. A session-level `SET` does not
go away, and both halves of why that matters are measured here:

```
session-level SET, then a second transaction on the same connection -> sees 'bbbb'   LEAK
session-level SET, connection returned to the pool and checked out  -> sees 'cccc'   LEAK
```

So a session-scoped GUC outlives both the transaction *and* the pool checkin, and the next
request — a different tenant — inherits it. Fly fronts Postgres with PgBouncer in
**transaction** pooling mode, which makes the reuse window even tighter than a local pool.
This is the subtlest rule in the spec and the one with the worst failure mode: not an error,
just another tenant's rows.

Because it is transaction-local it must be re-issued on **every** transaction, which is why
this hooks `after_begin` rather than being set once when a session opens.

**Both GUCs, not just the account.** §4.4's snippet sets only `app.current_account`, but
§4.2's `membership_self` RLS policy — the one bootstrap exception, which makes the account
picker work before any account is chosen — keys on `app.current_user`. Setting only the
account leaves that policy permanently unsatisfiable, so the picker would return an empty
list. The user GUC is set whenever a user is bound, independently of the account.

**Every transaction stamps both GUCs — with `NULL` when nothing is bound.** This replaces
Step 9's "pool `checkin` `RESET`", for two reasons, and the substitution is deliberate:

*The checkin RESET does not work.* Executing SQL in the `checkin` event leaves an implicit
transaction open on the psycopg connection, and SQLAlchemy's own connection reset — which
restores the isolation level, i.e. sets `autocommit` — then fails with
`can't change 'autocommit' now: connection in transaction status INERROR`. Measured: it broke
every fixture that shares the pool. `RESET` is also itself transactional, so a `RESET` issued
inside a transaction that is subsequently rolled back is simply undone.

*Always-stamping is strictly stronger anyway.* A transaction-local
`set_config(guc, NULL, true)` **overrides** a session-level value for the duration of the
transaction (measured: the leaked `'leaked'` reads as `''` inside it, and returns afterwards —
which no longer matters, because the next transaction overrides it too). So a stray session
`SET` from a migration, a `psql` session on the same pool, or any future code cannot be
observed by a scoped query. The guarantee holds at the point of use rather than depending on
the pool having cleaned up — the same principle G5 applied to `ensure_unique_slug`.

**Where the empty string comes from — this closes a question G7 left open.** After a
transaction-local GUC's transaction ends, `current_setting('app.current_account', true)`
returns **`''`**, not `NULL` (measured). That is the source of the
`invalid input syntax for type uuid: ""` failure seen in G7, and it means the `NULLIF(..., '')`
in `tenancy/rls.py`'s policy predicate is **required for correctness, not defensive**: without
it, the second transaction on any reused connection raises instead of returning zero rows.
"""

from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from mihomes.tenancy.context import current_account, current_user

__all__ = [
    "ACCOUNT_GUC",
    "USER_GUC",
    "bind_account_guc",
    "bind_user_guc",
    "install_connection_listeners",
]

ACCOUNT_GUC = "app.current_account"
USER_GUC = "app.current_user"


def bind_account_guc(session: Session, account_id) -> None:
    """Stamp `ACCOUNT_GUC` on the **already-open** transaction, for a tenant write.

    **`account_context` alone is not enough, and this is the trap.** That context manager sets
    the `current_account` ContextVar, which is what `_stamp_tenant_on_insert` reads to fill a
    new row's `account_id` — so the *stamp* works. But RLS's `WITH CHECK` compares that
    `account_id` against `current_setting('app.current_account')`, and the GUC is written once
    per transaction by `_set_tenant_guc` on `after_begin`. On a transaction that opened before
    the context was entered — every web request, which begins its transaction in `get_db` and
    only later learns which account it is acting for — the GUC still holds whatever it held
    then, usually empty. The insert is then refused:

        InsufficientPrivilege: new row violates row-level security policy
        for table "onboarding_state"

    Three separate writes hit exactly this and each looked like its own bug: the onboarding
    state row (which made `GET /onboarding/` 500 for an existing member, so the wizard could
    neither be finished nor escaped), the estate-rename audit row, and the owner membership at
    account creation. The pattern to reach for is `account_context(...)` *plus* this — the
    ContextVar for the stamp, the GUC for the policy.

    Kept beside `bind_user_guc` and the listener that owns the same rule, because a copy per
    call site is how the fourth one gets forgotten.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT set_config(:guc, :value, true)"),
        {"guc": ACCOUNT_GUC, "value": str(account_id)},
    )


def bind_user_guc(session: Session, user_id) -> None:
    """Stamp `app.current_user` so RLS's `membership_self` policy can see this user's rows.

    **Every membership read that runs before a tenant is bound needs this, and six of them
    were missing it.** `memberships` is a tenant table. The pre-account reads — session
    lookup, principal resolution, sign-in's account binding — are covered not by
    `ACCOUNT_GUC` (there is no account yet; that is what they are resolving) but by the
    `membership_self` policy:

        USING (user_id = (SELECT NULLIF(current_setting('app.current_user', true), '')::uuid))

    `auth/sessions.py`'s docstring names that policy as the enforcement for exactly these
    reads. But the GUC is otherwise only stamped by `_set_tenant_guc` from the `current_user`
    ContextVar, which is unset on these paths — the request is *becoming* authenticated. So the
    policy evaluated against NULL and the reads returned **zero rows for a user with a
    perfectly good membership**, with no error to hint at it.

    Measured on a live install, and the symptoms did not look like one bug:

      * `lookup_session` found no membership → returned `None` → every request read as
        unauthenticated → **the browser bounced back to `/login` in a loop**.
      * `resolve_principal` found none → 403 "No account selected" → **stuck on the onboarding
        wizard**, unable to reach any other page.
      * `sole_account_for` found none → the session was never bound to an account at sign-in.

    Invisible to the whole suite, whose fixtures connect as a superuser — RLS does not apply to
    one at all (`rls.py` records that measurement), so the policies were present and bound to
    nothing.

    Lives here rather than in each caller because it is one rule about one GUC, and six copies
    is how five of them end up missing it. `is_local=true` keeps it transaction-scoped (N3), so
    it cannot leak onto a pooled connection.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT set_config(:guc, :value, true)"),
        {"guc": USER_GUC, "value": str(user_id)},
    )


def _set_tenant_guc(session: Session, transaction, connection) -> None:
    """Stamp the transaction-local GUCs at the start of every transaction (D8, N3).

    **Absence of context is not an error here**, unlike the read filter in `session.py`. A
    transaction may legitimately begin with no tenant — sign-in reading GLOBAL `users`, the
    account picker before a choice is made, Alembic. Such a transaction gets `NULL`, so RLS
    returns zero rows: the correct fail-closed outcome. Raising would break those paths for no
    gain, and `session.py`'s filter already refuses *tenant-table* access without a context.

    Unbound is stamped as `NULL` rather than skipped, so a session-level value left on the
    connection by anything else cannot be observed — see the module docstring.
    """
    if connection.dialect.name != "postgresql":
        return

    for guc, var in ((ACCOUNT_GUC, current_account), (USER_GUC, current_user)):
        try:
            value = str(var.get())
        except LookupError:
            value = None
        connection.execute(
            text("SELECT set_config(:guc, :value, true)"),
            {"guc": guc, "value": value},
        )


def install_connection_listeners() -> None:
    """Register the listener. Idempotent.

    Bound to the `Session` **class** rather than to one engine, for the same reason as the
    other tenancy listeners: a session created by code that predates tenancy would otherwise
    run against an unstamped connection.
    """
    if not event.contains(Session, "after_begin", _set_tenant_guc):
        event.listen(Session, "after_begin", _set_tenant_guc)


install_connection_listeners()
