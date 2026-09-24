"""The Free home cap as the user meets it — `PRICING` §4.1's upgrade choice, not a silent trial.

**The bug.** A Free account adding a 2nd home was let through: the gate silently started the
14-day Pro trial and retried. Nothing on screen said so, so the 1-home cap looked broken. §4.1
asks for a modal instead — *"Adding another home is a Pro feature. Start your 14-day Pro
trial."* — and a trial that starts only when that button is pressed.

Driven over real HTTP as the non-superuser app role (`rls_app`), because this is a request-path
feature and RLS-blind superuser fixtures have hidden request-path bugs here before.
"""

from __future__ import annotations

from sqlalchemy import text

from tests.integration.test_rls_request_path import _HTML, _sign_in, rls_app  # noqa: F401


def _set_plan(account_id, *, plan="free", trial_used=False, paying=False):
    import mihomes.db as db_module

    with db_module._engine.begin() as conn:
        conn.execute(
            text(
                "update accounts set plan = :p, subscription_status = :s,"
                " trial_ends_at = null,"
                " trial_used_at = case when :used then now() else null end,"
                " stripe_subscription_id = case when :paying then 'sub_test' else null end"
                " where id = :a"
            ),
            {"p": plan, "s": "active" if paying else None, "used": trial_used,
             "paying": paying, "a": account_id},
        )


def _account_state(account_id):
    import mihomes.db as db_module

    with db_module._engine.begin() as conn:
        conn.execute(
            text("select set_config('app.current_account', :a, true)"), {"a": str(account_id)}
        )
        homes = conn.execute(
            text("select count(*) from properties where account_id = :a"), {"a": account_id}
        ).scalar_one()
        plan, used = conn.execute(
            text("select plan, trial_used_at from accounts where id = :a"), {"a": account_id}
        ).one()
    return homes, plan, used


def test_at_the_cap_the_add_button_opens_the_choice(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id)

    page = client.get("/properties/", headers=_HTML)

    assert page.status_code == 200
    assert 'data-testid="upgrade-modal"' in page.text
    assert "Adding another home is a Pro feature" in page.text
    assert 'data-testid="start-trial"' in page.text
    assert 'data-testid="see-plans"' in page.text
    assert "Stay on Free" in page.text


def test_below_the_cap_there_is_no_popup(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id, plan="pro")

    page = client.get("/properties/", headers=_HTML)
    assert 'data-testid="upgrade-modal"' not in page.text

    form = client.get("/properties/new", headers=_HTML)
    assert 'action="/properties/"' in form.text
    assert "upgrade-choice" not in form.text


def test_the_new_home_page_shows_the_choice_instead_of_a_form(rls_app):  # noqa: F811
    """The no-JS / bookmarked path: the same choice, and no form that could only be refused."""
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id)

    page = client.get("/properties/new", headers=_HTML)

    assert 'data-testid="upgrade-choice"' in page.text
    assert 'action="/properties/"' not in page.text


def test_a_used_trial_is_not_offered_again(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id, trial_used=True)

    page = client.get("/properties/", headers=_HTML)

    assert 'data-testid="upgrade-modal"' in page.text
    assert 'data-testid="start-trial"' not in page.text
    assert 'data-testid="see-plans"' in page.text


def test_a_refused_add_does_not_start_the_trial(rls_app):  # noqa: F811
    """**The regression.** Posting past the cap used to spend the trial and add the home."""
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id)

    response = client.post("/properties/", data={"name": "Second Home"}, headers=_HTML)

    assert response.status_code == 402
    assert 'data-testid="start-trial"' in response.text, "the paywall should offer the trial"
    homes, plan, used = _account_state(account_id)
    assert (homes, plan, used) == (1, "free", None), (
        f"a refused add changed the account: homes={homes} plan={plan} trial_used_at={used}"
    )


def test_start_trial_then_add_the_home(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id)

    started = client.post(
        "/billing/trial",
        data={"action": "property.add", "next": "/properties/new"},
        headers=_HTML,
        follow_redirects=False,
    )
    assert started.status_code == 303
    assert started.headers["location"] == "/properties/new"

    homes, plan, used = _account_state(account_id)
    assert plan == "pro" and used is not None

    # With the trial running, the form is back and the add goes through.
    assert 'action="/properties/"' in client.get("/properties/new", headers=_HTML).text
    added = client.post(
        "/properties/", data={"name": "Second Home"}, headers=_HTML, follow_redirects=False
    )
    assert added.status_code == 303
    assert _account_state(account_id)[0] == 2

    banner = client.get("/trial-status", headers=_HTML)
    assert 'data-testid="trial-banner"' in banner.text
    assert "14 days left" in banner.text


def test_the_trial_starts_only_once(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id, trial_used=True)

    again = client.post("/billing/trial", data={"next": "/properties/new"},
                        headers=_HTML, follow_redirects=False)

    assert again.status_code == 303
    assert again.headers["location"] == "/billing"
    assert _account_state(account_id)[1] == "free"


def test_a_paying_customer_is_never_given_a_trial(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id, plan="pro", paying=True)

    response = client.post("/billing/trial", data={}, headers=_HTML, follow_redirects=False)

    assert response.headers["location"] == "/billing"
    homes, plan, used = _account_state(account_id)
    assert used is None


def test_the_trial_redirect_is_not_an_open_redirect(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id)

    response = client.post("/billing/trial", data={"next": "//evil.example/x"},
                           headers=_HTML, follow_redirects=False)

    assert response.headers["location"] == "/"


def test_no_banner_when_not_on_a_trial(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id)

    banner = client.get("/trial-status", headers=_HTML)

    assert banner.status_code == 200
    assert "trial-banner" not in banner.text


def test_the_billing_page_matches_the_plan(rls_app):  # noqa: F811
    """Over HTTP: a Pro account sees Pro as current, is offered Estate, and no Pro upgrade."""
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id, plan="pro")

    page = client.get("/billing", headers=_HTML)

    assert page.status_code == 200
    assert "Upgrade to Pro" not in page.text
    assert "Upgrade to Estate" in page.text
    assert 'data-testid="plan-free"' in page.text
    assert 'name="plan" value="free"' not in page.text
    assert "Unlimited homes (fair use)" in page.text


def test_downgrade_to_free_over_http(rls_app):  # noqa: F811
    """Confirm page first, then the drop — for a Pro account not paying through Stripe."""
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id, plan="pro")

    billing = client.get("/billing", headers=_HTML)
    assert 'data-testid="downgrade-free"' in billing.text

    confirm = client.get("/billing/cancel", headers=_HTML)
    assert confirm.status_code == 200
    assert "Nothing is deleted" in confirm.text
    assert _account_state(account_id)[1] == "pro", "the confirm page must not change anything"

    done = client.post("/billing/cancel", headers=_HTML)
    assert done.status_code == 200
    assert "You are now on the Free plan" in done.text
    assert _account_state(account_id)[:2] == (1, "free")


def test_estate_downgrade_to_pro_over_http(rls_app):  # noqa: F811
    client, account_id = rls_app
    _sign_in(client)
    _set_plan(account_id, plan="estate")

    billing = client.get("/billing", headers=_HTML)
    assert 'data-testid="downgrade-pro"' in billing.text

    confirm = client.get("/billing/cancel?to=pro", headers=_HTML)
    assert "Move from Estate to Pro?" in confirm.text
    assert "Predictive maintenance" in confirm.text
    assert _account_state(account_id)[1] == "estate", "the confirm page must not change anything"

    done = client.post("/billing/cancel", data={"to": "pro"}, headers=_HTML)
    assert "You are now on the Pro plan" in done.text
    assert _account_state(account_id)[1] == "pro"


def test_pages_carry_the_lazy_banner_slot(rls_app):  # noqa: F811
    """The banner loads as its own request; the page itself does no plan lookup for it."""
    client, _ = rls_app
    _sign_in(client)

    page = client.get("/", headers=_HTML)

    assert 'hx-get="/trial-status"' in page.text
