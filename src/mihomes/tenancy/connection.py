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

__all__ = ["ACCOUNT_GUC", "USER_GUC", "install_connection_listeners"]

ACCOUNT_GUC = "app.current_account"
USER_GUC = "app.current_user"


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
