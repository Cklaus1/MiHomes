"""G7/G8 — the privacy routes are owner-only (A8, D8).

Step 7 says *"Owner-only route"* and **no §8 criterion covers the export route**: A8 is Step 8's,
about deletion. So F.3a would pass on a G7 that shipped only the service, which is why the export
tests below exist (harness deviation D10).

`test_owner_only` is A8 itself, at the node id §8 declares. It covers **all three** routes rather
than just the delete one: they share row 16 and a regression in the shared declaration would show
up on whichever route the test happened not to check.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def owner(web_client_as):
    return web_client_as(role="owner")


def test_an_owner_can_export(owner):
    """The positive case, which the denial tests below would otherwise not distinguish from a
    route that is broken for everyone."""
    response = owner.get("/privacy/export")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers.get("content-disposition", "")


def test_the_export_is_this_accounts_data(owner):
    """A bundle that named the wrong account, or no account, is a support incident."""
    payload = json.loads(owner.get("/privacy/export").text)

    assert payload["account_id"]
    assert payload["generated_at"]
    assert isinstance(payload["tables"], dict)
    assert payload["tables"], "an export with no tables at all is not an export"


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_only_the_owner_may_export(web_client_as, role):
    """**D8** — admin and staff are denied.

    Row 16 (`account.delete`) is `(owner=ALLOW, admin=DENY, staff=DENY)`, and the export reuses
    it rather than adding a 21st key: downloading every row an account holds is the same
    authority as ending the account. An admin who could take the whole estate with them but not
    close it is a distinction without a security difference.

    Enforced app-wide by `enforce_declared_action`, not by a check in the handler — so this
    asserts the declaration is right, which is the part a handler-level check cannot prove.
    """
    client = web_client_as(role=role)

    response = client.get("/privacy/export")

    assert response.status_code == 403, (
        f"{role} must not be able to export the account's data — got "
        f"{response.status_code}"
    )


def test_an_anonymous_request_is_refused(web_client_as):
    """No session, no export. The 401/403 distinction is the app's; either is a refusal."""
    client = web_client_as(role="owner")
    client.cookies.clear()

    response = client.get("/privacy/export")

    assert response.status_code in (401, 403), response.status_code


# --- A8: deletion is owner-only ------------------------------------------------------------

#: Every route row 16 protects, with the method that reaches it.
#:
#: Enumerated rather than tested one at a time: all three carry the same `@declares`, so a
#: regression in that declaration is a regression in all of them — and a test covering only
#: `/privacy/delete` would pass while the export leaked to an admin.
PRIVACY_ROUTES = [
    ("GET", "/privacy/export"),
    ("POST", "/privacy/delete"),
    ("POST", "/privacy/delete/cancel"),
]


@pytest.mark.parametrize("role", ["admin", "staff"])
@pytest.mark.parametrize(("method", "path"), PRIVACY_ROUTES)
def test_owner_only(web_client_as, role, method, path):
    """**A8** — deletion is owner-only; admin and staff are denied.

    Row 16 (`account.delete`) is `(owner=ALLOW, admin=DENY, staff=DENY)` and the deletion routes
    reuse it. An admin who can end the account is an admin who can end the customer's
    relationship with the product on their own authority — which is the owner's decision by
    definition, not a matter of seniority.

    Enforced app-wide by `enforce_declared_action`, so this asserts the *declaration*, which is
    the part a check inside the handler cannot prove.
    """
    client = web_client_as(role=role)

    response = client.request(method, path)

    assert response.status_code == 403, (
        f"{role} reached {method} {path} — row 16 is owner-only"
    )


@pytest.mark.parametrize(("method", "path"), PRIVACY_ROUTES)
def test_an_owner_reaches_every_privacy_route(owner, method, path):
    """The positive half. Without it, three routes broken for everyone would pass A8."""
    response = owner.request(method, path)

    assert response.status_code == 200, f"{method} {path}: {response.text}"


def test_requesting_deletion_offers_the_export_first(owner):
    """`PRICING` §4.4 — the export is offered before the account is destroyed.

    Asserted on the response rather than left to the UI: a customer who deletes without
    exporting has lost data they were entitled to take with them, and there is no second chance
    to mention it.
    """
    payload = owner.post("/privacy/delete").json()

    assert payload["state"] == "requested"
    assert payload["export_first"] == "/privacy/export"
    assert payload["purge_after"] > payload["requested_at"]


def test_the_delete_route_deletes_nothing_yet(owner):
    """`requested` starts a clock (D15). The estate must still be there afterwards.

    Compared table by table **excluding `account_deletion_requests`**, which is the one table
    the request legitimately adds a row to. Comparing the whole bundle asserts that requesting
    a deletion leaves no trace of the request, which is the opposite of what D15 wants.
    """
    before = owner.get("/privacy/export").json()["tables"]

    owner.post("/privacy/delete")

    after = owner.get("/privacy/export").json()["tables"]

    ignored = "account_deletion_requests"
    assert {k: v for k, v in after.items() if k != ignored} == {
        k: v for k, v in before.items() if k != ignored
    }
    assert len(after[ignored]) == len(before[ignored]) + 1


def test_cancel_is_idempotent(owner):
    """A9 at the route: cancelling twice reports "nothing to cancel", never an error."""
    owner.post("/privacy/delete")

    first = owner.post("/privacy/delete/cancel").json()
    second = owner.post("/privacy/delete/cancel").json()

    assert first["cancelled"] is True
    assert second["cancelled"] is False
    assert second["state"] == "nothing_to_cancel"


# ── G14 / A34 — the audit export, at the surface a user reaches ────────────────
#
# `test_estate_gates.py::test_denied_names_target` asserts the *source* carries the upgrade
# target, which cannot see a route that 500s on the way to saying so. These two drive it.
# Measured before they were written: Estate → 200, Pro → 402 with `upgrade_target: estate`.


def test_an_estate_owner_can_export_the_audit_log(owner, _pg_engine):
    """The positive case. Without it, the denial test below cannot tell a working paywall from
    a route that is broken for everybody."""
    from sqlalchemy import text

    with _pg_engine.begin() as conn:
        conn.execute(text("UPDATE accounts SET plan = 'estate' WHERE slug LIKE 'acct-a%'"))

    response = owner.get("/privacy/audit-export")

    assert response.status_code == 200, response.text
    assert "attachment" in response.headers.get("content-disposition", "")


def test_a_pro_owner_is_denied_and_told_what_to_buy(owner, _pg_engine):
    """**A34** at the route: the denial names the plan that would allow it.

    402 rather than 403 on purpose — 403 is what the *role* gate returns, and a customer who
    cannot tell "your plan lacks this" from "your role lacks this" cannot tell which of the two
    they are able to fix.
    """
    from sqlalchemy import text

    with _pg_engine.begin() as conn:
        conn.execute(text("UPDATE accounts SET plan = 'pro' WHERE slug LIKE 'acct-a%'"))

    response = owner.get("/privacy/audit-export")

    assert response.status_code == 402, response.text

    body = response.json()
    assert body["error"] == "plan_required"
    assert body["upgrade_target"] == "estate", (
        "A34: the denial must name the plan that would allow it, not merely refuse"
    )
