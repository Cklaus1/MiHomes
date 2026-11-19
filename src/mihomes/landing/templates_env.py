"""Jinja environment for the landing page.

Separate from the email renderer: that one enforces an HTML+text pair, this one
renders a single page. Autoescape is on for the same reason — utm_* values come
straight from the query string.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=1)
def get_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(default_for_string=True, default=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_page(template: str, data: dict) -> str:
    return get_env().get_template(template).render(**data)
