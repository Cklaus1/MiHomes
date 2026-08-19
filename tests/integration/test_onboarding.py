"""G11 · §6 Step 11 — the 6-step onboarding flow (A17, A18).

`ONBOARDING` §5's goal: *"A brand-new user (no memberships) becomes an owner with one home and a
live dashboard in under two minutes."*

**A17 is about dropping off, not about walking the happy path.** Steps 2 and 3 are the only hard
requirements, and *"if the user drops off after step 2, next sign-in resumes at step 3."*

**A18 is about skipping being a first-class path**, not a dead end: *"Step 5 is always shown but
is not required; skipping is a first-class path."* A flow that only ends when every step is
completed can never end for a user who skips one.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.models.property import Property
from mihomes.models.user import User
from mihomes.services import onboarding_service as onboarding
from mihomes.services.onboarding_service import (
    STEP_ADD_HOME,
    STEP_CREATE_ACCOUNT,
    STEP_DASHBOARD,
    STEP_INVITE,
    STEP_SPACES,
)


def _add_property(session, account_id, name: str = "Belle Estate"):
    """Seed a property **inside the new account's tenant context**.

    The `session` fixture binds `account_a`, but onboarding creates its own account — so an
    insert made under the ambient context would be stamped to the wrong tenant by the G8.3
    listener and then filtered out of every subsequent read. Binding explicitly is what the
    application does too (`onboarding_service.current_step`).
    """
    from mihomes.tenancy import account_context

    with account_context(account_id):
        session.add(
            Property(
                id=uuid.uuid4(), account_id=account_id, name=name,
                slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
            )
        )
        session.flush()


@pytest.fixture
def fresh_user(session):
    """A signed-in user with **no memberships** — the state onboarding exists to resolve."""
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"new-{uuid.uuid4().hex[:6]}@example.com",
        name="Dana Belle",
    )
    session.add(user)
    session.flush()
    return user


class TestResumability:
    def test_resumable(self, session, fresh_user):
        """A17 — drop off after step 2, resume at step 3.

        The account exists and has no property, which is exactly the state
        `ONBOARDING` §5 names. Nothing is replayed: the user does not re-create their account.
        """
        account = onboarding.create_account_step(session, fresh_user, "The Belle Household")

        assert onboarding.current_step(session, account.id) == STEP_ADD_HOME

    def test_resume_is_derived_from_the_world_for_mandatory_steps(self, session, fresh_user):
        """Ground truth beats the record where ground truth exists.

        If the account somehow has a property but the step was never recorded — a crash between
        the insert and the flush, a property created by the importer, a second tab — the user must
        not be sent back to "add your first home". Deriving the mandatory steps from the data is
        what makes that impossible.
        """
        account = onboarding.create_account_step(session, fresh_user, "Belle")
        _add_property(session, account.id)

        # STEP_ADD_HOME was never recorded as completed, yet the home exists.
        state = onboarding.get_state(session, account.id)
        assert STEP_ADD_HOME not in state.completed_steps
        assert onboarding.current_step(session, account.id) == STEP_SPACES

    def test_completing_a_step_twice_is_not_an_error(self, session, fresh_user):
        """Idempotent — *"onboarding is idempotent/resumable"*.

        A refresh-and-resubmit must not duplicate the entry or corrupt the position.
        """
        account = onboarding.create_account_step(session, fresh_user, "Belle")
        onboarding.complete_step(session, account.id, STEP_SPACES)
        onboarding.complete_step(session, account.id, STEP_SPACES)

        state = onboarding.get_state(session, account.id)
        assert state.completed_steps.count(STEP_SPACES) == 1


class TestSkippingIsFirstClass:
    def test_skip_optional(self, session, fresh_user):
        """A18 — skipping steps 4 and 5 lands on the dashboard.

        `finish()` is what makes this work: the user has *not* completed 4 or 5 and never will,
        so a flow that ended only on full completion would strand them forever.
        """
        account = onboarding.create_account_step(session, fresh_user, "Belle")
        _add_property(session, account.id)

        onboarding.finish(session, account.id)

        assert onboarding.current_step(session, account.id) == STEP_DASHBOARD
        state = onboarding.get_state(session, account.id)
        assert STEP_SPACES not in state.completed_steps, (
            "skipping must not be recorded as completing — they are different states"
        )
        assert STEP_INVITE not in state.completed_steps

    def test_optional_steps_are_offered_before_finishing(self, session, fresh_user):
        """The positive control: they are *offered*, not silently bypassed.

        Without this, a `current_step` that jumped straight to the dashboard would satisfy A18
        while never showing the user steps 4 and 5 at all.
        """
        account = onboarding.create_account_step(session, fresh_user, "Belle")
        _add_property(session, account.id)

        assert onboarding.current_step(session, account.id) == STEP_SPACES
        onboarding.complete_step(session, account.id, STEP_SPACES)
        assert onboarding.current_step(session, account.id) == STEP_INVITE


class TestMandatoryStepsAndDefaults:
    def test_account_creation_makes_exactly_one_active_owner(self, session, fresh_user):
        """D1/D2 — ownership is *created* here, never assigned.

        SPEC-002 D4's partial unique index enforces one active owner per account; this asserts
        the flow produces exactly that, in the same transaction as the account.
        """
        from mihomes.models.membership import Membership
        from mihomes.tenancy import account_context

        account = onboarding.create_account_step(session, fresh_user, "Belle")

        # Read inside the new account's context: `Membership` is TenantOwned, so the ambient
        # `account_a` binding from the `session` fixture would filter these rows away.
        with account_context(account.id):
            owners = (
                session.query(Membership)
                .filter(
                    Membership.account_id == account.id,
                    Membership.role == "owner",
                    Membership.status == "active",
                )
                .all()
            )
        assert len(owners) == 1
        assert owners[0].user_id == fresh_user.id

    def test_defaults_minimise_friction(self, session, fresh_user):
        """*"Default type to `household`"*, and the Free plan — *"billing never blocks"*."""
        account = onboarding.create_account_step(session, fresh_user, "Belle")
        assert account.type == "household"
        assert account.plan == "free"

    def test_account_name_is_prefilled_from_the_google_profile(self, fresh_user):
        """*"Prefill account name from the Google profile ('The <LastName> Household')."*"""
        assert onboarding.suggested_account_name(fresh_user) == "The Belle Household"

    def test_prefill_falls_back_rather_than_returning_empty(self):
        """An empty prefill is worse than a generic one the user overwrites in a single tap."""

        class _NamelessUser:
            name = None

        assert onboarding.suggested_account_name(_NamelessUser()) == "My Household"

    def test_blank_submitted_name_falls_back_to_the_suggestion(self, session, fresh_user):
        """The mandatory screen must be completable in *one tap* — submitting the prefilled
        field empty must not produce a nameless account."""
        account = onboarding.create_account_step(session, fresh_user, "   ")
        assert account.name == "The Belle Household"

    def test_new_account_starts_at_step_two(self, session, fresh_user):
        """A user with no account resumes at the first mandatory step, not at the welcome
        screen — step 1 is non-interactive (`—` in the source table), so there is nothing to
        resume to."""
        assert onboarding.current_step(session, uuid.uuid4()) == STEP_CREATE_ACCOUNT
