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
