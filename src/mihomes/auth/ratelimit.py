"""Auth rate limiter (SPEC-001 D7, A13).

Signup: 5 per email, 10 per IP per 5 min.
Login: 3 per email, 5 per IP per 5 min.

Buckets are keyed separately for email and IP:
- Email buckets: (kind, email) — tracks across all IPs
- IP buckets: (kind, "", ip) — tracks across all emails

"""

import time


class AuthRateLimitExceeded(Exception):
    """Raised when a rate-limited auth request is rejected."""

    def __init__(self, kind: str, identifier: str, limit: int, window_seconds: int) -> None:
        self.kind = kind
        self.identifier = identifier
        self.limit = limit
        self.window_seconds = window_seconds
        super().__init__(
            f"{kind.capitalize()} rate limit exceeded for {identifier!r} "
            f"(limit: {limit} per {window_seconds}s)"
        )


class _Bucket:
    """Sliding window counter for a single bucket."""

    __slots__ = ("_timestamps",)

    def __init__(self) -> None:
        self._timestamps: list[float] = []

    def add(self) -> None:
        self._timestamps.append(time.monotonic())

    def prune(self, cutoff: float) -> int:
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        return len(self._timestamps)


class AuthRateLimiter:
    """Token-bucket rate limiter for auth endpoints.

    Args:
        signup_per_email: Max signup requests per email per window.
        signup_per_ip: Max signup requests per IP per window.
        login_per_email: Max login requests per email per window.
        login_per_ip: Max login requests per IP per window.
        window_seconds: Sliding window size in seconds (default 300 = 5 min).
    """

    def __init__(
        self,
        signup_per_email: int = 5,
        signup_per_ip: int = 10,
        login_per_email: int = 3,
        login_per_ip: int = 5,
        window_seconds: int = 300,
    ) -> None:
        self.signup_per_email = signup_per_email
        self.signup_per_ip = signup_per_ip
        self.login_per_email = login_per_email
        self.login_per_ip = login_per_ip
        self.window_seconds = window_seconds
        # Email buckets: (kind, email)
        self._email_buckets: dict[tuple[str, str], _Bucket] = {}
        # IP buckets: (kind, "", ip)
        self._ip_buckets: dict[tuple[str, str, str], _Bucket] = {}

    def _email_bucket(self, kind: str, email: str) -> _Bucket:
        key = (kind, email.lower())
        if key not in self._email_buckets:
            self._email_buckets[key] = _Bucket()
        return self._email_buckets[key]

    def _ip_bucket(self, kind: str, ip: str) -> _Bucket:
        key = (kind, "", ip)
        if key not in self._ip_buckets:
            self._ip_buckets[key] = _Bucket()
        return self._ip_buckets[key]

    def _email_limit(self, kind: str) -> int:
        if kind == "signup":
            return self.signup_per_email
        return self.login_per_email

    def _ip_limit(self, kind: str) -> int:
        if kind == "signup":
            return self.signup_per_ip
        return self.login_per_ip

    def check(self, kind: str, email: str, ip: str) -> None:
        """Check both email-based and IP-based rate limits.

        Raises AuthRateLimitExceeded if either limit is exceeded.
        """
        # Email check
        limit = self._email_limit(kind)
        bucket = self._email_bucket(kind, email)
        cutoff = time.monotonic() - self.window_seconds
        count = bucket.prune(cutoff)
        if count >= limit:
            raise AuthRateLimitExceeded(kind, email, limit, self.window_seconds)
        bucket.add()

        # IP check
        limit = self._ip_limit(kind)
        bucket = self._ip_bucket(kind, ip)
        cutoff = time.monotonic() - self.window_seconds
        count = bucket.prune(cutoff)
        if count >= limit:
            # Don't count rejected requests in any bucket
            raise AuthRateLimitExceeded(kind, ip, limit, self.window_seconds)
        bucket.add()

    def _count(self, kind: str, email: str) -> int:
        """Return the number of requests in the current window for an email bucket.

        Used by tests to verify rate-limit counters without reaching the limit.
        """
        bucket = self._email_bucket(kind, email)
        cutoff = time.monotonic() - self.window_seconds
        return bucket.prune(cutoff)


# Module-level singleton for use by routes
limiter = AuthRateLimiter()