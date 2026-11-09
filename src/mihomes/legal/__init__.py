"""The Terms of Service and Privacy Policy, and the switch that publishes them (2026-11-09).

The documents are Markdown files beside this module (`terms.md`, `privacy.md`), so every
version is in git history, which is the record of what users agreed to when.

**Publishing is the `effective_date` line at the top of each file.** Blank means unpublished:
`/legal/terms` and `/legal/privacy` answer 404, exactly as the footer links did before (SPEC-001
O1 — the founder and counsel sign off, not the code). Filling in a date (YYYY-MM-DD) and deploying
publishes that document and shows the date on it. Nothing else changes.

The renderer is deliberately tiny: headings, paragraphs, bullet and numbered lists, bold and
links. Everything is HTML-escaped before any markup is added, and links are limited to
http(s), mailto and same-site paths.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_DIR = Path(__file__).parent
DOCUMENTS = {"terms": "terms.md", "privacy": "privacy.md"}


@dataclass(frozen=True)
class LegalDocument:
    slug: str
    title: str
    effective_date: date | None
    version: str
    html: str

    @property
    def published(self) -> bool:
        return self.effective_date is not None


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.index("\n---\n", 4)
    meta = {}
    for line in text[4:end].splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, text[end + 5:]


def load(slug: str) -> LegalDocument | None:
    """The document, or None for an unknown slug. Read on each call: the files are small and a
    deploy that edits one must not need a restart to show it."""
    name = DOCUMENTS.get(slug)
    if name is None:
        return None
    meta, body = _frontmatter((_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n"))
    raw_date = meta.get("effective_date", "")
    effective = date.fromisoformat(raw_date) if raw_date else None
    return LegalDocument(slug, meta.get("title", slug.title()), effective,
                         meta.get("version", ""), render(body))


#: What an unfinished draft looks like: an ALL-CAPS [PLACEHOLDER] for counsel or the founder, or
#: the draft banner. A document still carrying any of these is never served, date or not.
_UNRESOLVED = re.compile(r"\[(?:[A-Z][A-Z0-9 ,/:'()&.\-]+[\]:]|\d+\])|DRAFT for counsel review")


def unresolved(slug: str) -> list[str]:
    """Placeholders still in the document's source — empty when it is ready to publish."""
    name = DOCUMENTS.get(slug)
    if name is None:
        return []
    return sorted(set(_UNRESOLVED.findall((_DIR / name).read_text(encoding="utf-8"))))


def published(slug: str) -> LegalDocument | None:
    """The document only if it has an effective date **and** no placeholder left in it; None
    means "answer 404". A date set on a half-finished draft must not put it in front of users."""
    doc = load(slug)
    if doc is None or not doc.published or unresolved(slug):
        return None
    return doc


# ── A small, escaping Markdown renderer ──────────────────────────────────────────────────

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_SAFE_URL = re.compile(r"^(https?://|mailto:|/)", re.IGNORECASE)


def _inline(text: str) -> str:
    out = html.escape(text, quote=True)

    def link(m: re.Match) -> str:
        label, url = m.group(1), html.unescape(m.group(2))
        if not _SAFE_URL.match(url):
            return label
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

    out = _LINK.sub(link, out)
    return _BOLD.sub(r"<strong>\1</strong>", out)


def render(markdown: str) -> str:
    blocks: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""

    def flush() -> None:
        nonlocal list_tag
        if para:
            blocks.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if items:
            blocks.append(f"<{list_tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items)
                          + f"</{list_tag}>")
            items.clear()
            list_tag = ""

    for line in markdown.split("\n"):
        stripped = line.strip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        number = re.match(r"^\d+\.\s+(.*)$", stripped)
        if not stripped:
            flush()
        elif heading:
            flush()
            level = len(heading.group(1)) + 1  # the page title is the <h1>
            slug = re.sub(r"[^a-z0-9]+", "-", heading.group(2).lower()).strip("-")
            blocks.append(f'<h{level} id="{slug}">{_inline(heading.group(2))}</h{level}>')
        elif bullet or number:
            tag = "ul" if bullet else "ol"
            if para or (items and list_tag != tag):
                flush()
            list_tag = tag
            items.append((bullet or number).group(1))
        elif items and line.startswith("  "):
            items[-1] += " " + stripped  # a wrapped list item
        else:
            if items:
                flush()
            para.append(stripped)
    flush()
    return "\n".join(blocks)
