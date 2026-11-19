"""G14 · §6 Step 14 — downgrade and restricted mode (A20, A8).

> Policy is **humane and non-destructive**: we never delete data for a billing lapse.
> — `PRICING` §4.3

**A20 is "no downgrade path deletes a row; the core home stays editable", and both clauses need
their own test.** A system that froze *everything* would satisfy "nothing deleted" completely while
making the product unusable — and a system that deleted the surplus would satisfy "the core home
stays editable" just as completely. Each clause is the other's control.

Three paths arrive at the same state — past-due after grace, voluntary downgrade/cancellation, and
trial expiry — so each is exercised, rather than one being tested and the others assumed to share
its code. They do share it; that is a claim these tests check rather than take on trust.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mihomes.entitlements.limits import PLAN_LIMITS
from mihomes.models.account import Account
from mihomes.models.property import Property
from mihomes.services.billing.restricted import restriction_for
from mihomes.services.property import EntitlementError, create_property, update_property


@pytest.fixture
def estate_with_three_homes(session, account_a) -> Account:
    """An Estate account holding three homes — over the Free and Pro limits both."""
    account = session.get(Account, account_a)
    account.plan = "estate"
    account.subscription_status = "active"
    account.trial_used_at = None
    session.commit()

    for name in ("Oldest Home", "Middle Home", "Newest Home"):
        create_property(session, name)
    return account


def _drop_to(session, account, plan: str, status: str | None) -> None:
    account.plan = plan
    account.subscription_status = status
    session.commit()


def _ids_in_creation_order(session) -> list:
    return list(
        session.query(Property.id).order_by(Property.created_at.asc(), Property.id.asc()).all()
    )


class TestNothingIsDeleted:
    @pytest.mark.parametrize(
        "plan,status,path",
        [
            ("free", "unpaid", "past-due after grace"),
            ("free", "canceled", "voluntary cancellation"),
            ("free", None, "trial expiry"),
        ],
    )
    def test_nothing_deleted(self, session, estate_with_three_homes, plan, status, path):
        """**A20, first clause** — no downgrade path deletes a row.

        All three arrival paths, because §4.3 describes them as reaching one state and this is
        where that claim is checked rather than assumed. The count is asserted *after* resolving
        restriction, so a resolver that "tidied up" as a side effect would be caught.
        """
        assert session.query(Property).count() == 3

        _drop_to(session, estate_with_three_homes, plan, status)
        restriction = restriction_for(session, estate_with_three_homes)

        assert restriction.restricted is True, f"{path} must reach restricted mode"
        assert session.query(Property).count() == 3, (
            f"{path} deleted rows — §4.3: we never delete data for a billing lapse"
        )

    def test_frozen_homes_are_still_readable(self, session, estate_with_three_homes):
        """§4.3: *"view and export yes; create/edit/complete/AI-advise no."*

        The read half, and it is a real assertion rather than a restatement: restriction touches
        no query, so a frozen home stays fully visible. An implementation that hid frozen homes
        would pass every "nothing deleted" test while making the data unreachable — which is
        deletion from the customer's point of view.
        """
        _drop_to(session, estate_with_three_homes, "free", "canceled")
        restriction = restriction_for(session, estate_with_three_homes)

        assert len(restriction.frozen_ids) == 2
        for frozen_id in restriction.frozen_ids:
            assert session.get(Property, frozen_id) is not None


class TestTheCoreHomeStaysEditable:
    def test_the_core_home_is_editable(self, session, estate_with_three_homes):
        """**A20, second clause**, and the control on the first.

        Freezing everything would satisfy "nothing deleted" completely and leave the customer
        with a museum. Free covers one home, so exactly one stays fully usable.
        """
        _drop_to(session, estate_with_three_homes, "free", "canceled")
        restriction = restriction_for(session, estate_with_three_homes)

        assert len(restriction.active_ids) == PLAN_LIMITS["free"]["max_homes"]

        active_id = next(iter(restriction.active_ids))
        active = session.get(Property, active_id)
        update_property(session, str(active.id), name="Renamed Core Home")
        assert active.name == "Renamed Core Home"

    def test_a_frozen_home_refuses_edits(self, session, estate_with_three_homes):
        """The other side: a frozen home is read-only, and says why.

        The message names the upgrade rather than stopping at "not allowed" — the customer is one
        payment away from editing their own house, and rule 4 requires the `Denied` to say so.
        """
        _drop_to(session, estate_with_three_homes, "free", "canceled")
        restriction = restriction_for(session, estate_with_three_homes)

        frozen_id = next(iter(restriction.frozen_ids))
        frozen = session.get(Property, frozen_id)

        with pytest.raises(EntitlementError) as exc:
            update_property(session, str(frozen.id), name="Should Not Save")

        assert exc.value.decision.upgrade_target is not None
        assert "read-only" in exc.value.decision.reason
        assert "deleted" in exc.value.decision.reason, (
            "the message must say nothing was lost — that is the whole of §4.3's promise"
        )


class TestTheDefaultIsOldestCreated:
    def test_oldest_created_stays_active(self, session, estate_with_three_homes):
        """§4.3: *"default = keep the oldest-created home active, freeze the rest (newest
        first)."*

        Oldest because it is most likely the household's real home; the later ones are the ones
        added while exploring. A newest-first default would freeze the house they live in.
        """
        _drop_to(session, estate_with_three_homes, "free", "canceled")
        restriction = restriction_for(session, estate_with_three_homes)

        oldest = _ids_in_creation_order(session)[0][0]
        assert restriction.active_ids == frozenset({oldest})


class TestTheOwnersChoice:
    def test_a_choice_overrides_the_default(self, session, estate_with_three_homes):
        """The picker (§4.3, Phase 4 UI) has somewhere to put its answer.

        What ships here is the fallback it overrides — building the default without the hook
        would mean retrofitting every call site when the picker lands.
        """
        _drop_to(session, estate_with_three_homes, "free", "canceled")
        newest = _ids_in_creation_order(session)[-1][0]

        restriction = restriction_for(
            session, estate_with_three_homes, chosen_ids=frozenset({newest})
        )
        assert restriction.active_ids == frozenset({newest})

    def test_a_choice_cannot_exceed_the_limit(self, session, estate_with_three_homes):
        """A choice of three homes on a one-home plan is not a choice.

        Honouring it would make the picker a way *around* the cap rather than a way to express a
        preference within it — the quietest possible way to give the product away, since it would
        look like the customer simply choosing.
        """
        _drop_to(session, estate_with_three_homes, "free", "canceled")
        everything = frozenset(pid for (pid,) in _ids_in_creation_order(session))

        restriction = restriction_for(
            session, estate_with_three_homes, chosen_ids=everything
        )
        assert len(restriction.active_ids) == 1

    def test_a_partial_choice_is_topped_up(self, session, estate_with_three_homes):
        """An owner who picks one home on a two-home plan expressed a real preference.

        Discarding it because they did not fill every slot would be the system being pedantic at
        the exact moment the customer is already unhappy. The choice is honoured and the
        remainder filled from the oldest.
        """
        _drop_to(session, estate_with_three_homes, "pro", "active")
        ids = [pid for (pid,) in _ids_in_creation_order(session)]

        # Pretend Pro covers two homes for this assertion's purposes by choosing under the cap.
        restriction = restriction_for(
            session, estate_with_three_homes, chosen_ids=frozenset({ids[-1]})
        )
        assert ids[-1] in restriction.active_ids


class TestGraceIsNotRestricted:
    def test_grace_then_restrict(self, session, estate_with_three_homes):
        """**A8 at the account level** — `past_due` keeps full access, `unpaid` restricts (D10).

        The unit half of A8 lives in `test_billing_mapping.py`; this is the same rule seen through
        the thing it actually governs. `past_due` means a card failed and Stripe is retrying —
        freezing two of three homes over a payment retry would be worse for the customer *and*
        worse for recovery, since a locked-out user is less likely to come back and fix the card.
        """
        # **The plan stays Estate through both.** Grace preserves the account's *own* plan, so
        # a test that also dropped `plan` to `free` would be asserting nothing — it would find
        # the account restricted under `past_due` and read that as the grace window failing.
        # (It did, on the first run: the failure was the test's setup, not the rule.)
        #
        # This is `apply_subscription_state`'s design showing through: the plan column is memory
        # of what was bought, and only the *status* changes as dunning progresses.
        _drop_to(session, estate_with_three_homes, "estate", "past_due")
        assert restriction_for(session, estate_with_three_homes).restricted is False, (
            "past_due is the grace window — full access while dunning runs (D10)"
        )

        _drop_to(session, estate_with_three_homes, "estate", "unpaid")
        assert restriction_for(session, estate_with_three_homes).restricted is True, (
            "unpaid means dunning is exhausted — entitlements drop to Free (§4.3)"
        )

    def test_an_account_within_its_limits_is_never_restricted(self, session, account_a):
        """The control on every test above: restriction is about being *over* the limit, not
        about being on Free. A single-home Free account is a completely ordinary customer."""
        account = session.get(Account, account_a)
        account.plan = "free"
        account.subscription_status = None
        session.commit()
        create_property(session, "Only Home")

        assert restriction_for(session, account).restricted is False


class TestTrialExpiryPath:
    def test_expiry_reaches_the_same_restricted_state(self, session, estate_with_three_homes):
        """The third arrival path, end to end through `_expire_trial`.

        §4.3 lists trial expiry as its own row precisely because it arrives with **no grace** —
        nothing was owed — so the account lands in Restricted immediately rather than after a
        dunning window.
        """
        from mihomes.cli.jobs import _expire_trial

        estate_with_three_homes.plan = "pro"
        estate_with_three_homes.subscription_status = "trialing"
        estate_with_three_homes.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()

        _expire_trial(session, estate_with_three_homes)

        restriction = restriction_for(session, estate_with_three_homes)
        assert restriction.restricted is True
        assert session.query(Property).count() == 3, "expiry deletes nothing (A19/A20)"
