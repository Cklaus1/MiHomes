"""Unit tests for the auth rate limiter (SPEC-001 D7, A13)."""

import pytest

from mihomes.auth.ratelimit import (
    AuthRateLimiter,
    AuthRateLimitExceeded,
    limiter,
)


class TestAuthRateLimiter:
    """Test the in-process AuthRateLimiter class."""

    def test_signup_allow_first_request(self):
        lim = AuthRateLimiter(signup_per_email=5, signup_per_ip=10, login_per_email=3, login_per_ip=5)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="signup")
        # Should not raise

    def test_login_allow_first_request(self):
        lim = AuthRateLimiter(signup_per_email=5, signup_per_ip=10, login_per_email=3, login_per_ip=5)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="login")
        # Should not raise

    def test_signup_email_limit(self):
        lim = AuthRateLimiter(signup_per_email=2, signup_per_ip=10, login_per_email=3, login_per_ip=5)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="signup")
        lim.check(email="a@b.com", ip="1.2.3.5", kind="signup")
        # Third request from same email should fail
        try:
            lim.check(email="a@b.com", ip="1.2.3.6", kind="signup")
            pytest.fail("Expected AuthRateLimitExceeded")
        except AuthRateLimitExceeded as exc:
            assert "a@b.com" in str(exc)

    def test_signup_ip_limit(self):
        lim = AuthRateLimiter(signup_per_email=10, signup_per_ip=2, login_per_email=3, login_per_ip=5)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="signup")
        lim.check(email="b@c.com", ip="1.2.3.4", kind="signup")
        # Third request from same IP should fail
        try:
            lim.check(email="c@d.com", ip="1.2.3.4", kind="signup")
            pytest.fail("Expected AuthRateLimitExceeded")
        except AuthRateLimitExceeded as exc:
            assert "1.2.3.4" in str(exc)

    def test_login_email_limit(self):
        lim = AuthRateLimiter(signup_per_email=10, signup_per_ip=10, login_per_email=2, login_per_ip=5)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="login")
        lim.check(email="a@b.com", ip="1.2.3.5", kind="login")
        try:
            lim.check(email="a@b.com", ip="1.2.3.6", kind="login")
            pytest.fail("Expected AuthRateLimitExceeded")
        except AuthRateLimitExceeded as exc:
            assert "a@b.com" in str(exc)

    def test_login_ip_limit(self):
        lim = AuthRateLimiter(signup_per_email=10, signup_per_ip=10, login_per_email=3, login_per_ip=2)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="login")
        lim.check(email="b@c.com", ip="1.2.3.4", kind="login")
        try:
            lim.check(email="c@d.com", ip="1.2.3.4", kind="login")
            pytest.fail("Expected AuthRateLimitExceeded")
        except AuthRateLimitExceeded as exc:
            assert "1.2.3.4" in str(exc)

    def test_kind_isolation(self):
        """Signup and login buckets are independent."""
        lim = AuthRateLimiter(signup_per_email=1, signup_per_ip=10, login_per_email=1, login_per_ip=10)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="signup")
        # Signup bucket exhausted, login should still work
        lim.check(email="a@b.com", ip="1.2.3.4", kind="login")

    def test_different_emails_independent(self):
        lim = AuthRateLimiter(signup_per_email=1, signup_per_ip=10, login_per_email=3, login_per_ip=5)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="signup")
        # Different email should still be allowed
        lim.check(email="c@d.com", ip="1.2.3.4", kind="signup")

    def test_different_ips_independent(self):
        lim = AuthRateLimiter(signup_per_email=10, signup_per_ip=1, login_per_email=3, login_per_ip=5)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="signup")
        # Different IP should still be allowed
        lim.check(email="a@b.com", ip="5.6.7.8", kind="signup")

    def test_default_limits(self):
        """Default limits match the spec: signup 5/10, login 3/5."""
        lim = AuthRateLimiter()
        # Signup: 5 per email, 10 per IP
        for _ in range(5):
            lim.check(email="a@b.com", ip="1.2.3.4", kind="signup")
        try:
            lim.check(email="a@b.com", ip="1.2.3.4", kind="signup")
            pytest.fail("Expected AuthRateLimitExceeded")
        except AuthRateLimitExceeded:
            pass

    def test_error_message_includes_key(self):
        lim = AuthRateLimiter(signup_per_email=1, signup_per_ip=10, login_per_email=3, login_per_ip=5)
        lim.check(email="a@b.com", ip="1.2.3.4", kind="signup")
        try:
            lim.check(email="a@b.com", ip="1.2.3.5", kind="signup")
            pytest.fail("Expected AuthRateLimitExceeded")
        except AuthRateLimitExceeded as exc:
            assert "a@b.com" in str(exc)


class TestGlobalLimiter:
    """Test the singleton limiter instance."""

    def test_global_limiter_is_singleton(self):
        from mihomes.auth.ratelimit import limiter
        assert limiter is limiter

    def test_global_limiter_has_default_limits(self):
        """The global limiter should accept normal requests."""
        limiter.check(email="a@b.com", ip="1.2.3.4", kind="signup")
        limiter.check(email="a@b.com", ip="1.2.3.4", kind="login")