"""Session-level tenant enforcement (SPEC-002 §4.4).

**Partially built: G8.3 only.** The `before_flush` insert-stamping listener is here
because G15's fixtures cannot insert a row without it — `TenantOwned` made
`account_id` NOT NULL on 40 tables and nothing was supplying it.

Still to come in G8:

  G8.1  `do_orm_execute` read filter via `with_loader_criteria`   (A5)
  G8.2  the same filter covering ORM bulk UPDATE/DELETE           (A6)

Those two are the *read* half and are not needed to make inserts valid, so they are
deliberately not here — building them now would mean claiming A5/A6 before their
tests exist.

**Fail closed, always.** `current_account.get()` raises `LookupError` when no tenant
is bound, and this module lets that propagate. A version that caught it and skipped
stamping would look tidier and would be the bug: today the NOT NULL constraint
catches an unstamped insert loudly, but the day a column is nullable somewhere, a
silent skip becomes an unscoped write. The exception *is* the safety property.
"""

from __future__ import annotations

import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session

from mihomes.models import TenantOwned
from mihomes.tenancy.context import current_account

__all__ = ["association_account_default", "install_tenant_listeners"]


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


# Installed at import so anything that touches mihomes.tenancy gets the behaviour.
# G8.1/G8.2 will add the read-side listeners alongside this one.
install_tenant_listeners()
