"""Account-level operations — currently just the rename.

**Why this file exists at all.** Onboarding step 2 tells the user *"You can change this
later"* under the household-name field, and nothing in the app could. There was no route that
wrote `accounts.name`, and `/settings` offered only a raw config key/value form — so the only
way to rename an estate was `UPDATE accounts SET name = …` by hand. That promise is now kept.

The rename lives in a service rather than in the route for the reason every other write does:
the audit entry and the validation belong with the operation, so the CLI or a future API path
gets them too rather than reimplementing them.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from mihomes.models.account import Account
from mihomes.services.audit import record_change

__all__ = ["AccountNameError", "MAX_NAME_LENGTH", "rename_account"]

#: Matches the column. Enforced here rather than left to the database, so an over-long name is a
#: readable refusal instead of a `StringDataRightTruncation` from three frames inside a flush.
MAX_NAME_LENGTH = 200


class AccountNameError(ValueError):
    """The proposed name is not usable."""


def rename_account(session: Session, account_id: uuid.UUID, name: str) -> Account:
    """Rename an account. **Does not commit.**

    **The slug deliberately does not change.** It is the account's stable identifier — it
    appears in `--account <slug>` on every CLI invocation, and `resolve_account` looks installs
    up by it. Renaming an estate is a display change; silently rebuilding the slug would break
    whatever the operator had scripted, and it is the kind of breakage that shows up later and
    somewhere else. `slug` is also `UNIQUE`, so a rebuild could collide and fail a rename that
    has nothing wrong with it.

    Audited, because "who renamed the estate and when" is exactly the sort of question the audit
    trail exists for, and the old value is otherwise unrecoverable.
    """
    cleaned = name.strip()
    if not cleaned:
        raise AccountNameError("The estate name cannot be empty.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise AccountNameError(
            f"The estate name is too long — {len(cleaned)} characters, "
            f"and the limit is {MAX_NAME_LENGTH}."
        )

    account = session.get(Account, account_id)
    if account is None:
        # Not an assertion: a revoked account between the role gate and this read is a live
        # race, and failing with a clear error beats an AttributeError on None.
        raise AccountNameError("That account no longer exists.")

    previous = account.name
    if previous == cleaned:
        # No write and no audit row: a form re-submit with the same value is not a change, and
        # recording it would fill the trail with events that did nothing.
        return account

    account.name = cleaned
    session.flush()

    # **`account_context`, because `audit_log` is a tenant table.** The rename itself touches
    # only `accounts`, which is GLOBAL and needs no tenant bound — but the audit row does, and
    # `_stamp_tenant_on_insert` reads `current_account` to fill its `account_id`, raising
    # `LookupError` when unset (fail closed, by design). Measured: without this the rename
    # succeeded and then died on the audit insert, so the name changed and the trail did not.
    #
    # Bound around the audit write rather than the whole function so the validation above still
    # runs with whatever context the caller had.
    from mihomes.tenancy import account_context

    with account_context(account.id):
        record_change(
            session,
            entity_type="account",
            entity_id=account.id,
            action="rename",
            changes={"name": {"from": previous, "to": cleaned}},
        )
        session.flush()
    return account
