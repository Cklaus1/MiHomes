"""G12.4 — CSRF via double-submit (§6 Step 12).

**Why double-submit rather than a server-stored token.** Sessions here are server-side, so a
synchroniser token could be stored on the session row — but that adds a write to every form render
and a read to every submit for a property the cookie already provides. Double-submit compares a
cookie against a form field: an attacker's page can *cause* a request carrying the victim's cookies
but cannot **read** them (same-origin policy), so it cannot populate the matching field.

**This is a second layer, not the only one.** `HostAndOriginGuardMiddleware` already rejects
state-changing requests whose `Origin`/`Sec-Fetch-Site` is cross-site, and that is the stronger
check because it does not depend on the attacker being unable to write a cookie. Double-submit is
here because the Origin header is absent on some legitimate old clients and because a subdomain that
can set cookies on the parent domain would defeat a cookie-only scheme — the two failure modes are
different, so both checks earn their place.

The CSRF cookie is deliberately **not** `httpOnly`: the page's own JavaScript has to read it to
populate the field. That is safe — it is a random value with no authority of its own, unlike the
session cookie, which is `httpOnly` precisely because nothing in the page should ever read it.
"""

from __future__ import annotations

import hmac
import secrets

__all__ = ["CSRF_COOKIE", "CSRF_FIELD", "issue_csrf_token", "tokens_match"]

CSRF_COOKIE = "mihomes_csrf"
CSRF_FIELD = "csrf_token"


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(cookie_value: str | None, submitted: str | None) -> bool:
    """Constant-time comparison of the cookie and the submitted field.

    `hmac.compare_digest`, not `==`: a short-circuiting comparison leaks how many leading characters
    matched, which over enough requests is enough to reconstruct a token. The cost of doing it
    properly is nil, so there is no reason to reason about whether the leak is exploitable here.

    Empty or missing values never match — otherwise a request with no cookie and no field would
    compare equal and pass.
    """
    if not cookie_value or not submitted:
        return False
    return hmac.compare_digest(str(cookie_value), str(submitted))
