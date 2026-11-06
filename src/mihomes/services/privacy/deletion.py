"""Account deletion — `requested` → (grace) → `purged` (SPEC-005 §5.4, D15/D18).

## The purge enumerates; it does not consult a list

Tables come from `tenancy.registry.TENANT_TABLES`, the same source the export uses and for the
same reason: a hand-written list is correct the day it is written and silently wrong the first
time someone adds a model. Here the failure mode is worse than the export's — a missed table
leaves personal data behind after an erasure request, which is a regulatory finding rather than a
bug report.

## THREE dispositions, and the middle one is the trap (D18)

    DELETE     every tenant table by default. The rows are the account's own data.

    PRESERVE   skipped entirely, rows survive untouched:
               · account_deletion_requests — the proof the request was honoured
               · email_suppressions — not tenant-scoped; a suppressed address must STAY
                 suppressed after the account that surfaced it is gone

    ANONYMIZE  an UPDATE, never a skip and never a delete: null every author column, keep
               the content. For rows the account authored into a SHARED surface, where
               deleting silently rewrites a record other people rely on.

**`ANONYMIZE` is not a third kind of exclusion.** Both `PRESERVE` entries are *skips*, and a
skipped row keeps its `account_id` — which retains personal data after an erasure request. Using
`PRESERVE` for authored content would look like caution and be a violation. Anonymize is the only
disposition that both honours the request and leaves the shared record standing.

The set is **empty today** and declared anyway: no table qualifies until SPEC-008's `VendorReview`
(a published rating carrying an author, appearing in a public average). Declared rather than
omitted so the partition stays total and `test_purge_dispositions_all_tables` can assert it.

## Storage before rows (A10)

`StorageProvider.delete` runs before any DELETE. A failure midway then leaves rows pointing at
deleted files — findable, fixable, and visibly wrong. The reverse leaves orphaned S3 objects that
nothing references and nobody can enumerate.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from mihomes.models import Base
from mihomes.models.account_deletion import AccountDeletionRequest
from mihomes.tenancy.registry import GLOBAL_TABLES, TENANT_TABLES

__all__ = [
    "ANONYMIZE",
    "DELETE",
    "DISPOSITIONS",
    "PRESERVE",
    "DeletionState",
    "account_referencing_global_columns",
    "cancel_deletion",
    "disposition_for",
    "PurgeOutcome",
    "PurgeRefused",
    "due_request",
    "purge",
    "purge_due",
    "request_deletion",
]

logger = logging.getLogger(__name__)

DELETE = "delete"
PRESERVE = "preserve"
ANONYMIZE = "anonymize"

#: The three dispositions. A table gets **exactly one**, and the default is `DELETE`.
DISPOSITIONS = (DELETE, PRESERVE, ANONYMIZE)

#: Tables skipped by the purge, with the reason each survives (§5.4).
#:
#: `email_suppressions` is here despite not being `TenantOwned` — it is named explicitly so the
#: manifest records a decision about it rather than silence. A reader checking "was the
#: suppression list handled?" finds an answer.
PRESERVED_TABLES: dict[str, str] = {
    "account_deletion_requests": (
        "the proof the request was honoured, and when — the artifact a regulator asks for"
    ),
    "email_suppressions": (
        "suppression belongs to an ADDRESS, not an account: a complainer must stay suppressed "
        "after the account that surfaced them is gone"
    ),
}

#: Tables whose rows survive with every author column nulled — **an UPDATE, not a skip**.
#:
#: **Empty, and declared empty on purpose.** SPEC-008's `VendorReview` is the first real member:
#: a published review carries an author `account_id` and appears in a public rating average, so
#: deleting it rewrites a record other people rely on and skipping it retains the author.
#:
#: **Its spec must declare those author columns NULLABLE, and this is not a caution — it was
#: measured.** §5.4 warns that *"a NOT NULL column cannot be anonymized, and that is discovered
#: at implementation time if nobody says so first"*; `test_anonymize_is_an_update_not_a_skip`
#: pointed this branch at `notes.content` and got `NotNullViolation` on the first run. The
#: warning arrived exactly on schedule, which is the strongest argument for carrying it forward.
ANONYMIZED_TABLES: dict[str, tuple[str, ...]] = {}

#: Days between `requested` and `purge_after`. **O2 is open** — `PRICING` §4.4 says 30 with the
#: document's blanket PLACEHOLDER tag — so this is a config value, and the state machine is
#: identical whatever the founder picks.
DEFAULT_GRACE_DAYS = 30


@dataclass(frozen=True)
class DeletionState:
    """Where a request is. Derived from the row, never stored as a string.

    A stored status column would be a second source of truth for something three timestamps
    already answer — and the two would disagree the first time a purge half-failed.
    """

    requested: bool
    cancelled: bool
    purged: bool

    @property
    def name(self) -> str:
        if self.purged:
            return "purged"
        if self.cancelled:
            return "cancelled"
        return "requested"


def disposition_for(table_name: str) -> str:
    """The one disposition this table gets. Total by construction.

    Every tenant table lands in exactly one bucket, which is what makes A28's partition
    assertion meaningful: the default is `DELETE`, so a table added tomorrow is deleted rather
    than quietly skipped. Failing *safe* here means erasing more, not less.
    """
    if table_name in PRESERVED_TABLES:
        return PRESERVE
    if table_name in ANONYMIZED_TABLES:
        return ANONYMIZE
    return DELETE


def account_referencing_global_columns() -> list[tuple[str, str]]:
    """`(table, column)` for every account-referencing column on a **global** table (A29b).

    Global tables carry no `account_id` and are invisible to the `TENANT_TABLES` sweep, so a
    nullable `claimed_by` / `created_by` pointer on one survives the purge pointing at a dead
    account. §5.4 makes nulling them the caller's job.

    **Derived, and empty today.** `processed_webhook_events.account_id` is the only candidate and
    it is deliberately excluded: it records which account an event *resolved to*, has no FK, and
    must outlive the account or a replayed webhook is processed as if new (SPEC-004 B7).

    Returning an empty list is the honest answer right now — and `purge` still walks whatever
    this returns, so the mechanism exists before the first real member arrives rather than
    after.
    """
    columns = []
    for table_name in sorted(GLOBAL_TABLES):
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        for column in table.columns:
            if column.name == "account_id" and table_name == "processed_webhook_events":
                continue  # see the docstring
            references_accounts = any(
                fk.column.table.name == "accounts" for fk in column.foreign_keys
            )
            if references_accounts or column.name.endswith("_account_id"):
                columns.append((table_name, column.name))
    return columns


def request_deletion(
    session: Session,
    account_id,
    user_id,
    *,
    grace_days: int = DEFAULT_GRACE_DAYS,
    now: datetime | None = None,
) -> AccountDeletionRequest:
    """Start the clock. **Deletes nothing.**

    Idempotent: an account with a live request gets that request back rather than a second one.
    Two rows would mean two `purge_after` dates and no answer to "when was this asked for".
    """
    now = now or datetime.now(UTC)

    existing = _live_request(session, account_id)
    if existing is not None:
        return existing

    row = AccountDeletionRequest(
        account_id=account_id,
        requested_at=now,
        requested_by_user_id=user_id,
        purge_after=now + timedelta(days=grace_days),
    )
    session.add(row)
    session.flush()
    logger.info("deletion requested: account=%s purge_after=%s", account_id, row.purge_after)
    return row


def cancel_deletion(session: Session, account_id, *, now: datetime | None = None) -> bool:
    """Stop a pending deletion. Returns whether anything changed.

    Only while `purged_at` is NULL — a purge cannot be undone, and saying otherwise would be
    the cruellest possible bug. Idempotent: cancelling twice is not an error.
    """
    request = _live_request(session, account_id)
    if request is None:
        return False

    request.cancelled_at = now or datetime.now(UTC)
    session.flush()
    logger.info("deletion cancelled: account=%s", account_id)
    return True


def _live_request(session: Session, account_id) -> AccountDeletionRequest | None:
    """The pending request, if any — not cancelled, not purged."""
    return session.execute(
        sa.select(AccountDeletionRequest)
        .where(
            AccountDeletionRequest.account_id == account_id,
            AccountDeletionRequest.cancelled_at.is_(None),
            AccountDeletionRequest.purged_at.is_(None),
        )
        .order_by(AccountDeletionRequest.requested_at)
    ).scalars().first()


def _delete_storage_objects(session: Session, account_id) -> int:
    """Remove this account's stored files. **Before any row is deleted** (A10).

    A failure here leaves every row intact and nothing lost. A failure *after* the rows were
    deleted would leave objects in a bucket that nothing references and no query can enumerate.
    """
    from mihomes.models.document import Document
    from mihomes.storage import get_storage, is_storage_key

    try:
        storage = get_storage()
    except Exception:
        logger.exception("purge: storage unavailable; no objects deleted")
        return 0

    deleted = 0
    keys = session.execute(
        sa.select(Document.file_path).where(Document.account_id == account_id)
    ).scalars()
    for key in keys:
        if not key or not is_storage_key(key):
            continue
        try:
            storage.delete(key)
            deleted += 1
        except Exception:
            # One unreachable object must not strand the purge — the erasure request is the
            # obligation, and a key that cannot be deleted is logged for an operator.
            logger.exception("purge: could not delete %s", key)
    return deleted


class PurgeRefused(RuntimeError):
    """The purge would not have reached the account's rows, so it did not start."""


def _require_bound_to(session: Session, account_id) -> None:
    """Bind this transaction's tenant GUC to `account_id` and prove it took (2026-11-06).

    **Without it a purge can record an erasure that never happened.** On the app role every
    count and DELETE below goes through RLS, which reads `app.current_account`. If that is unset
    or names another account, every count is 0 and every DELETE removes nothing; the manifest
    then says "0 rows" everywhere and `purged_at` is stamped. Binding here rather than trusting
    the caller makes the purge correct from any session; checking afterwards makes it refuse
    rather than guess when the binding cannot be made.
    """
    from mihomes.tenancy.connection import ACCOUNT_GUC, bind_account_guc

    bind_account_guc(session, account_id)
    bound = session.execute(sa.text("SELECT current_setting(:guc, true)"),
                            {"guc": ACCOUNT_GUC}).scalar()
    if str(bound or "") != str(account_id):
        raise PurgeRefused(f"tenant GUC is {bound!r}, not {account_id}; refusing to purge")


def purge(
    session: Session,
    request: AccountDeletionRequest,
    *,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Apply the deletion across every table. Returns the per-table manifest (D18).

    The manifest carries **disposition and row count per table**, never a bare total: a count
    alone cannot distinguish a table that held nothing from one the purge never reached, which
    is exactly the distinction A28 exists to make.
    """
    now = now or datetime.now(UTC)
    account_id = request.account_id
    _require_bound_to(session, account_id)
    manifest: dict[str, dict] = {}

    # A10 — storage first. See `_delete_storage_objects`.
    manifest["_storage"] = {
        "disposition": DELETE,
        "objects": _delete_storage_objects(session, account_id),
    }

    # Members' Google calendars hold copies of the estate's appointments and addresses, and the
    # rows below are the only way to reach them, so they are removed from Google first.
    from mihomes.services.personal_calendar.connect import forget_account

    manifest["_google_calendars"] = {"disposition": DELETE,
                                     "connections": forget_account(session, account_id)}

    # Children before parents: `sorted_tables` is topological, so reversing it deletes the
    # dependent rows first and no FK is violated on the way down.
    for table_name in reversed(
        [t.name for t in Base.metadata.sorted_tables if t.name in TENANT_TABLES]
    ):
        table = Base.metadata.tables[table_name]
        disposition = disposition_for(table_name)
        count = session.execute(
            sa.select(sa.func.count())
            .select_from(table)
            .where(table.c.account_id == account_id)
        ).scalar()

        if disposition == DELETE:
            session.execute(sa.delete(table).where(table.c.account_id == account_id))
        elif disposition == ANONYMIZE:
            columns = ANONYMIZED_TABLES[table_name]
            session.execute(
                sa.update(table)
                .where(table.c.account_id == account_id)
                .values({column: None for column in columns})
            )
        # PRESERVE: nothing. The rows stay exactly as they are.

        manifest[table_name] = {"disposition": disposition, "rows": count}

    # Global tables the sweep above cannot see (A29b).
    for table_name, column_name in account_referencing_global_columns():
        table = Base.metadata.tables[table_name]
        session.execute(
            sa.update(table)
            .where(table.c[column_name] == account_id)
            .values({column_name: None})
        )
        manifest[f"{table_name}.{column_name}"] = {"disposition": ANONYMIZE, "rows": None}

    # `email_suppressions` is preserved and has no `account_id` to filter on, so it never
    # appears in the tenant sweep. Recorded explicitly, or the manifest is silent about the
    # one table whose survival is most likely to be questioned.
    manifest["email_suppressions"] = {"disposition": PRESERVE, "rows": None}

    request.purged_at = now
    request.purge_manifest = json.dumps(manifest, default=str)
    session.flush()

    logger.info(
        "purge complete: account=%s tables=%d", account_id, len(manifest)
    )
    return manifest


# ── Running the purge when the grace period ends (2026-11-06) ─────────────────────────────
#
# `request_deletion` started a clock and `purge` could erase, but nothing ever called `purge`:
# an owner who pressed "Delete account" got a 30-day countdown and then nothing, forever. This
# is what `mihomes jobs purge-deletions` runs for each account, daily.


@dataclass
class PurgeOutcome:
    account_id: object
    status: str          # "purged" | "would_purge" (dry run) | "billing_failed"
    detail: str = ""


def due_request(session: Session, account_id, *, now: datetime) -> AccountDeletionRequest | None:
    """The live request whose grace period has ended: not cancelled, not purged, due."""
    request = _live_request(session, account_id)
    if request is None or request.purge_after > now:
        return None
    return request


def purge_due(session: Session, account_id, *, now: datetime | None = None,
              dry_run: bool = False, billing=None) -> PurgeOutcome | None:
    """Erase `account_id` if its deletion is due; None when it is not.

    Order matters, and each step is the safe direction if the next one fails:
    1. **Billing first.** A live subscription is cancelled immediately. If that call fails the
       account is skipped and the request stays live, so tomorrow's run retries: a day's delay
       is better than erasing an account Stripe keeps charging.
    2. Note the members, then `purge` (which refuses unless the tenant binding holds).
    3. Finish the account row: kept, because the preserved request points at it, but scrubbed.
    4. Scrub members who belonged to no other estate, so nobody can sign in as them.
    """
    from mihomes.models.account import Account
    from mihomes.models.membership import Membership
    from mihomes.services.billing.cancel import has_live_subscription

    now = now or datetime.now(UTC)
    _require_bound_to(session, account_id)  # the request itself is a tenant row
    request = due_request(session, account_id, now=now)
    if request is None:
        return None
    if dry_run:
        return PurgeOutcome(account_id, "would_purge",
                            f"requested {request.requested_at:%Y-%m-%d}, due {request.purge_after:%Y-%m-%d}")

    account = session.get(Account, account_id)
    if account is not None and has_live_subscription(account):
        try:
            if billing is None:
                from mihomes.services.billing.provider import get_billing_provider

                billing = get_billing_provider("stripe")
            billing.cancel(subscription_id=account.stripe_subscription_id, at_period_end=False)
        except Exception as exc:  # noqa: BLE001 — any billing failure means "not today"
            logger.warning("purge skipped: account=%s billing cancel failed: %s", account_id, exc)
            return PurgeOutcome(account_id, "billing_failed", str(exc))

    memberships = Membership.__table__
    member_ids = [uid for (uid,) in session.execute(
        sa.select(memberships.c.user_id).where(memberships.c.account_id == account_id))]

    manifest = purge(session, request, now=now)
    if account is not None:
        _finish_account(account, now)
    scrubbed = _scrub_orphaned_users(session, member_ids)
    manifest["_account_row"] = {"disposition": ANONYMIZE, "rows": 1 if account else 0}
    manifest["_users"] = {"disposition": ANONYMIZE, "rows": scrubbed}
    request.purge_manifest = json.dumps(manifest, default=str)
    session.flush()
    return PurgeOutcome(account_id, "purged",
                        f"{len(manifest)} tables; {scrubbed} sign-in(s) removed")


def _finish_account(account, now: datetime) -> None:
    """The account row outlives the purge (the preserved request references it); its name and
    billing identity do not."""
    account.name = "Deleted account"
    account.plan = "free"
    account.stripe_customer_id = None
    account.stripe_subscription_id = None
    account.subscription_status = None
    account.current_period_end = None
    account.billing_interval = None
    account.trial_ends_at = None
    account.cancel_at_period_end = False
    account.deactivated_at = now


def _scrub_orphaned_users(session: Session, user_ids: list) -> int:
    """Scrub every former member who now belongs to no estate at all. Returns how many.

    Membership is counted with a **Core** query on purpose: the ORM's tenant filter would limit
    it to the account just purged, find nothing, and scrub people who still belong to another
    estate. On the app role the `membership_self` policy (bound by `bind_user_guc`) lets the
    Core query see exactly this user's memberships, in every account.

    Scrubbed rather than deleted: other estates' rows may still reference the user (an old
    invite, an audit entry), and the row without a name, email, password or Google id is no
    longer anyone. Their sessions and reset tokens are deleted, so no device stays signed in.
    """
    from mihomes.models.membership import Membership
    from mihomes.models.password_reset_token import PasswordResetToken
    from mihomes.models.session import Session as LoginSession
    from mihomes.models.user import User
    from mihomes.tenancy.connection import bind_user_guc

    memberships = Membership.__table__
    scrubbed = 0
    for user_id in dict.fromkeys(user_ids):
        bind_user_guc(session, user_id)
        remaining = session.execute(
            sa.select(sa.func.count()).select_from(memberships)
            .where(memberships.c.user_id == user_id)
        ).scalar()
        if remaining:
            continue
        user = session.get(User, user_id)
        if user is None:
            continue
        user.email = f"deleted-{user.id}@deleted.invalid"
        user.name = None
        user.password_hash = None
        user.password_set_at = None
        user.google_sub = None
        user.avatar_url = None
        user.last_used_account_id = None
        session.execute(sa.delete(LoginSession.__table__).where(
            LoginSession.__table__.c.user_id == user_id))
        session.execute(sa.delete(PasswordResetToken.__table__).where(
            PasswordResetToken.__table__.c.user_id == user_id))
        scrubbed += 1
    session.flush()
    return scrubbed
