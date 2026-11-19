"""G13 — how a CLI process acquires an account.

Every write needs a tenant (G8.3 stamps `account_id` and raises `LookupError` without one), and
until now **nothing in the CLI supplied one**: `mihomes init`, demo seeding, and all 48
`test_cli.py` cases failed with `LookupError: current_account`. That was recorded as launch gate
S3.

**The resolution rule, in order:**

1. an explicit ``--account <slug>`` — required when the install has more than one account;
2. otherwise the **sole** account, if exactly one exists;
3. otherwise raise, naming the available slugs.

Rule 2 is what keeps a single-user install free of ceremony: the local CLI has one account and
should never make you say so. Rule 3 is why it is safe — the moment a second account exists the
implicit choice stops, rather than silently picking the first row and writing another tenant's
data. **A `LIMIT 1` default would be the bug here**, and it would be invisible until someone had
two accounts.

`accounts` is a GLOBAL table, so these queries work with no tenant bound: the G8 read filter
checks `state.all_mappers` and skips statements that touch no `TenantOwned` entity. Bootstrapping
the first account therefore does not require the context it exists to create — which is the
circularity that made the unconditional version of that filter unworkable (see
`tenancy/session.py`).
"""

from __future__ import annotations

import uuid

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = [
    "DEFAULT_ACCOUNT_NAME",
    "DEFAULT_ACCOUNT_SLUG",
    "AccountResolutionError",
    "ensure_default_account",
    "resolve_account",
]

DEFAULT_ACCOUNT_SLUG = "default"
DEFAULT_ACCOUNT_NAME = "My Estate"


class AccountResolutionError(RuntimeError):
    """Raised when the account cannot be determined without guessing."""


def ensure_default_account(engine: Engine) -> uuid.UUID:
    """Create the local account if the install has none. Idempotent; returns its id.

    Called from `init_db()` so that **every** path which initialises a database gets an account
    — the CLI, the demo seeder, and the test fixtures that call `init_db()` directly. Putting it
    in the CLI callback alone would leave anything that bypasses the callback (a service called
    straight from a test) still unable to write.

    Deliberately does nothing when an account already exists, including when several do: this is
    a bootstrap, not a default-setter, and inventing an extra account on a multi-account install
    would be surprising.
    """
    from mihomes.models.account import Account

    with Session(engine) as session:
        existing = session.query(Account).order_by(Account.created_at).first()
        if existing is not None:
            return existing.id
        account = Account(
            slug=DEFAULT_ACCOUNT_SLUG,
            name=DEFAULT_ACCOUNT_NAME,
            type="household",
            plan="free",
        )
        session.add(account)
        session.commit()
        return account.id


def resolve_account(session: Session, slug: str | None = None) -> uuid.UUID:
    """Resolve the account a CLI invocation should run as. See the module docstring for the rule."""
    from mihomes.models.account import Account

    if slug:
        account = session.query(Account).filter(Account.slug == slug).one_or_none()
        if account is None:
            available = [a.slug for a in session.query(Account).order_by(Account.slug)]
            raise AccountResolutionError(
                f"No account with slug {slug!r}."
                + (f" Available: {', '.join(available)}" if available else " No accounts exist.")
            )
        return account.id

    accounts = session.query(Account).order_by(Account.created_at).all()
    if len(accounts) == 1:
        return accounts[0].id
    if not accounts:
        raise AccountResolutionError(
            "No accounts exist. Run `mihomes init` to create one."
        )
    # More than one: refuse rather than pick. Picking would write to whichever account happened
    # to be created first, and nothing in the output would say which.
    slugs = ", ".join(a.slug for a in accounts)
    raise AccountResolutionError(
        f"This install has {len(accounts)} accounts, so --account is required. "
        f"Available: {slugs}"
    )
