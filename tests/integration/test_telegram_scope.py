"""G16 · §6 Step 16 — Telegram bot scoping (A28–A32).

F5's finding: the bot captures `sender` on every message, reads it at **one** place in the whole
codebase (the PTO approver check), never persists it, and scopes by *chat* instead — so *"any
member of a linked group can ask anything and receives the full property-scoped estate context."*

**This is also where G10's recorded deviation stops being a risk.** The scope travels in a
ContextVar that defaults to *unrestricted*, so a path that binds nothing fails **open** — and the
bot is the one consumer running entirely outside a web request. `sender_authz` binds
unconditionally, and `test_unlinked_binds_the_most_restrictive_combination` is the assertion that
it never falls through to the default.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.property import Property
from mihomes.models.telegram_link import TelegramLink
from mihomes.models.user import User
from mihomes.services import telegram_link_service as links
from mihomes.services.gateways.telegram import financial_guard as guard


def _member(session, account_id, role, status="active") -> Membership:
    user = User(
        id=uuid.uuid4(), google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"{role}-{uuid.uuid4().hex[:6]}@example.com",
    )
    session.add(user)
    session.flush()
    membership = Membership(
        id=uuid.uuid4(), account_id=account_id, user_id=user.id, role=role, status=status,
    )
    session.add(membership)
    session.flush()
    return membership


@pytest.fixture
def belle(session, account_a):
    prop = Property(
        id=uuid.uuid4(), account_id=account_a, name="Belle Estate",
        slug=f"belle-{uuid.uuid4().hex[:6]}",
    )
    session.add(prop)
    session.flush()
    return prop


class TestSenderResolution:
    def test_unlinked_is_staff(self, session, account_a):
        """A28 · D16 — an unlinked sender is **staff-level, not denied**.

        A deliberate departure from `TELEGRAM_PRD:158`'s deny-by-default, and the reason is
        operational: on day one no links exist, so deny-by-default silences the bot for the entire
        Belle group — *including the founder*. Staff-level closes the leak and makes linking an
        upgrade rather than a prerequisite.
        """
        from mihomes.authz.scope import current_property_scope, current_role

        with links.sender_authz(session, 99999, account_a) as membership:
            assert membership is None
            assert current_role.get() == "staff"
            assert current_property_scope.get() == frozenset()

    def test_unlinked_binds_the_most_restrictive_combination(self, session, account_a):
        """**The assertion that closes G10's deviation for the bot.**

        `current_property_scope` defaults to `None` meaning *unrestricted*, so the failure mode
        here is not "denied" but "sees everything". D16 says staff-*level*; the empty scope is
        what makes that mean "zero properties" rather than "all of them" (D3).
        """
        from mihomes.authz.scope import current_property_scope

        with links.sender_authz(session, 12345, account_a):
            scope = current_property_scope.get()

        assert scope is not None, (
            "an unlinked sender must not inherit the unrestricted default — that is fail-open"
        )
        assert scope == frozenset()

    def test_no_sender_at_all_is_also_staff_level(self, session, account_a):
        """A channel post or an edited message may carry no `from` at all."""
        from mihomes.authz.scope import current_property_scope, current_role

        with links.sender_authz(session, None, account_a):
            assert current_role.get() == "staff"
            assert current_property_scope.get() == frozenset()

    def test_linked_sender_gets_their_own_role_and_scope(self, session, account_a, belle):
        """The positive control: linking is what upgrades you."""
        from mihomes.authz.scope import current_property_scope, current_role

        member = _member(session, account_a, "staff")
        session.add(
            MembershipPropertyScope(
                id=uuid.uuid4(), account_id=account_a,
                membership_id=member.id, property_id=belle.id,
            )
        )
        links.link_sender(session, account_a, member.id, 555001)
        session.flush()

        with links.sender_authz(session, 555001, account_a) as resolved:
            assert resolved is not None
            assert current_role.get() == "staff"
            assert current_property_scope.get() == frozenset({belle.id})

    def test_a_linked_owner_is_unrestricted(self, session, account_a, belle):
        """owner/admin ignore their scope rows (`ONBOARDING:44`), on the bot as on the web."""
        from mihomes.authz.scope import current_property_scope, current_role

        owner = _member(session, account_a, "owner")
        links.link_sender(session, account_a, owner.id, 555002)
        session.flush()

        with links.sender_authz(session, 555002, account_a):
            assert current_role.get() == "owner"
            assert current_property_scope.get() == frozenset({belle.id})


class TestRevocationCascades:
    def test_revocation_cascades(self, session, account_a):
        """A32 — *"revoking a membership implicitly revokes the link."*

        Revocation is a **status change**, not a delete, so the FK CASCADE alone does not cover
        it. Resolution therefore requires `status = 'active'` as well: two mechanisms, because
        each covers a case the other misses.
        """
        member = _member(session, account_a, "staff")
        links.link_sender(session, account_a, member.id, 555003)
        session.flush()

        assert links.resolve_sender(session, 555003, account_a) is not None

        member.status = "revoked"
        session.flush()

        assert links.resolve_sender(session, 555003, account_a) is None, (
            "a revoked membership must not resolve — the bot closes when the web app closes"
        )

    def test_deleting_the_membership_removes_the_link_row(self, session, account_a):
        """The other mechanism: `ondelete=CASCADE` makes the promise structural.

        `TELEGRAM_PRD:158`'s claim is only true by construction if the link is keyed on the
        membership — which is D19's whole argument for not keying it on `Staff`.
        """
        member = _member(session, account_a, "staff")
        links.link_sender(session, account_a, member.id, 555004)
        session.flush()

        session.execute(
            text("DELETE FROM memberships WHERE id = :id"), {"id": member.id}
        )
        session.flush()

        remaining = session.execute(
            text("SELECT count(*) FROM telegram_links WHERE telegram_user_id = 555004")
        ).scalar_one()
        assert remaining == 0


class TestLinkIsKeyedOnMembershipNotStaff:
    def test_link_targets_a_membership(self):
        """D19/N6 — the two role vocabularies must not be crossed.

        `memberships.role` is `owner`/`admin`/`staff`, the matrix's vocabulary. `StaffRole` is a
        **job** enum containing its own `OWNER`, so resolving a sender through `Staff` and then
        applying a matrix decision would make a `StaffRole.OWNER` housekeeping record an account
        owner. Pinned structurally, because the mistake is one refactor away.
        """
        columns = TelegramLink.__table__.columns
        assert "membership_id" in columns
        assert "staff_id" not in columns

        fk = next(iter(columns["membership_id"].foreign_keys))
        assert fk.column.table.name == "memberships"
        assert fk.ondelete == "CASCADE"


class TestFinancialRefusal:
    def test_staff_financial_refused(self, session, account_a):
        """A29 · D15 — a staff sender's financial question is refused **anywhere**, DM included.

        Refusing with a message rather than silence matters: a bot that ignores the question reads
        as broken, and the person asks a colleague instead.
        """
        answer = guard.screen_financial_answer(
            session, account_a, asker_role="staff",
            question="how much did we spend this month?", is_group=False,
        )
        assert answer == guard.STAFF_REFUSAL

    def test_staff_non_financial_question_is_not_refused(self, session, account_a):
        """The negative control — staff must keep using the bot for their actual work."""
        answer = guard.screen_financial_answer(
            session, account_a, asker_role="staff",
            question="what needs doing at Belle today?", is_group=True,
        )
        assert answer is None

    def test_group_dm_offer(self, session, account_a):
        """A30 · D17 — the half scoping alone cannot fix.

        *"Scoping by asker alone still leaks: the bot replies into a shared group, so an owner's
        answer about monthly spend is read by every staff member in the chat."* The asker is
        authorised; the **audience** is not.
        """
        staff = _member(session, account_a, "staff")
        links.link_sender(session, account_a, staff.id, 700001)
        session.flush()

        answer = guard.screen_financial_answer(
            session, account_a, asker_role="owner",
            question="what did we spend on maintenance?", is_group=True,
            member_telegram_ids=[700001],
        )
        assert answer == guard.DM_OFFER

    def test_owner_gets_the_number_in_a_dm(self, session, account_a):
        """Redirected, not refused — refusing would punish the owner for the room they are in."""
        answer = guard.screen_financial_answer(
            session, account_a, asker_role="owner",
            question="what did we spend?", is_group=False,
        )
        assert answer is None

    def test_owner_only_group_gets_the_answer_in_place(self, session, account_a):
        """A group of owners and admins is not a leak, so D17 must not fire there."""
        admin = _member(session, account_a, "admin")
        links.link_sender(session, account_a, admin.id, 700002)
        session.flush()

        answer = guard.screen_financial_answer(
            session, account_a, asker_role="owner",
            question="what did we spend?", is_group=True,
            member_telegram_ids=[700002],
        )
        assert answer is None

    def test_an_unlinked_participant_counts_as_staff(self, session, account_a):
        """D16 applied to the *audience*, not just the asker.

        Otherwise the guard is strictest for accounts that have linked everyone and weakest for
        the ones that have not — exactly backwards.
        """
        answer = guard.screen_financial_answer(
            session, account_a, asker_role="owner",
            question="what did we spend?", is_group=True,
            member_telegram_ids=[999999],       # not linked to anything
        )
        assert answer == guard.DM_OFFER

    def test_an_unknown_roster_counts_as_staff(self, session, account_a):
        """A group whose membership the bot cannot enumerate is not one to read finances into."""
        answer = guard.screen_financial_answer(
            session, account_a, asker_role="owner",
            question="what did we spend?", is_group=True, member_telegram_ids=[],
        )
        assert answer == guard.DM_OFFER

    @pytest.mark.parametrize(
        "question",
        [
            "how much did we spend?", "what's the budget?", "was that invoice paid?",
            "total costs this year?", "what's the payroll?", "is that expensive?",
        ],
    )
    def test_financial_detection_is_generous(self, question):
        """The bias is deliberate and asymmetric.

        A false positive offers a DM for a question that did not need one — mildly annoying. A
        false negative posts the household's spending into a group with the housekeeper in it.
        There is no symmetric tuning to be done.
        """
        assert guard.is_financial_question(question) is True

    def test_ordinary_questions_are_not_financial(self):
        assert guard.is_financial_question("what needs doing at Belle today?") is False


class TestBothPathsScoped:
    def test_both_paths_scoped(self, session, account_a, belle):
        """A31 — F5: *"two independent DB paths, neither using the 15 executors."*

        The Q&A path goes through `assemble_context`; the classification path through
        `build_estate_context`. Both read via the ORM, so both are covered by the property-scope
        listener **once a scope is bound** — which is what `sender_authz` guarantees. Missing
        either would leave a hole, so this asserts on both under one binding.
        """
        from mihomes.models.issue import Issue
        from mihomes.services.ai.context import assemble_context
        from mihomes.services.ai.roles import ROLES
        from mihomes.services.gateways.review_common import build_estate_context

        other = Property(
            id=uuid.uuid4(), account_id=account_a, name="ZZSECRET Estate",
            slug=f"secret-{uuid.uuid4().hex[:6]}",
        )
        session.add(other)
        session.flush()
        session.add(
            Issue(
                id=uuid.uuid4(), title="ZZSECRET issue", slug=f"i-{uuid.uuid4().hex[:8]}",
                property_id=other.id,
            )
        )
        session.flush()

        member = _member(session, account_a, "staff")
        session.add(
            MembershipPropertyScope(
                id=uuid.uuid4(), account_id=account_a,
                membership_id=member.id, property_id=belle.id,
            )
        )
        links.link_sender(session, account_a, member.id, 800001)
        session.flush()

        with links.sender_authz(session, 800001, account_a):
            qa = assemble_context(session, [ROLES["estate_manager"]], "what is open?")
            classification = build_estate_context(session, other.slug)

        assert "ZZSECRET" not in qa, "the Q&A path leaked an out-of-scope property"
        assert "ZZSECRET" not in classification, (
            "the classification path leaked an out-of-scope property — F5's second DB path"
        )
