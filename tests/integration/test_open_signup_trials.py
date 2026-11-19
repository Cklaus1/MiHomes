"""Open sign-up with Pro and Estate trials (2026-11-19).

The landing page's plan cards send people to `/signup?trial=<plan>`. A new user has no account
until onboarding creates one, so the choice rides in a cookie and the create-account step starts
the trial. Someone already signed in is sent to the billing page's one-click trial instead.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from mihomes.models.account import Account
from mihomes.services.billing.trial import TRIAL_INTENT_COOKIE, start_trial, trial_plan
from tests.integration import test_password_e2e as _e2e
from tests.integration.test_rls_request_path import _HTML, _sign_in, rls_app  # noqa: F401
from tests.integration.test_upgrade_prompt import _account_state, _set_plan
from tests.integration.verify_helpers import confirm_email

# The e2e suite's fixtures, re-used rather than copied (assigned so pytest registers them).
client = _e2e.client
mail = _e2e.mail
_clean = _e2e._clean

EMAIL = "trial-journey@example.com"


# ---------------------------------------------------------------- the service


def test_only_pro_and_estate_are_trialable():
    assert trial_plan("estate") == "estate"
    assert trial_plan(" PRO ") == "pro"
    assert trial_plan("free") is None
    assert trial_plan("enterprise") is None
    assert trial_plan(None) is None


class _Acct:
    plan = "free"
    subscription_status = None
    trial_ends_at = None
    trial_used_at = None
    id = "a"


class _Session:
    def commit(self):
        pass


def test_an_estate_trial_grants_estate():
    account = _Acct()
    assert start_trial(_Session(), account, plan="estate") is True
    assert account.plan == "estate"
    assert account.subscription_status == "trialing"
    assert (account.trial_ends_at - account.trial_used_at).days == 14


def test_a_made_up_plan_starts_nothing():
    account = _Acct()
    assert start_trial(_Session(), account, plan="enterprise") is False
    assert account.plan == "free" and account.trial_used_at is None


def test_still_one_trial_per_account():
    account = _Acct()
    assert start_trial(_Session(), account, plan="pro") is True
    assert start_trial(_Session(), account, plan="estate") is False
    assert account.plan == "pro"


# ---------------------------------------------------------------- sign-up → onboarding


def _new_account(client):
    s = client._Session()
    try:
        return s.execute(select(Account).where(Account.name == "Trial Estate")).scalar_one()
    finally:
        s.close()


def _sign_up_and_create_account(client, mail, path):
    page = client.get(path)
    assert page.status_code == 200, page.text
    r = client.post(
        "/signup",
        data={"email": EMAIL, "password": "a-long-passphrase", "name": "Trial Person"},
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text
    assert confirm_email(client, mail, EMAIL).headers["location"] == "/onboarding/"
    made = client.post(
        "/onboarding/account",
        data={"name": "Trial Estate", "account_type": "household"},
        follow_redirects=False,
    )
    assert made.status_code == 303, made.text
    return page, made


def test_signing_up_from_the_estate_button_starts_an_estate_trial(client, mail):
    page, made = _sign_up_and_create_account(client, mail, "/signup?trial=estate")

    assert "14-day Estate trial" in page.text, "the form should say which trial is coming"
    account = _new_account(client)
    assert account.plan == "estate"
    assert account.subscription_status == "trialing"
    assert account.trial_used_at is not None
    assert TRIAL_INTENT_COOKIE in made.headers.get("set-cookie", ""), "the choice is cleared"


def test_plain_sign_up_stays_on_free(client, mail):
    _sign_up_and_create_account(client, mail, "/signup")

    account = _new_account(client)
    assert account.plan == "free"
    assert account.trial_used_at is None


@pytest.mark.parametrize("bogus", ["free", "enterprise", "<script>"])
def test_a_made_up_trial_is_ignored(client, bogus):
    page = client.get(f"/signup?trial={bogus}")
    assert page.status_code == 200
    assert TRIAL_INTENT_COOKIE not in page.headers.get("set-cookie", "")


# ---------------------------------------------------------------- signed in: billing page


def test_signed_in_trial_link_goes_to_the_billing_offer(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id)

    r = client.get("/signup?trial=estate", headers=_HTML, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/billing?trial=estate"

    page = client.get("/billing?trial=estate", headers=_HTML)
    assert 'data-testid="trial-offer"' in page.text
    assert "Try Estate free for 14 days" in page.text
    assert _account_state(account_id)[1] == "free", "a GET must never start a trial"


def test_the_billing_page_offers_both_trials(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id)

    page = client.get("/billing", headers=_HTML)
    assert 'data-testid="start-trial"' in page.text
    assert 'data-testid="start-trial-estate"' in page.text


def test_starting_the_estate_trial_from_billing(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id)

    r = client.post("/billing/trial", data={"plan": "estate", "next": "/"},
                    headers=_HTML, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    homes, plan, used = _account_state(account_id)
    assert plan == "estate" and used is not None

    banner = client.get("/trial-status", headers=_HTML)
    assert "free Estate trial" in banner.text


def test_a_used_trial_is_explained_not_offered(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id, trial_used=True)

    page = client.get("/billing?trial=pro", headers=_HTML)
    assert 'data-testid="trial-offer"' not in page.text
    assert 'data-testid="trial-offer-used"' in page.text
    assert 'data-testid="start-trial-estate"' not in page.text


# ---------------------------------------------------------------- the landing page


def test_open_landing_offers_free_and_both_trials(client, monkeypatch):
    from mihomes.auth import signup_gate

    monkeypatch.delenv(signup_gate.SIGNUP_MODE_ENV, raising=False)
    html = client.get("/", headers={"Accept": "text/html"}).text

    assert "Try for free" in html
    assert 'href="/signup?trial=pro"' in html
    assert 'href="/signup?trial=estate"' in html
    assert "waitlist" not in html.lower()
    assert "opening in stages" not in html


def test_waitlist_landing_has_no_trial_buttons(client, monkeypatch):
    from mihomes.auth import signup_gate

    monkeypatch.setenv(signup_gate.SIGNUP_MODE_ENV, "waitlist")
    html = client.get("/", headers={"Accept": "text/html"}).text

    assert "?trial=" not in html
    assert "Join the waitlist" in html
