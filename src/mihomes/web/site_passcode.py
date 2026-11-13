"""Pre-launch site passcode gate.

While the product is still being built, the public domain should not be open to everyone. Set
``MIHOMES_SITE_PASSCODE`` and every page — including the landing page, login and signup — asks
for that passcode first. Leave it unset (evo-dev, laptop, tests) and this middleware does nothing.

It is a *curtain*, not authentication: one shared code, no per-person identity. Accounts and
their sign-in still sit behind it unchanged.

Design notes
  - The page is served by the middleware itself, not a router, so it needs no SPEC-003 action
    declaration and cannot be reached by ``enforce_declared_action`` before the gate.
  - The cookie is an HMAC of the passcode keyed with ``MIHOMES_SECRET_KEY``: the passcode
    itself never sits in a browser, and changing the passcode locks everyone out again.
  - Machine callers (Stripe/Telegram webhooks, RFC 8058 one-click unsubscribe) are exempt —
    they carry no cookie and authenticate by signature, exactly as for the Host guard.
  - Read per request, so changing the env var and restarting is the whole on/off switch.
"""

import asyncio
import hashlib
import hmac
import html
import os
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from mihomes.auth.session_flow import safe_next

PASSCODE_ENV = "MIHOMES_SITE_PASSCODE"
GATE_PATH = "/site-passcode"
COOKIE_NAME = "mihomes_site_pass"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
_WRONG_CODE_DELAY = 1.0  # seconds; slows guessing without blocking other requests

# Package assets (CSS, icons) are public anyway and the gate page may use them.
_STATIC_PREFIX = "/static/"
# Browsers fetch the web-app manifest *without* cookies, so gating it would break PWA install.
# Both are public metadata/JS that reveal no estate data.
_PUBLIC_PATHS = frozenset({"/manifest.webmanifest", "/sw.js"})


def configured_passcode() -> str:
    return os.environ.get(PASSCODE_ENV, "").strip()


def _token(passcode: str) -> str:
    key = (os.environ.get("MIHOMES_SECRET_KEY", "") + "\x00" + passcode).encode()
    return hmac.new(key, b"mihomes-site-passcode-v1", hashlib.sha256).hexdigest()


def _exempt(path: str) -> bool:
    from mihomes.web.routes.unsubscribe import UNSUBSCRIBE_PATH_PREFIX
    from mihomes.web.routes.webhooks import WEBHOOK_PATH_PREFIX

    return path in _PUBLIC_PATHS or path.startswith(
        (WEBHOOK_PATH_PREFIX, UNSUBSCRIBE_PATH_PREFIX, _STATIC_PREFIX)
    )


def _dest(raw: str) -> str:
    """Where to go after unlocking. `safe_next` refuses bare /login and /signup to stop sign-in
    loops; that risk does not exist here, and those are the pages a visitor most likely wanted."""
    if raw in ("/login", "/signup"):
        return raw
    return safe_next(raw) or "/"


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() == "https"


def _page(next_path: str, error: bool = False, status_code: int = 200) -> HTMLResponse:
    err = (
        '<p class="err" role="alert">That passcode isn\'t right. Please try again.</p>'
        if error
        else ""
    )
    body = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>MiHomes — Coming soon</title>
<style>
  :root {{ color-scheme: light dark; --bg:#f6f5f2; --card:#fff; --ink:#1c1c1a; --muted:#6b6b66;
          --line:#e2e0da; --accent:#2f5d50; --err:#b3261e; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#141413; --card:#1e1e1c; --ink:#f0efe9;
          --muted:#a3a29b; --line:#33332f; --accent:#7fb8a6; --err:#f2b8b5; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:16px;
         background:var(--bg); color:var(--ink);
         font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
  main {{ width:100%; max-width:380px; background:var(--card); border:1px solid var(--line);
         border-radius:14px; padding:32px 28px; }}
  h1 {{ margin:0 0 4px; font-size:1.5rem; letter-spacing:-.01em; }}
  p {{ margin:0 0 20px; color:var(--muted); }}
  label {{ display:block; font-size:.875rem; font-weight:600; margin-bottom:6px; }}
  input {{ width:100%; padding:10px 12px; font:inherit; color:inherit; background:transparent;
          border:1px solid var(--line); border-radius:8px; }}
  input:focus {{ outline:2px solid var(--accent); outline-offset:1px; border-color:transparent; }}
  button {{ width:100%; margin-top:14px; padding:10px 12px; font:inherit; font-weight:600;
           color:#fff; background:var(--accent); border:0; border-radius:8px; cursor:pointer; }}
  @media (prefers-color-scheme: dark) {{ button {{ color:#10201b; }} }}
  .err {{ color:var(--err); margin:12px 0 0; font-size:.875rem; }}
</style></head>
<body><main>
  <h1>MiHomes</h1>
  <p>We're still building. Enter the passcode to continue.</p>
  <form method="post" action="{GATE_PATH}">
    <input type="hidden" name="next" value="{html.escape(next_path, quote=True)}">
    <label for="passcode">Passcode</label>
    <input id="passcode" name="passcode" type="password" autocomplete="current-password"
           required autofocus>
    <button type="submit">Continue</button>
    {err}
  </form>
</main></body></html>"""
    resp = HTMLResponse(body, status_code=status_code)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


class SitePasscodeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        passcode = configured_passcode()
        if not passcode:
            return await call_next(request)

        path = request.url.path
        if _exempt(path):
            return await call_next(request)

        expected = _token(passcode)
        has_pass = hmac.compare_digest(request.cookies.get(COOKIE_NAME, ""), expected)

        if path == GATE_PATH:
            if request.method == "POST":
                form = await request.form()
                dest = _dest(str(form.get("next", "")))
                given = str(form.get("passcode", "")).strip()
                if not hmac.compare_digest(given.encode(), passcode.encode()):
                    await asyncio.sleep(_WRONG_CODE_DELAY)
                    return _page(dest, error=True, status_code=401)
                resp = RedirectResponse(dest, status_code=303)
                resp.set_cookie(
                    COOKIE_NAME,
                    expected,
                    max_age=COOKIE_MAX_AGE,
                    httponly=True,
                    secure=_is_https(request),
                    samesite="lax",
                    path="/",
                )
                return resp
            dest = _dest(request.query_params.get("next", ""))
            if has_pass:
                return RedirectResponse(dest, status_code=303)
            return _page(dest)

        if has_pass:
            return await call_next(request)

        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"{GATE_PATH}?next={quote(target, safe='')}", status_code=303)
