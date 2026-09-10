"""The 6-step onboarding flow — SPEC-003 §6 Step 11, `ONBOARDING` §5 (A17, A18).

*"A brand-new user (no memberships) becomes an owner with one home and a live dashboard in under
two minutes."* Onboarding is a **guided wrapper** over existing domain operations — it introduces
no new domain concepts, and every step below delegates to the service that already owns its data.

**Steps 2 and 3 are the only hard requirements.** Steps 1 and 6 are non-interactive screens (the
source marks them `—`, not "Yes"), and 4 and 5 are skippable with **skipping as a first-class
path** — not a dead end the user backs out of.

**Billing never blocks onboarding** (`ONBOARDING:143`). The account is created on the Free plan
and Phase 3 supplies billing state; nothing here consults it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.account import Account
from mihomes.models.membership import Membership
from mihomes.models.onboarding_state import OnboardingState
from mihomes.models.property import Property

__all__ = [
    "STEP_ADD_HOME",
    "STEP_CREATE_ACCOUNT",
    "STEP_DASHBOARD",
    "STEP_INVITE",
    "STEP_SPACES",
    "STEP_WELCOME",
    "complete_step",
    "create_account_step",
    "current_step",
    "finish",
    "suggested_account_name",
]

STEP_WELCOME = 1
STEP_CREATE_ACCOUNT = 2
STEP_ADD_HOME = 3
STEP_SPACES = 4
STEP_INVITE = 5
STEP_DASHBOARD = 6

#: Steps a user cannot skip. Everything else is optional by design, so "not completed" is not the
#: same as "not finished" — see `current_step`.
MANDATORY_STEPS = (STEP_CREATE_ACCOUNT, STEP_ADD_HOME)


logger = logging.getLogger(__name__)


def suggested_account_name(user) -> str:
    """*"Prefill account name from the Google profile ('The <LastName> Household')."*

    Minimising friction on the mandatory steps is the stated design goal: each screen should be
    *"completable in one tap plus at most one text field."* A blank name field costs a keystroke
    on the one screen nobody can skip.

    Falls back to a generic name rather than an empty string — an empty prefill is worse than a
    wrong one the user can overwrite in a single tap.
    """
    name = (getattr(user, "name", None) or "").strip()
    if name:
        last = name.split()[-1]
        return f"The {last} Household"
    return "My Household"


def get_state(session: Session, account_id: uuid.UUID) -> OnboardingState:
    """The account's onboarding row, created on first read.

    Created lazily rather than at account creation so that accounts made by other paths — the
    CLI bootstrap, the importer, a test fixture — do not need to know this table exists.

    **Binds the tenant explicitly.** `OnboardingState` is `TenantOwned`, so both the read and the
    insert go through the G8 filter and the G8.3 stamp listener — and onboarding runs precisely
    when the session has not selected an account yet. Binding the account we were *given* is what
    lets this work before the ambient context exists, and it is safe because the caller reached
    this account through the signed-in user's own membership.
    """
    from mihomes.tenancy import account_context

    with account_context(account_id):
        state = session.get(OnboardingState, account_id)
        if state is None:
            state = OnboardingState(account_id=account_id, completed_steps=[])
            session.add(state)
            session.flush()
        return state


def complete_step(session: Session, account_id: uuid.UUID, step: int) -> OnboardingState:
    """Record a step as done. **Idempotent** — completing twice is not an error.

    A user who refreshes the spaces screen and submits again must not end up with a duplicate
    entry or a corrupted position in the flow.
    """
    from mihomes.tenancy import account_context

    state = get_state(session, account_id)
    with account_context(account_id):
        if step not in state.completed_steps:
            # Reassign rather than mutate: a JSON column tracked by value does not see in-place
            # list mutation, so `.append()` would be silently lost on flush.
            state.completed_steps = [*state.completed_steps, step]
        session.flush()
    return state


def current_step(session: Session, account_id: uuid.UUID) -> int:
    """Where this account resumes — A17.

    **Derived from the world, then from the record, in that order.** The mandatory steps are
    checked against reality (does the account exist, does it have a property) because that is the
    ground truth a crash or a concurrent tab cannot desynchronise. The optional steps are checked
    against `completed_steps`, because for them there is no observable difference between *not yet
    asked* and *asked and declined* — inferring would re-prompt someone who already skipped.
    """
    # The account check comes **first**, before `get_state` would insert a row: `onboarding_state`
    # is keyed on `accounts.id` by a real foreign key, so asking "where does this account resume"
    # for an account that does not exist must answer "at step 2", not raise a FK violation.
    if session.get(Account, account_id) is None:
        return STEP_CREATE_ACCOUNT

    state = get_state(session, account_id)

    # **Bound explicitly to the account being asked about, not to whatever is ambient.**
    # `Property` is `TenantOwned`, so the G8 filter constrains it to the *current* tenant — and
    # during onboarding the session has often not selected an account yet, or has selected a
    # different one. Without this the query returns nothing and the user is sent back to "add
    # your first home" on an account that already has one. The explicit `account_id` predicate
    # stays as well: belt and braces, and it documents the intent at the query.
    from mihomes.tenancy import account_context

    with account_context(account_id):
        has_property = session.execute(
            select(Property.id).where(Property.account_id == account_id).limit(1)
        ).first()
    if has_property is None:
        return STEP_ADD_HOME

    if state.finished_at is not None:
        return STEP_DASHBOARD

    for step in (STEP_SPACES, STEP_INVITE):
        if step not in state.completed_steps:
            return step

    return STEP_DASHBOARD


def finish(session: Session, account_id: uuid.UUID) -> OnboardingState:
    """Land on the dashboard (step 6). Idempotent.

    Setting `finished_at` is what makes skipping stick: a user who skipped steps 4 and 5 has not
    "completed" them and never will, so completion alone could never end the flow.
    """
    from mihomes.tenancy import account_context

    state = get_state(session, account_id)
    with account_context(account_id):
        if state.finished_at is None:
            state.finished_at = datetime.now(timezone.utc)
        session.flush()
    return state


def create_account_step(
    session: Session, user, name: str, account_type: str = "household"
) -> Account:
    """Step 2 — the account plus its **owner** membership, in one transaction.

    The two are inseparable: an account with no owner is unreachable by anyone, and D1 requires
    exactly one *active* owner. Creating them apart would leave a window in which a crash orphans
    the account — and SPEC-002 D4's partial unique index would then refuse the second attempt.

    Defaults to `type='household'` and the Free plan, per `ONBOARDING` §5. **Ownership is created
    here, never assigned** — D2: the `owner` role can only arrive this way or by transfer.
    """
    from mihomes.ids import new_id
    from mihomes.services.slug import generate_slug

    account = Account(
        id=new_id(),
        slug=f"{generate_slug(name)}-{uuid.uuid4().hex[:6]}",
        name=name.strip() or suggested_account_name(user),
        type=account_type,
        plan="free",
    )
    session.add(account)
    session.flush()

    # **Bind the GUC to the account just created, or RLS refuses the owner membership.**
    #
    # This is the one write in the app that creates a tenant *and* its first tenant-owned row
    # in a single transaction, and the two halves want opposite context. `accounts` is GLOBAL,
    # so it inserts with no tenant bound — which is why onboarding runs as `Access.SESSION`
    # with `current_account` deliberately unset (`web/deps.py`: "resolving one first would 403
    # every screen that needs to run before it exists"). But `memberships` is tenant-owned and
    # FORCE-protected, so its `WITH CHECK (account_id = current_setting('app.current_account',
    # true)::uuid)` compares against NULL and rejects the row:
    #
    #     InsufficientPrivilege: new row violates row-level security policy
    #     for table "memberships"
    #
    # Measured: **onboarding step 2 failed for every new account** on any install where RLS is
    # actually enforced — i.e. every real deployment, since `alembic upgrade head` runs
    # `0002_rls`. It passed everywhere in testing because the suite's fixtures connect as a
    # superuser, which bypasses RLS unconditionally (`rls.py` records that measurement).
    #
    # **Bound via the ContextVar, not raw `set_config`, and that distinction is the fix.**
    #
    # A `SELECT set_config('app.current_account', …, true)` here works for the very next
    # statement and is then silently undone: `tenancy/connection.py` stamps the GUC on
    # SQLAlchemy's `after_begin` event, which fires again for **every SAVEPOINT**. So
    # `campaigns.enrol`'s `session.begin_nested()` re-stamps the GUC from `current_account` —
    # unset on this route — wiping it back to empty mid-transaction. Measured: with
    # `mihomes.tenancy` imported, the value reads back as `''` inside the savepoint.
    #
    # Setting the ContextVar makes the listener itself stamp the right account, so every
    # statement *and* every nested savepoint in the rest of this transaction is covered.
    # **Both halves are required, and each covers what the other misses.** The GUC is stamped
    # on `after_begin`, so setting the ContextVar alone does nothing for the transaction that is
    # *already* open — measured: the value still reads `''` on the next statement. And a bare
    # `set_config` alone is undone by the next SAVEPOINT, as above. So: stamp this transaction
    # directly, and set the var so every later `after_begin` (i.e. `enrol`'s savepoint) stamps
    # the same account.
    from sqlalchemy import text as _sa_text

    from mihomes.tenancy import current_account as _current_account_var

    _token = _current_account_var.set(account.id)
    try:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                _sa_text("SELECT set_config('app.current_account', :acct, true)"),
                {"acct": str(account.id)},
            )

        session.add(
            Membership(
                id=new_id(),
                account_id=account.id,
                user_id=user.id,
                role="owner",
                status="active",
            )
        )
        session.flush()

        complete_step(session, account.id, STEP_CREATE_ACCOUNT)

        # SPEC-005 Step 11 — *"enrolment on account creation"*. **No §8 criterion covers
        # this**: A25 is "each step sends once and never twice", which a drip system with zero
        # enrolments satisfies perfectly. Wired here rather than recorded as a gap, because the
        # seam is one line and the alternative is a mechanism nothing ever starts (harness
        # §2.2 D17).
        #
        # Failures are swallowed: a marketing sequence must never be able to fail account
        # creation, which is the one irreversible step in onboarding. **That swallow is also
        # what hid this bug**: `campaign_enrolments` is tenant-owned, so before the binding
        # above its insert was refused by RLS and the failure went to the log rather than the
        # response. Onboarding "worked" while silently enrolling nobody.
        #
        # Inside the bound block deliberately — `enrol` opens a SAVEPOINT, which re-triggers
        # the `after_begin` stamp, so it needs the ContextVar set rather than a bare
        # `set_config` that the savepoint would overwrite.
        try:
            from mihomes.services.email.campaigns import enrol

            enrol(session, account.id, "onboarding")
        except Exception:
            logger.exception("could not enrol account %s in the onboarding drip", account.id)
    finally:
        # Reset rather than leave bound: this service is also called from the CLI and tests,
        # where a leaked account context would silently scope a later unrelated write.
        _current_account_var.reset(_token)

    return account
