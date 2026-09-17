"""A signed-out visitor to `/` gets the marketing page, not a login form.

Before this, `/` was the dashboard and nothing else: an anonymous browser hit
`enforce_declared_action`, got a 401, and the handler in `web/app.py` sent it to `/login`. A
visitor who had never heard of MiHomes was asked to sign in to an account they had no reason
to know they could create.

The fix lives in the 401 handler rather than in the route table, and these tests pin the two
halves of that choice: `/` is public, and **nothing else moved**.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mihomes.web.app import create_app

HTML = {"accept": "text/html"}


@pytest.fixture()
def client() -> TestClient:
    # `HostAndOriginGuardMiddleware` 400s anything whose Host is not loopback (DNS-rebinding
    # defence), and TestClient's default `testserver` is not. The base_url is what makes these
    # requests look like the browser on the user's machine that they are meant to represent.
    return TestClient(create_app(), base_url="http://127.0.0.1", follow_redirects=False)


def test_anonymous_root_renders_the_marketing_page(client: TestClient) -> None:
    response = client.get("/", headers=HTML)

    assert response.status_code == 200, "a signed-out visitor must not be bounced to /login"
    assert "Every home, under control" in response.text


def test_the_marketing_page_offers_signup_not_a_waitlist(client: TestClient) -> None:
    """Phase 0's CTA was a waitlist because there was nothing to sign up to.

    The Free tier now exists, so the page must point at it. A waitlist in front of a working
    free tier is friction that converts nothing.
    """
    body = client.get("/", headers=HTML).text

    assert "/signup" in body
    assert 'action="/waitlist"' not in body, (
        "the waitlist form posts to a route this app does not serve — it would 404 on submit"
    )


def test_the_marketing_page_does_not_link_a_missing_hero_image(client: TestClient) -> None:
    """`hero.svg` lives in the landing app's static dir, not this app's."""
    assert "/static/hero.svg" not in client.get("/", headers=HTML).text


def test_a_protected_route_still_redirects_to_login(client: TestClient) -> None:
    """The exemption is `/` alone. Everything else keeps its 401 → /login behaviour.

    `/tasks/` carries the trailing slash the router mounts it under; `/tasks` would answer 307
    from FastAPI's own redirect before authorisation is ever consulted, which would pass this
    assertion without testing anything.
    """
    response = client.get("/tasks/", headers=HTML)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_an_api_client_still_gets_401_at_root(client: TestClient) -> None:
    """Content negotiation is preserved: only a *browser* gets HTML.

    A client checking for 401 must not silently receive a 200 and parse marketing copy as data.
    """
    assert client.get("/", headers={"accept": "application/json"}).status_code == 401


def test_an_htmx_request_still_gets_401_at_root(client: TestClient) -> None:
    """An HTMX fragment request must not get a full marketing page swapped into its target."""
    response = client.get("/", headers={**HTML, "hx-request": "true"})

    assert response.status_code == 401
