"""The dashboard's "Getting started" checklist, and the wizard exits that feed it.

**The bug this exists for.** The signup wizard's optional steps ("Add rooms", "Invite someone")
were plain links out of it. Following one recorded nothing, nothing ever routes a member back to
`/onboarding/`, and the dashboard had no follow-up surface — so a new owner who added a room
from the wizard never saw another suggested step. Measured on evo-dev with a fresh signup.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from mihomes.models.onboarding_state import OnboardingState
from mihomes.models.user import User
from mihomes.services import onboarding_service as onboarding
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services.onboarding_service import (
    STEP_ADD_HOME,
    STEP_CHECKLIST_DISMISSED,
    STEP_DASHBOARD,
    STEP_INVITE,
    STEP_SPACES,
)
from mihomes.tenancy import account_context


@pytest.fixture
def new_account(session):
    """A brand-new account that has come through the wizard's mandatory steps."""
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"new-{uuid.uuid4().hex[:6]}@example.com",
        name="Dana Belle",
    )
    session.add(user)
    session.flush()
    account = onboarding.create_account_step(session, user, "The Belle Household")
    with account_context(account.id):
        prop = prop_svc.create_property(session, "Belle Estate")
    onboarding.complete_step(session, account.id, STEP_ADD_HOME)
    return account, prop


def _done(items) -> dict[str, bool]:
    return {item.key: item.done for item in items}


class TestChecklist:
    def test_new_account_has_home_done_and_the_rest_open(self, session, new_account):
        account, _ = new_account
        items = onboarding.checklist(session, account.id)

        assert _done(items) == {
            "home": True, "rooms": False, "task": False,
            "people": False, "document": False, "invite": False,
        }

    def test_items_tick_from_real_rows_wherever_they_were_made(self, session, new_account):
        """Adding a room on the Rooms page — not via the wizard — still ticks it off."""
        account, prop = new_account
        with account_context(account.id):
            space_svc.create_space(session, "Living Room", prop.slug)

        assert _done(onboarding.checklist(session, account.id))["rooms"] is True

    def test_account_without_an_onboarding_row_never_sees_it(self, session, account_a):
        """An estate that never came through signup (CLI bootstrap, import) is not a beginner."""
        assert session.get(OnboardingState, account_a) is None
        assert onboarding.checklist(session, account_a) is None
        # …and asking did not create the row: the dashboard read must never write.
        assert session.get(OnboardingState, account_a) is None

    def test_dismissed_checklist_stays_hidden(self, session, new_account):
        account, _ = new_account
        onboarding.dismiss_checklist(session, account.id)
        onboarding.dismiss_checklist(session, account.id)  # idempotent

        assert onboarding.checklist(session, account.id) is None
        assert onboarding.get_state(session, account.id).completed_steps.count(
            STEP_CHECKLIST_DISMISSED
        ) == 1

    def test_dismiss_marker_does_not_move_the_wizard(self, session, new_account):
        account, _ = new_account
        onboarding.dismiss_checklist(session, account.id)

        assert onboarding.current_step(session, account.id) == STEP_SPACES


class TestOptionalStepsInferredFromPresence:
    def test_a_room_completes_the_rooms_step(self, session, new_account):
        """The stranded account from the bug report: `[2,3]`, a room, no `finished_at`."""
        account, prop = new_account
        with account_context(account.id):
            space_svc.create_space(session, "Living Room", prop.slug)

        assert onboarding.current_step(session, account.id) == STEP_INVITE

    def test_absence_still_defers_to_the_record(self, session, new_account):
        """No rooms is ambiguous, so it must not be read as either answer."""
        account, _ = new_account
        assert onboarding.current_step(session, account.id) == STEP_SPACES

        onboarding.complete_step(session, account.id, STEP_SPACES)
        assert onboarding.current_step(session, account.id) == STEP_INVITE


# ── Web ───────────────────────────────────────────────────────────────────────


def _start_onboarding(account_id):
    """Seed callback: give the fixture account an onboarding row, as signup would."""
    def seed(s):
        prop_svc.create_property(s, "Test Manor")
        onboarding.complete_step(s, account_id, STEP_ADD_HOME)
    return seed


def _state(client, account_id) -> OnboardingState:
    with client._SessionLocal() as s:
        return s.get(OnboardingState, account_id)


class TestDashboardCard:
    def test_owner_mid_setup_sees_it(self, web_client_factory, account_a):
        client = web_client_factory(_start_onboarding(account_a))
        body = client.get("/").text

        assert 'id="getting-started"' in body
        assert "1 of 6 done" in body
        assert 'href="/assets"' in body

    def test_established_estate_does_not(self, web_client_factory):
        client = web_client_factory()
        assert 'id="getting-started"' not in client.get("/").text

    def test_staff_do_not(self, web_client_factory, account_a):
        client = web_client_factory(_start_onboarding(account_a))
        with client._SessionLocal() as s:
            s.execute(
                text("UPDATE memberships SET role = 'staff' WHERE account_id = :a"),
                {"a": account_a},
            )
            s.commit()

        response = client.get("/")
        # Staff still get their dashboard — the card is absent, not the page.
        assert response.status_code == 200 and "Portfolio Overview" in response.text
        assert 'id="getting-started"' not in response.text

    def test_dismiss_hides_it(self, web_client_factory, account_a):
        client = web_client_factory(_start_onboarding(account_a))
        response = client.post("/getting-started/dismiss", follow_redirects=False)

        assert response.status_code == 303 and response.headers["location"] == "/"
        assert 'id="getting-started"' not in client.get("/").text

    def test_staff_cannot_dismiss_it(self, web_client_factory, account_a):
        client = web_client_factory(
            _start_onboarding(account_a), raise_server_exceptions=False
        )
        with client._SessionLocal() as s:
            s.execute(
                text("UPDATE memberships SET role = 'staff' WHERE account_id = :a"),
                {"a": account_a},
            )
            s.commit()

        response = client.post("/getting-started/dismiss", follow_redirects=False)
        assert response.status_code == 403
        assert STEP_CHECKLIST_DISMISSED not in _state(client, account_a).completed_steps


class TestWizardExits:
    def test_add_rooms_records_the_step_and_goes_to_rooms(self, web_client_factory, account_a):
        client = web_client_factory(_start_onboarding(account_a))
        response = client.post(
            "/onboarding/leave", data={"step": STEP_SPACES}, follow_redirects=False
        )

        assert response.headers["location"] == "/assets"
        assert STEP_SPACES in _state(client, account_a).completed_steps

    def test_invite_someone_finishes_the_wizard(self, web_client_factory, account_a):
        client = web_client_factory(_start_onboarding(account_a))
        response = client.post(
            "/onboarding/leave", data={"step": STEP_INVITE}, follow_redirects=False
        )

        assert response.headers["location"] == "/settings#members"
        assert _state(client, account_a).finished_at is not None
        with client._SessionLocal() as s:
            assert onboarding.current_step(s, account_a) == STEP_DASHBOARD

    def test_unknown_step_cannot_redirect_anywhere(self, web_client_factory, account_a):
        client = web_client_factory(_start_onboarding(account_a))
        response = client.post(
            "/onboarding/leave", data={"step": STEP_ADD_HOME}, follow_redirects=False
        )

        assert response.headers["location"] == "/onboarding/"
