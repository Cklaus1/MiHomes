"""Integration tests for rate-limited auth routes (SPEC-001 D7, A13)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from mihomes.web.app import create_app
from mihomes.models import Base
from mihomes.auth.sessions import SESSION_COOKIE

# Use the same in-memory DB pattern as test_auth.py
ENGINE = create_engine("sqlite:///:memory:")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=ENGINE)


def _setup_db():
    """Create tables and insert the password_hash column."""
    Base.metadata.create_all(bind=ENGINE)
    with ENGINE.connect() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN password_hash TEXT"))
        conn.commit()


@pytest.fixture(autouse=True)
def _reset():
    """Recreate DB between tests so rate limit buckets reset."""
    Base.metadata.drop_all(bind=ENGINE)
    _setup_db()
    yield


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def _signup(client, email="a@b.com", password="password123", name="Test"):
    """Helper: POST /signup."""
    return client.post(
        "/signup",
        data={"email": email, "password": password, "name": name},
        follow_redirects=False,
    )


def _login(client, email="a@b.com", password="password123"):
    """Helper: POST /login."""
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


class TestSignupRateLimit:
    """D7: signup is throttled per-email (5) and per-IP (10)."""

    def test_signup_succeeds_under_email_limit(self, client):
        for i in range(5):
            resp = _signup(client, email=f"user{i}@b.com")
            assert resp.status_code == 303, f"Signup failed on request {i+1}"

    def test_signup_blocked_after_email_limit(self, client):
        for i in range(5):
            resp = _signup(client, email=f"a{i}@b.com")
            assert resp.status_code == 303, f"Signup failed on request {i+1}"
        # 6th request from same email should be rate-limited
        resp = _signup(client, email="a@b.com")
        assert resp.status_code == 429
        assert "rate limit" in resp.text.lower() or "too many" in resp.text.lower()

    def test_signup_blocked_after_ip_limit(self, client):
        """10 requests from same IP should be blocked."""
        for i in range(10):
            resp = _signup(client, email=f"ipuser{i}@b.com")
            assert resp.status_code == 303, f"Signup failed on request {i+1}"
        # 11th from same IP
        resp = _signup(client, email="ipuser10@b.com")
        assert resp.status_code == 429


class TestLoginRateLimit:
    """D7: login is throttled per-email (3) and per-IP (5)."""

    def _create_user(self, email="a@b.com", password="password123"):
        from mihomes.auth.password_identity import create_password_user
        db = SessionLocal()
        try:
            create_password_user(db, email=email, password=password, name="Test")
            db.commit()
        finally:
            db.close()

    def test_login_succeeds_under_limit(self, client):
        self._create_user()
        resp = _login(client)
        assert resp.status_code == 303

    def test_login_blocked_after_email_limit(self, client):
        self._create_user()
        for i in range(3):
            resp = _login(client, email="a@b.com", password=f"wrong{i}")
            assert resp.status_code == 401, f"Login failed on request {i+1}"
        # 4th request should be rate-limited
        resp = _login(client, email="a@b.com", password="wrong")
        assert resp.status_code == 429

    def test_login_blocked_after_ip_limit(self, client):
        for i in range(5):
            self._create_user(email=f"ipuser{i}@b.com", password="password123")
            resp = _login(client, email=f"ipuser{i}@b.com", password="wrongpass")
            assert resp.status_code == 401, f"Login failed on request {i+1}"
        # 6th from same IP
        resp = _login(client, email="ipuser5@b.com", password="wrongpass")
        assert resp.status_code == 429


class TestRateLimitErrorPresentation:
    """A13: rate limit errors are shown to the user on the form."""

    def test_signup_rate_limit_shows_error_on_form(self, client):
        for i in range(5):
            _signup(client, email=f"err{i}@b.com")
        resp = client.post(
            "/signup",
            data={"email": "err@b.com", "password": "password123", "name": "Test"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "rate limit" in resp.text.lower() or "too many" in resp.text.lower()

    def test_login_rate_limit_shows_error_on_form(self, client):
        for i in range(3):
            _login(client, email=f"err{i}@b.com", password="wrong")
        resp = client.post(
            "/login",
            data={"email": "err@b.com", "password": "wrong"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "rate limit" in resp.text.lower() or "too many" in resp.text.lower()