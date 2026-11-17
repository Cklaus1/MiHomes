"""Search-engine files at the site root: `/robots.txt`, `/sitemap.xml`, and Google's
site-verification file (2026-11-17).

**Undeclared on purpose** (permanent allowlist in `test_route_declarations.py`): crawlers carry
no cookies, and all three must sit at the root — that is where crawlers and Google's verifier
look. Every response is either fixed text or built from public facts (the site address, which
legal documents are published, an operator-set verification filename). No tenant data.

**robots.txt is an allowlist, not a blocklist.** It admits the marketing page, the legal pages,
the sign-in/sign-up doors and `/static/` (so Google can render the page), and disallows the rest.
A blocklist would need a new line for every product route ever added; this way a new route is
out of search results by default.

**The verification file answers only for filenames the operator configured.** Google's file
method is "serve `googleXXXX.html` containing `google-site-verification: googleXXXX.html`" —
the content is derivable from the name. So answering for *any* `google*.html` would let anyone
verify ownership of this domain in their own Search Console. `MIHOMES_GOOGLE_SITE_VERIFICATION`
(comma-separated filenames) is the allowlist; unset, the route is a 404.
"""

from __future__ import annotations

import os
import re
from xml.sax.saxutils import escape

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from mihomes import legal

router = APIRouter()

DEFAULT_BASE_URL = "https://mihomes.ai"
VERIFICATION_ENV = "MIHOMES_GOOGLE_SITE_VERIFICATION"

#: Public paths a crawler may visit. Everything else is behind sign-in.
_ALLOWED_PATHS = ("/$", "/legal/", "/login", "/signup", "/static/", "/google")

_LEGAL_SLUGS = ("terms", "privacy")

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def base_url() -> str:
    """The public site address. `MIHOMES_BASE_URL` on the Droplet is https://mihomes.ai."""
    return os.environ.get("MIHOMES_BASE_URL", "").strip().rstrip("/") or DEFAULT_BASE_URL


def verification_tokens() -> set[str]:
    """Configured tokens, each the part between `google` and `.html`. Accepts the filename with
    or without `.html`, so pasting Google's filename as shown works either way."""
    tokens = set()
    for raw in os.environ.get(VERIFICATION_ENV, "").split(","):
        name = raw.strip().removesuffix(".html")
        if name.startswith("google"):
            name = name[len("google"):]
        if _TOKEN.match(name):
            tokens.add(name)
    return tokens


@router.get("/robots.txt", include_in_schema=False)
def robots() -> PlainTextResponse:
    lines = ["User-agent: *"]
    lines += [f"Allow: {path}" for path in _ALLOWED_PATHS]
    lines += ["Disallow: /", "", f"Sitemap: {base_url()}/sitemap.xml", ""]
    return PlainTextResponse("\n".join(lines),
                             headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> Response:
    base = base_url()
    urls = [f"{base}/"]
    # Terms and Privacy 404 until published — listing an unpublished one would feed Google a 404.
    urls += [f"{base}/legal/{slug}" for slug in _LEGAL_SLUGS if legal.published(slug)]
    body = "".join(f"  <url><loc>{escape(u)}</loc></url>\n" for u in urls)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           f"{body}</urlset>\n")
    return Response(xml, media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/google{token}.html", include_in_schema=False)
def google_site_verification(token: str) -> PlainTextResponse:
    if token not in verification_tokens():
        return PlainTextResponse("Not found", status_code=404)
    return PlainTextResponse(f"google-site-verification: google{token}.html",
                             media_type="text/html")
