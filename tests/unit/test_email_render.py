"""Template rendering — subject/html/text extraction (SPEC-001 A8, §5.2).

`render_template` returns (subject, html, text). The subject comes from the
.html template's `{% block subject %}`, and a .txt sibling is REQUIRED — never
ship HTML-only mail (D6: templates are server-side Jinja, in-repo, so they
survive failover to Postmark/SES).
"""

import pytest

from mihomes.services.email.render import TemplateNotFoundError, render_template

CONFIRM_DATA = {
    "confirm_url": "https://mihomes.ai/waitlist/confirm?token=abc123",
    "name": "Alex",
    "position": 42,
}


def test_waitlist_confirmation_has_both_parts():
    """A8 — rendering yields a subject plus non-empty HTML *and* text parts."""
    subject, html, text = render_template("waitlist_confirmation", CONFIRM_DATA)

    assert subject and subject.strip() == subject, "subject must be stripped, non-empty"
    assert "\n" not in subject, "subject must be a single line"
    assert html.strip(), "html part must not be empty"
    assert text.strip(), "text part must not be empty"


def test_both_parts_carry_the_confirm_url():
    """D7 double opt-in: the link is the whole point of the email.

    A text part that silently lost the URL would leave plain-text readers unable
    to confirm — and the funnel counts confirmed signups (GTM:293).
    """
    _, html, text = render_template("waitlist_confirmation", CONFIRM_DATA)
    assert CONFIRM_DATA["confirm_url"] in html
    assert CONFIRM_DATA["confirm_url"] in text


def test_text_part_is_not_html():
    """The .txt sibling must be real plain text, not a stripped copy of the HTML."""
    _, _, text = render_template("waitlist_confirmation", CONFIRM_DATA)
    assert "<p" not in text.lower()
    assert "<html" not in text.lower()
    assert "<a " not in text.lower()


def test_subject_is_not_rendered_into_the_body():
    """The subject block is metadata; leaking it into the body reads as a bug."""
    subject, html, _ = render_template("waitlist_confirmation", CONFIRM_DATA)
    assert f"<title>{subject}</title>" not in html or html.count(subject) <= 1


def test_missing_template_raises():
    """An unknown key must fail loudly at the call site, not send a blank email."""
    with pytest.raises(TemplateNotFoundError):
        render_template("no_such_template", {})


def test_html_only_template_is_rejected(tmp_path, monkeypatch):
    """A .html with no .txt sibling must raise — never ship HTML-only mail (§5.2).

    Guards the discipline rather than the current templates: this fails the day
    someone adds a template and forgets the text part, which is exactly when the
    rule stops being obvious.
    """
    from mihomes.services.email import render as render_mod

    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "orphan.html").write_text(
        "{% block subject %}Hi{% endblock %}<p>body</p>", encoding="utf-8"
    )

    # _get_env is lru_cached, so repointing TEMPLATE_DIR alone would be ignored
    # (stale env) AND would leak the temp loader into every later test in the
    # module. Clear before and after, inside a try/finally so an assertion failure
    # cannot leave the cache poisoned for the rest of the suite.
    monkeypatch.setattr(render_mod, "TEMPLATE_DIR", templates)
    render_mod._get_env.cache_clear()
    try:
        with pytest.raises(TemplateNotFoundError, match="no .txt sibling"):
            render_template("orphan", {})
    finally:
        monkeypatch.undo()
        render_mod._get_env.cache_clear()


def test_optional_position_is_tolerated():
    """O4 default is 'compute it, do not display it' — so absence must render fine.

    The founder may later decide to show queue position (§1.3 O4); until then the
    template must not break when it is omitted.
    """
    data = {k: v for k, v in CONFIRM_DATA.items() if k != "position"}
    subject, html, text = render_template("waitlist_confirmation", data)
    assert subject and html.strip() and text.strip()


def test_autoescape_is_on():
    """A name is user-supplied and lands in HTML — it must be escaped.

    Phase 0 collects a free-text name on a public form. Rendering it raw would be
    a stored-XSS vector in the confirmation email.
    """
    _, html, _ = render_template(
        "waitlist_confirmation",
        {**CONFIRM_DATA, "name": "<script>alert(1)</script>"},
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ── The footer reason (2026-11-16) ───────────────────────────────────────────────────────────

_WAITLIST_REASON = "asked to join the MiHomes waitlist"


def test_only_the_waitlist_email_says_you_joined_the_waitlist():
    """The shared footer used to tell every recipient "you asked to join the MiHomes waitlist",
    including someone resetting a password or accepting a staff invite. Every template except
    the waitlist confirmation must now render without that sentence, in both parts."""
    from pathlib import Path

    from jinja2 import ChainableUndefined

    from mihomes.services.email import render as render_module

    templates = sorted(
        p.stem for p in (Path(render_module.__file__).parent / "templates").glob("*.html")
        if p.stem != "base" and not p.stem.startswith("_")  # `_button.html` is a macro
    )
    assert "password_reset" in templates and "staff_invite" in templates

    env = render_module._get_env()
    original = env.undefined
    env.undefined = ChainableUndefined  # render with missing context, we only read the footer
    try:
        for name in templates:
            _, html, text = render_template(name, {})
            if name == "waitlist_confirmation":
                assert _WAITLIST_REASON in html and _WAITLIST_REASON in text
            else:
                assert _WAITLIST_REASON not in html, f"{name}.html still claims a waitlist signup"
                assert _WAITLIST_REASON not in text, f"{name}.txt still claims a waitlist signup"
                assert "mihomes.ai" in html, f"{name}.html lost the neutral footer"
    finally:
        env.undefined = original


# ── Buttons that survive Outlook (2026-11-17) ─────────────────────────────────────────────────

def test_every_button_is_a_table_cell_outlook_can_draw():
    """Desktop Outlook drew the old styled-link buttons as plain text ("Confirm my email" with no
    button). Every email's button must now be a `<td bgcolor>` holding the real link, and no
    template may fall back to the old styled link."""
    import re
    from pathlib import Path

    from jinja2 import ChainableUndefined

    from mihomes.services.email import render as render_module

    folder = Path(render_module.__file__).parent / "templates"
    old_style = "display:inline-block;background"
    for path in folder.glob("*.html"):
        if path.name != "_button.html":
            assert old_style not in path.read_text(encoding="utf-8"), f"{path.name} has an old button"

    env = render_module._get_env()
    original = env.undefined
    env.undefined = ChainableUndefined
    try:
        url = "https://mihomes.ai/verify-email/TOKEN?next=%2Finvite%2Fabc&x=1"
        _, html, _ = render_template("email_verification", {"verify_url": url, "expires_in": "24 hours"})
    finally:
        env.undefined = original
    cell = re.search(r'<td[^>]*bgcolor="#1c1c1a"[^>]*>\s*<a href="([^"]+)"[^>]*>([^<]+)</a>', html)
    assert cell, "the button is not a table cell"
    assert cell.group(1) == url.replace("&", "&amp;"), cell.group(1)
    assert cell.group(2).strip() == "Confirm my email"
    assert "mso-padding-alt" in html
