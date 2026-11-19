"""Pre-launch site passcode gate (`mihomes.web.site_passcode`).

Only the front door (homepage, login, signup, Google sign-in start) is gated; everything else is
left to account sign-in.
"""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mihomes.auth.sessions import SESSION_COOKIE
from mihomes.web import site_passcode
from mihomes.web.site_passcode import COOKIE_NAME, GATE_PATH, SitePasscodeMiddleware


def _ok(request):
    return PlainTextResponse(f"ok {request.url.path}")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(site_passcode, "_WRONG_CODE_DELAY", 0)
    monkeypatch.setenv("MIHOMES_SECRET_KEY", "test-key")
    app = Starlette(
        routes=[
            Route("/", _ok),
            Route("/login", _ok, methods=["GET", "POST"]),
            Route("/signup", _ok, methods=["GET", "POST"]),
            Route("/auth/google/start", _ok),
            Route("/auth/google/callback", _ok),
            Route("/tasks", _ok),
            Route("/invite/{token}", _ok),
            Route("/password/reset/{token}", _ok),
            Route("/webhooks/stripe", _ok, methods=["POST"]),
            Route("/manifest.webmanifest", _ok),
            Route("/loginfoo", _ok),
        ]
    )
    app.add_middleware(SitePasscodeMiddleware)
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")


def test_unset_passcode_is_a_no_op(client, monkeypatch):
    monkeypatch.delenv("MIHOMES_SITE_PASSCODE", raising=False)
    assert client.get("/").text == "ok /"
    assert client.get("/signup").status_code == 200


def test_blank_passcode_is_a_no_op(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "   ")
    assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    "path", ["/", "/login", "/signup", "/auth/google/start", "/login?next=/tasks"]
)
def test_front_door_is_gated(client, locked, path):
    resp = client.get(path)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith(f"{GATE_PATH}?next=")


def test_signup_post_is_gated(client, locked):
    assert client.post("/signup", data={"email": "x@y.z"}).status_code == 303


@pytest.mark.parametrize(
    "path",
    [
        "/tasks",
        "/invite/abc",
        "/password/reset/abc",
        "/auth/google/callback",
        "/manifest.webmanifest",
        "/loginfoo",
    ],
)
def test_everything_else_is_left_to_sign_in(client, locked, path):
    assert client.get(path).status_code == 200


def test_webhooks_pass(client, locked):
    assert client.post("/webhooks/stripe").status_code == 200


def test_signed_in_visitor_skips_gate_on_homepage(client, locked):
    client.cookies.set(SESSION_COOKIE, "some-session")
    assert client.get("/").text == "ok /"
    # ...but a session cookie is not a pass for signup.
    assert client.get("/signup").status_code == 303


def test_gate_page_renders_and_is_not_indexed(client, locked):
    resp = client.get(GATE_PATH, params={"next": "/login"})
    assert resp.status_code == 200
    assert 'name="passcode"' in resp.text
    assert 'value="/login"' in resp.text
    assert resp.headers["x-robots-tag"].startswith("noindex")
    assert resp.headers["cache-control"] == "no-store"


def test_wrong_passcode_is_refused(client, locked):
    resp = client.post(GATE_PATH, data={"passcode": "nope", "next": "/login"})
    assert resp.status_code == 401
    assert "isn't right" in resp.text
    assert COOKIE_NAME not in resp.cookies
    assert client.get("/login").status_code == 303


def test_right_passcode_unlocks_login(client, locked):
    resp = client.post(GATE_PATH, data={"passcode": " opensesame ", "next": "/login"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "opensesame" not in cookie  # the cookie is an HMAC, never the passcode
    assert client.get("/login").text == "ok /login"
    assert client.get("/").text == "ok /"
    # Visiting the gate again once unlocked just forwards on.
    assert client.get(GATE_PATH, params={"next": "/signup"}).headers["location"] == "/signup"


def test_secure_cookie_behind_https_proxy(client, locked):
    resp = client.post(
        GATE_PATH, data={"passcode": "opensesame"}, headers={"x-forwarded-proto": "https"}
    )
    assert "secure" in resp.headers["set-cookie"].lower()


def test_changing_the_passcode_relocks_everyone(client, monkeypatch, locked):
    client.post(GATE_PATH, data={"passcode": "opensesame"})
    assert client.get("/login").status_code == 200
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "newcode")
    assert client.get("/login").status_code == 303


def test_forged_cookie_is_refused(client, locked):
    client.cookies.set(COOKIE_NAME, "opensesame")
    assert client.get("/login").status_code == 303


@pytest.mark.parametrize("bad", ["https://evil.example/", "//evil.example/", "/\\evil"])
def test_next_cannot_redirect_off_site(client, locked, bad):
    resp = client.post(GATE_PATH, data={"passcode": "opensesame", "next": bad})
    assert resp.headers["location"] == "/"
