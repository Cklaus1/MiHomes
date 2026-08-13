"""Session-level tenant enforcement (SPEC-002 §4.4).

Three listeners, all installed on the `Session` **class** so nothing in the process can
hold an unscoped session:

  G8.1/G8.2  `do_orm_execute` — filters SELECT *and* ORM UPDATE/DELETE   (A5, A6)
  G8.3       `before_flush`   — stamps `account_id` on insert            (A7)

**Fail closed, always.** `current_account.get()` raises `LookupError` when no tenant is
bound, and this module lets that propagate. A version that caught it and skipped the filter
would look tidier and would be the bug: an unscoped query returns *other tenants' rows*,
which is worse than an exception by every measure. The exception **is** the safety property.

**N2 — the guard covers UPDATE and DELETE, not just SELECT.** `with_loader_criteria` applies
to ORM-enabled bulk `update()`/`delete()` as well, so guarding on `is_select` alone leaves
`session.query(Task).delete()` unscoped: a cross-tenant **write** path. A read leak exposes
data; a write leak destroys another tenant's data.

**Why the account is read outside the lambda — §4.4's snippet does not run.** The spec writes
`lambda cls: cls.account_id == current_account.get()`. Measured: SQLAlchemy rejects that
outright with

    InvalidRequestError: Can't invoke Python callable get() inside of lambda expression
    argument ...; lambda SQL constructs should not invoke functions from closure variables to
    produce literal values since the lambda SQL system normally extracts bound values without
    actually invoking the lambda or any functions within it. Call the function outside of the
    lambda and assign to a local variable that is used in the lambda as a closure variable...

So the fix below is not a precaution against a subtle caching bug — it is the form SQLAlchemy
itself prescribes, and the spec's version raises on the first query. Reading into a local
makes `account_id` an ordinary closure variable, which the lambda system binds per execution,
and it evaluates `current_account.get()` on *every* statement so the fail-closed check happens
when it should. `test_filter_is_not_cached_across_accounts` runs one query shape under two
accounts in sequence, which is what would catch a stale bound value if the mechanism ever
changed to silent caching rather than a hard error.

**What this does NOT cover: the two Core association tables.** `with_loader_criteria` takes a
mapped class, and `staff_properties` / `vendor_properties` have none — so the ORM filter
cannot reach them. Their protection is RLS alone, which means it is only real on a
non-superuser connection (see `tenancy/rls.py`). The same blind spot the registry exists for,
showing up a third time.
"""

from __future__ import annotations

import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from mihomes.models import TenantOwned
from mihomes.tenancy.context import current_account

__all__ = [
    "SKIP_TENANT",
    "association_account_default",
    "install_tenant_listeners",
]

# The one documented way out of the filter, for the paths that legitimately span tenants:
# Alembic migrations, the Step 16 importer, and admin tooling. Spelled as an execution
# option rather than a context flag so every use is visible at the call site — a
# module-level "disable scoping" switch would be reachable from anywhere and invisible in
# review.
SKIP_TENANT = "skip_tenant"


def _apply_tenant_filter(state) -> None:
    """Scope ORM SELECT/UPDATE/DELETE to the current account (§4.4, A5, A6)."""
    # N2: not `is_select` alone — see the module docstring.
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get(SKIP_TENANT):
        return

    # Only demand a tenant when the statement actually involves a tenant-owned entity.
    #
    # §4.4's snippet has no such check, and without it this listener raises `LookupError`
    # for *every* ORM query in the process — including queries that touch only GLOBAL
    # tables, which have no `account_id` to scope by. That is not a corner case:
    #
    #   * `users` and `sessions` are GLOBAL precisely because sign-in must read them
    #     **before** any account exists (D3). An unconditional check makes authentication
    #     impossible — the same bootstrap problem `membership_self` exists to solve.
    #   * `waitlist` belongs to the standalone landing app, whose sessions are the same
    #     `Session` class this listener is bound to. It broke all 10 SPEC-001 landing tests.
    #
    # `all_mappers` covers the entities at the top level of the statement, so a join from a
    # global table to a tenant table still includes the tenant mapper and is still filtered.
    if not any(issubclass(m.class_, TenantOwned) for m in state.all_mappers):
        return

    # Read *outside* the lambda: SQLAlchemy refuses a function call inside a criteria lambda
    # (see the module docstring). Raises LookupError with no context — fail closed.
    account_id = current_account.get()
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantOwned,
            lambda cls: cls.account_id == account_id,
            include_aliases=True,
        )
    )


def _stamp_tenant_on_insert(session: Session, flush_context, instances) -> None:
    """Stamp `account_id` on new TenantOwned rows (§4.4, A7).

    Only when it is None: an explicit account_id is respected, which is what lets
    the importer (Step 16) and admin tooling write rows for a specific tenant
    without fighting the listener.
    """
    for obj in session.new:
        if isinstance(obj, TenantOwned) and getattr(obj, "account_id", None) is None:
            # Raises LookupError if unset — fail closed. See the module docstring.
            obj.account_id = current_account.get()


def association_account_default() -> uuid.UUID:
    """Column default for the Core association tables' `account_id`.

    **`before_flush` cannot reach those rows.** It iterates `session.new`, which
    holds mapped *instances* — but `staff_properties` and `vendor_properties` have no
    declarative class, so appending to `staff.properties` emits an INSERT with no
    corresponding object. The row went in with a NULL `account_id` and hit the
    constraint.

    Same blind spot the registry exists for, one layer down: a mixin cannot see a
    Core `Table`, and neither can a listener that works on instances. A Python-side
    column `default` *does* fire for Core inserts, so the stamping lives on the
    column itself — declared where the table is, next to the comment explaining why
    `account_id` is hand-written there.

    Raises LookupError with no context, like the ORM path: fail closed.
    """
    return current_account.get()


def install_tenant_listeners() -> None:
    """Register the listeners on the Session class. Idempotent.

    Class-level rather than per-instance so every session in the process is covered,
    including ones created by code that predates tenancy. That breadth is the point:
    a session that escaped the listener would be a session with no tenant scoping.
    """
    if not event.contains(Session, "before_flush", _stamp_tenant_on_insert):
        event.listen(Session, "before_flush", _stamp_tenant_on_insert)
    if not event.contains(Session, "do_orm_execute", _apply_tenant_filter):
        event.listen(Session, "do_orm_execute", _apply_tenant_filter)


# Installed at import so anything that touches mihomes.tenancy gets the behaviour.
install_tenant_listeners()
