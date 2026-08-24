"""Request-origin and Host guard middleware (spec H30).

MiHomes is a local-first, single-user web app bound to loopback. Two browser-side
threats still apply:

  - **CSRF** — a malicious page the user has open can issue state-changing
    requests to ``http://localhost:<port>/...``. We reject any unsafe-method
    request whose ``Origin`` (or ``Sec-Fetch-Site``) marks it cross-site.
  - **DNS rebinding** — an attacker-controlled hostname resolving to 127.0.0.1
    lets a remote page talk to the local app. We reject any request whose
    ``Host`` is not a recognized loopback host.

Safe methods (GET/HEAD/OPTIONS) are exempt from the Origin check. Requests with
no Origin *and* no cross-site Sec-Fetch-Site signal (e.g. curl, same-origin form
posts from older browsers) are allowed — this is a usability/security balance
appropriate for a localhost tool, matching the "won't-fix bridge is accepted"
threat posture while still blocking the browser-driven cross-site case.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Host values (sans port) that denote the local machine.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"})


def _host_only(host_header: str) -> str:
    """Strip the port from a Host header value, preserving bracketed IPv6."""
    if not host_header:
        return ""
    host = host_header.strip()
    if host.startswith("["):  # [::1]:8000 → [::1]
        return host.split("]", 1)[0] + "]" if "]" in host else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def _origin_host(origin: str) -> str:
    """Extract host[:port] → host from an Origin/Referer URL."""
    # origin looks like scheme://host[:port]
    rest = origin.split("://", 1)[-1]
    rest = rest.split("/", 1)[0]
    return _host_only(rest)


class HostAndOriginGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 0. Provider webhooks are exempt from **both** guards, and this is the one exemption
        #    in the app (SPEC-004 Step 4).
        #
        #    Both guards assume the caller is a *browser the user is driving*. Stripe is neither:
        #    it POSTs from its own infrastructure to whatever public hostname the endpoint is
        #    registered under, so the Host guard would 400 every live webhook, and it sends no
        #    Origin, so only the Host half actually bites — but exempting one and not the other
        #    would leave a rule whose reason no longer matches its behaviour.
        #
        #    **This does not weaken CSRF or DNS-rebinding defence**, because neither threat model
        #    applies to a route with no session: CSRF is the browser attaching *the user's*
        #    cookies to a forged request, and this route reads no cookie and trusts no caller
        #    identity. Its authentication is a signature over the raw body (N3) — strictly
        #    stronger than an Origin header, which is advisory and unauthenticated.
        #
        #    Scoped to the prefix rather than the exact path so a second provider's endpoint
        #    inherits it; the constant lives beside the route so a rename cannot silently re-arm
        #    the guards and take production webhooks down.
        from mihomes.web.routes.webhooks import WEBHOOK_PATH_PREFIX

        if request.url.path.startswith(WEBHOOK_PATH_PREFIX):
            return await call_next(request)

        # 1. Host guard (DNS-rebinding): reject non-loopback hosts outright.
        host = _host_only(request.headers.get("host", ""))
        if host and host.lower() not in _LOCAL_HOSTS:
            return PlainTextResponse("Invalid Host header.", status_code=400)

        # 2. Origin guard (CSRF): only for state-changing methods.
        if request.method not in SAFE_METHODS:
            sec_fetch_site = request.headers.get("sec-fetch-site", "").lower()
            if sec_fetch_site in {"cross-site", "same-site"}:
                return PlainTextResponse("Cross-site request blocked.", status_code=403)

            origin = request.headers.get("origin", "")
            if origin:
                if _origin_host(origin).lower() not in _LOCAL_HOSTS:
                    return PlainTextResponse(
                        "Cross-origin request blocked.", status_code=403
                    )

        return await call_next(request)
