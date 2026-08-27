"""G7 · §6 Step 7 — the export route is owner-only (D8).

Step 7 says *"Owner-only route"*, and **no §8 criterion covers it**: A8 is Step 8's, about
deletion. So F.3a would pass on a G7 that shipped only the service. This module is the gate that
makes the step's own words checkable, recorded as harness deviation D10.

Step 8 adds `test_owner_only` here for A8; this file exists first because the export route does.
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
