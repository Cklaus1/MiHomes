"""Pre-launch site passcode gate (`mihomes.web.site_passcode`)."""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

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
            Route("/login", _ok),
            Route("/tasks", _ok),
            Route("/webhooks/stripe", _ok, methods=["POST"]),
            Route("/unsubscribe", _ok, methods=["GET", "POST"]),
            Route("/static/app.css", _ok),
        ]
    )
    app.add_middleware(SitePasscodeMiddleware)
    return TestClient(app, follow_redirects=False)


def test_unset_passcode_is_a_no_op(client, monkeypatch):
    monkeypatch.delenv("MIHOMES_SITE_PASSCODE", raising=False)
    assert client.get("/tasks").text == "ok /tasks"


def test_blank_passcode_is_a_no_op(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "   ")
    assert client.get("/tasks").status_code == 200


def test_locked_request_redirects_to_gate_with_next(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    resp = client.get("/tasks?x=1")
    assert resp.status_code == 303
    assert resp.headers["location"] == f"{GATE_PATH}?next=%2Ftasks%3Fx%3D1"


def test_gate_page_renders_and_is_not_indexed(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    resp = client.get(GATE_PATH, params={"next": "/tasks"})
    assert resp.status_code == 200
    assert 'name="passcode"' in resp.text
    assert 'value="/tasks"' in resp.text
    assert resp.headers["x-robots-tag"].startswith("noindex")
    assert resp.headers["cache-control"] == "no-store"


def test_wrong_passcode_is_refused(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    resp = client.post(GATE_PATH, data={"passcode": "nope", "next": "/tasks"})
    assert resp.status_code == 401
    assert "isn't right" in resp.text
    assert COOKIE_NAME not in resp.cookies
    assert client.get("/tasks").status_code == 303


def test_right_passcode_unlocks_and_returns_to_next(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    resp = client.post(GATE_PATH, data={"passcode": " opensesame ", "next": "/tasks"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/tasks"
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "opensesame" not in cookie  # the cookie is an HMAC, never the passcode
    assert client.get("/tasks").text == "ok /tasks"
    # Visiting the gate again once unlocked just forwards on.
    assert client.get(GATE_PATH, params={"next": "/login"}).headers["location"] == "/login"


def test_secure_cookie_behind_https_proxy(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    resp = client.post(
        GATE_PATH,
        data={"passcode": "opensesame"},
        headers={"x-forwarded-proto": "https"},
    )
    assert "secure" in resp.headers["set-cookie"].lower()


def test_changing_the_passcode_relocks_everyone(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    client.post(GATE_PATH, data={"passcode": "opensesame"})
    assert client.get("/tasks").status_code == 200
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "newcode")
    assert client.get("/tasks").status_code == 303


def test_forged_cookie_is_refused(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    client.cookies.set(COOKIE_NAME, "opensesame")
    assert client.get("/tasks").status_code == 303


@pytest.mark.parametrize("bad", ["https://evil.example/", "//evil.example/", "/\\evil"])
def test_next_cannot_redirect_off_site(client, monkeypatch, bad):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    resp = client.post(GATE_PATH, data={"passcode": "opensesame", "next": bad})
    assert resp.headers["location"] == "/"


def test_login_is_an_allowed_destination(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    resp = client.post(GATE_PATH, data={"passcode": "opensesame", "next": "/login"})
    assert resp.headers["location"] == "/login"


def test_machine_callers_and_static_bypass_the_gate(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_SITE_PASSCODE", "opensesame")
    assert client.post("/webhooks/stripe").status_code == 200
    assert client.post("/unsubscribe").status_code == 200
    assert client.get("/static/app.css").status_code == 200
