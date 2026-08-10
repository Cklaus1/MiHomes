"""Template rendering — one key in, (subject, html, text) out.

Server-side Jinja, templates in-repo (D6). A vendor-hosted template would not
survive failover to Postmark/SES, which breaks the abstraction requirement
(BILLING §2.5).

Rendering lives here rather than in a provider so it happens **once, identically,
regardless of vendor** — see provider.py.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

__all__ = ["TemplateNotFoundError", "render_template"]

TEMPLATE_DIR = Path(__file__).parent / "templates"

SUBJECT_BLOCK = "subject"


class TemplateNotFoundError(Exception):
    """A template key has no .html, or no required .txt sibling."""


@lru_cache(maxsize=1)
def _get_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        # A name is user-supplied free text on a public form; rendering it raw
        # would make the confirmation email a stored-XSS vector.
        autoescape=select_autoescape(default_for_string=True, default=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(template: str, data: dict) -> tuple[str, str, str]:
    """Render a template key to (subject, html, text).

    Subject is the first line of the .html template's `{% block subject %}`.
    A .txt sibling is **required** — never ship HTML-only mail.
    """
    env = _get_env()

    try:
        html_template = env.get_template(f"{template}.html")
    except TemplateNotFound as exc:
        raise TemplateNotFoundError(
            f"No HTML template for {template!r} in {TEMPLATE_DIR}"
        ) from exc

    try:
        text_template = env.get_template(f"{template}.txt")
    except TemplateNotFound as exc:
        # Not a warning, an error. HTML-only mail is a deliverability and
        # accessibility problem, and the rule is easiest to break on the day
        # someone adds a template and forgets the sibling.
        raise TemplateNotFoundError(
            f"Template {template!r} has no .txt sibling — never ship HTML-only "
            f"mail. Add {template}.txt beside {template}.html."
        ) from exc

    # Render the subject block on its own. Note `.blocks`, not `make_module()`:
    # blocks are not module attributes, and for a child template that overrides a
    # block declared in base.html the override is what `.blocks` resolves to.
    subject_block = html_template.blocks.get(SUBJECT_BLOCK)
    if subject_block is None:
        raise TemplateNotFoundError(
            f"Template {template!r} defines no {{% block {SUBJECT_BLOCK} %}} — "
            "the subject line comes from there."
        )
    context = html_template.new_context(data)
    rendered_subject = "".join(subject_block(context)).strip()
    if not rendered_subject:
        raise TemplateNotFoundError(
            f"Template {template!r} has an empty {{% block {SUBJECT_BLOCK} %}}."
        )
    # First line only: a wrapped block would otherwise inject a newline into a
    # header, which some MTAs treat as header injection.
    subject = rendered_subject.splitlines()[0].strip()

    html = html_template.render(**data)
    text = text_template.render(**data)
    return subject, html, text
