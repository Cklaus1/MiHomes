"""Rendered landing page — the nine GTM sections and their constraints (A16, §6 Step 6).

Step 6's verification: "page renders, contains no dollar amounts (D14), and the
chat-intake card does not mention WhatsApp (D15)."

These are content guards, and that is the point. D14 and D15 are not style
preferences — publishing a price that `PRICING_AND_PACKAGING.md` still marks
PLACEHOLDER, or advertising a WhatsApp integration whose pairing is broken, are
both promises the product cannot keep.
"""

import re

import pytest
from fastapi.testclient import TestClient

from mihomes.landing import create_landing_app


@pytest.fixture
def html():
    client = TestClient(create_landing_app(), raise_server_exceptions=False)
    response = client.get("/")
    assert response.status_code == 200
    return response.text


def test_page_renders(html):
    assert "<html" in html.lower()
    assert "MiHomes" in html


def test_no_prices_rendered(html):
    """A16 — no dollar amounts anywhere (D14, N6).

    Every figure in PRICING_AND_PACKAGING.md is PLACEHOLDER, so any number on this
    page is a commitment nobody has made. GTM:157.
    """
    # $12, $12.50, $1,200, 12 USD, USD 12 — and the bare-word forms.
    money = [
        r"\$\s*\d",
        r"\d+\s*(?:USD|EUR|GBP)\b",
        r"\b(?:USD|EUR|GBP)\s*\d",
        r"\d+\s*(?:dollars|euros|pounds)\b",
        r"\bper month\b.*?\d",
        r"\d+\s*/\s*(?:mo|month)\b",
    ]
    for pattern in money:
        found = re.findall(pattern, html, flags=re.IGNORECASE)
        assert not found, f"price-like text on the landing page: {found!r} (D14/N6)"


def test_plan_shapes_are_present_without_numbers(html):
    """The pricing teaser shows plan SHAPES only (GTM §2.6)."""
    for plan in ("Free", "Pro", "Estate"):
        assert plan in html, f"plan {plan!r} missing from the pricing teaser"


def test_chat_card_does_not_mention_whatsapp(html):
    """D15 — Telegram only, or omit the card.

    Baileys WhatsApp pairing is broken and Twilio is post-GA (SAAS_PRD §6.2).
    Advertising either would be vaporware.
    """
    assert "whatsapp" not in html.lower(), "D15: the chat-intake card must not name WhatsApp"
    assert "twilio" not in html.lower(), "Twilio is post-GA — do not advertise it"
    assert "Telegram" in html, "the chat card should name the channel that actually works"


def test_no_fabricated_social_proof(html):
    """GTM §2.2: 'Never fabricate testimonials.'

    Pre-launch there are no customers, so a quote or a logo wall on this page would
    be invented. The credibility line is allowed; fake numbers are not.
    """
    lowered = html.lower()
    for tell in ("trusted by", "★★★★★", "5 stars", "customers love"):
        assert tell not in lowered, f"fabricated social proof: {tell!r}"


def test_all_nine_sections_present(html):
    """GTM §2.1–2.9. Each section is checked by a phrase it uniquely owns."""
    lowered = html.lower()
    expectations = {
        "2.1 hero headline": "every home, under control",
        "2.3 the problem": "falls through the cracks",
        "2.4 how it works": "add your homes",
        "2.5 features": "estate manager",
        "2.6 pricing teaser": "estate",
        "2.7 faq": "faq",
        "2.8 closing cta": "waitlist",
        "2.9 footer": "privacy",
    }
    for label, phrase in expectations.items():
        assert phrase in lowered, f"missing {label} (looked for {phrase!r})"


def test_signup_form_posts_to_waitlist(html):
    """The form is the conversion path — it must target the real endpoint."""
    assert 'action="/waitlist"' in html
    assert 'method="post"' in html.lower()
    assert 'name="email"' in html


def test_qualification_fields_are_optional(html):
    """GTM §2.8/§3: email required, the two qualification fields optional.

    A required "how many homes?" would cost signups for data that never gates
    anything.
    """
    assert 'name="num_homes"' in html
    assert 'name="has_staff"' in html
    # Only the email input carries `required`.
    assert html.count("required") == 1, "only the email field may be required"


def test_no_js_framework_or_external_assets(html):
    """N5 — GTM:55 requires <1.5s LCP on 4G: no heavy JS, no CDN, no web fonts."""
    lowered = html.lower()
    assert "<script" not in lowered, "N5: no JS on the landing page"
    for host in ("cdn.", "googleapis.com", "gstatic.com", "unpkg.com", "jsdelivr"):
        assert host not in lowered, f"N5: external asset host {host!r}"
    assert "@font-face" not in lowered, "N5: no web fonts"


def test_utm_params_are_captured_into_the_form():
    """GTM §3 segmentation: utm_* arrive as query params and must survive the POST."""
    client = TestClient(create_landing_app(), raise_server_exceptions=False)
    response = client.get("/?utm_campaign=launch&utm_source=x&utm_medium=social")
    assert response.status_code == 200

    for field, value in (
        ("utm_campaign", "launch"),
        ("utm_source", "x"),
        ("utm_medium", "social"),
    ):
        assert f'name="{field}"' in response.text
        assert f'value="{value}"' in response.text


def test_utm_values_are_escaped():
    """utm_* come straight off the query string and land in HTML attributes."""
    client = TestClient(create_landing_app(), raise_server_exceptions=False)
    response = client.get('/?utm_source=%22%3E%3Cscript%3Ealert(1)%3C/script%3E')

    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text or "&#34;" in response.text


def test_footer_links_to_terms_and_privacy(html):
    """GTM §2.9 — required before collecting emails.

    They 404 until O1 lands, which is expected and tracked in the pre-launch
    checklist. The *links* must exist; the pages are a founder decision.
    """
    assert "/legal/terms" in html
    assert "/legal/privacy" in html
