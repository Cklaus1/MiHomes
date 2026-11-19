"""Playbook service — each account's own operational playbooks, with shared tick boxes.

**Playbooks are tenant rows (2026-10-21).** They used to be markdown files in
`knowledge/playbooks/`, read by every account — one estate's hiring, offboarding and emergency
procedures were visible to every signup, and nobody could edit them in the app. Now:

- `Playbook` rows belong to one account (RLS, like every tenant table).
- Writing one needs `playbook.manage` **and** `require_editor()`: owners and admins always,
  staff only when their membership has `can_edit_playbooks`. Every write in this module calls it,
  so the per-member rule cannot be bypassed by a route that forgets.
- New accounts start empty and copy from the generic template gallery
  (`mihomes/playbook_templates/*.md`) — package data, identical for everyone, never tenant data.

A playbook is a **document, not a task template** (user decision 2026-10-22): it never creates
tasks. Its markdown — metadata as `**Key**: value` lines under the title, `- [ ] step` lines —
renders with a tick box per step that anyone who can open the playbook can tick; the ticks are
shared by the whole account (`PlaybookCheck`), and an editor can Reset them all.
"""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mihomes.models.membership import Membership
from mihomes.models.playbook import Playbook, PlaybookCheck
from mihomes.models.user import User
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import ensure_unique_slug, generate_slug

# Knowledge base location — project root / knowledge, or override via env. Playbooks no longer
# live here; the rest of the knowledge base (job descriptions, notes) is still read by the CLI's
# search and the resume ranker.
# __file__ is src/mihomes/services/playbook.py → go up 4 levels to project root
_DEFAULT_KB = Path(__file__).resolve().parents[3] / "knowledge"

#: The starter gallery, shipped with the app. Generic on purpose — no account's own content.
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "playbook_templates"

MAX_NAME = 200
MAX_CONTENT = 100_000


class PlaybookPermissionError(PermissionError):
    """The signed-in member may view and run playbooks but not write them."""


def kb_path() -> Path:
    """Return the knowledge base directory."""
    import os
    override = os.environ.get("MIHOMES_KB")
    return Path(override) if override else _DEFAULT_KB


# ── Who may write ─────────────────────────────────────────────────────────────

def can_edit(membership: Membership | None) -> bool:
    """Owners and admins always; staff only with the per-member grant."""
    if membership is None:
        return False
    if membership.role in ("owner", "admin"):
        return True
    return membership.role == "staff" and bool(membership.can_edit_playbooks)


def require_editor(membership: Membership | None) -> None:
    if not can_edit(membership):
        raise PlaybookPermissionError(
            "You can view and run playbooks, but not edit them. "
            "Ask an owner or admin to turn on “Can edit playbooks” for you."
        )


# ── Reading ───────────────────────────────────────────────────────────────────

def list_playbooks(session: Session) -> list[Playbook]:
    return session.query(Playbook).order_by(Playbook.name).all()


def get_playbook(session: Session, slug: str) -> Optional[Playbook]:
    return session.query(Playbook).filter(Playbook.slug == slug).one_or_none()


def checklist(pb: Playbook) -> list[dict]:
    """The `- [ ]` steps of a playbook: [{title, section}]."""
    return _extract_checklist_items(pb.content or "")


# ── Ticking ───────────────────────────────────────────────────────────────────

def step_keys(content: str) -> list[tuple[str, str]]:
    """[(key, title)] for each `- [ ]` step, in order.

    A step is identified by its **text**, not its position: reordering a playbook keeps its
    ticks, and rewording a step drops just that one. The same text appearing twice ("Lock the
    doors" under two sections) is told apart by occurrence — `#0`, `#1` — so each can be ticked
    on its own. 40 chars of SHA-1 fits `item_key`; this is an identifier, not a secret.
    """
    seen: dict[str, int] = {}
    out = []
    for item in _extract_checklist_items(content or ""):
        title = item["title"]
        norm = " ".join(title.lower().split())
        n = seen.get(norm, 0)
        seen[norm] = n + 1
        key = hashlib.sha1(f"{norm}#{n}".encode("utf-8")).hexdigest()
        out.append((key, title))
    return out


def get_checks(session: Session, pb: Playbook) -> dict[str, dict]:
    """{item_key: {"by": name, "at": datetime}} for the ticked steps still in the text.

    `at` is in the estate's time zone: ticks are stored as UTC, and a tick at 9pm Eastern must not
    be labelled with tomorrow's date on a checklist people read day by day.
    """
    from mihomes.services.config_service import estate_zone

    zone = estate_zone(session)
    live = {key for key, _ in step_keys(pb.content)}
    rows = (
        session.query(PlaybookCheck, User.name, User.email)
        .outerjoin(User, User.id == PlaybookCheck.checked_by)
        .filter(PlaybookCheck.playbook_id == pb.id)
        .all()
    )
    return {
        c.item_key: {"by": name or email, "at": c.checked_at.replace(tzinfo=timezone.utc).astimezone(zone)}
        for c, name, email in rows
        if c.item_key in live
    }


def set_check(session: Session, pb: Playbook, user_id, key: str, checked: bool) -> bool:
    """Tick or untick one step. Anyone who can open the playbook may — that is how staff use it.

    Returns whether the step is ticked afterwards. Raises `ValueError` for a key that is not a step
    of the playbook as it stands now: the page was open while someone edited it, and ticking a step
    that no longer exists must not silently succeed.
    """
    if key not in {k for k, _ in step_keys(pb.content)}:
        raise ValueError("That step has changed — reload the playbook to see the latest.")
    existing = (
        session.query(PlaybookCheck)
        .filter(PlaybookCheck.playbook_id == pb.id, PlaybookCheck.item_key == key)
        .one_or_none()
    )
    if checked and existing is None:
        try:
            with session.begin_nested():
                session.add(PlaybookCheck(
                    playbook_id=pb.id, item_key=key, checked_by=user_id,
                    checked_at=datetime.now(timezone.utc).replace(tzinfo=None),  # naive UTC
                ))
                session.flush()
        except IntegrityError:
            pass  # someone else ticked it in the same instant — already the state we want
    elif not checked and existing is not None:
        session.delete(existing)
        session.flush()
    return checked


def reset_checks(session: Session, editor: Membership | None, slug: str) -> int:
    """Untick every step of a playbook; returns how many were ticked. Editors only."""
    require_editor(editor)
    pb = get_playbook(session, slug)
    if pb is None:
        raise ValueError("That playbook no longer exists.")
    n = (
        session.query(PlaybookCheck)
        .filter(PlaybookCheck.playbook_id == pb.id)
        .delete(synchronize_session=False)
    )
    session.flush()
    if n:
        # `audit_log.action` is String(10).
        record_change(session, "playbook", pb.id, "reset", {"unticked": n})
    return n


def _prune_checks(session: Session, pb: Playbook) -> None:
    """Drop ticks for steps that are no longer in the text, so they cannot pile up."""
    live = [k for k, _ in step_keys(pb.content)]
    q = session.query(PlaybookCheck).filter(PlaybookCheck.playbook_id == pb.id)
    if live:
        q = q.filter(PlaybookCheck.item_key.notin_(live))
    q.delete(synchronize_session=False)


# ── Writing (every function here calls require_editor) ────────────────────────

def _clean(name: str, content: str) -> tuple[str, str]:
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the playbook a name.")
    if len(name) > MAX_NAME:
        raise ValueError(f"The name is too long (max {MAX_NAME} characters).")
    content = (content or "").replace("\r\n", "\n")
    if len(content) > MAX_CONTENT:
        raise ValueError("The playbook is too long to save.")
    return name, content


def create_playbook(
    session: Session,
    editor: Membership | None,
    *,
    name: str,
    content: str = "",
    kind: str | None = None,
    audience: str | None = None,
    estimated_time: str | None = None,
) -> Playbook:
    require_editor(editor)
    name, content = _clean(name, content)
    pb = Playbook(
        name=name,
        slug=ensure_unique_slug(session, Playbook, generate_slug(name)),
        content=content,
        kind=(kind or "").strip() or None,
        audience=(audience or "").strip() or None,
        estimated_time=(estimated_time or "").strip() or None,
    )
    session.add(pb)
    session.flush()
    record_change(session, "playbook", pb.id, "create", snapshot_instance(pb))
    return pb


def update_playbook(
    session: Session,
    editor: Membership | None,
    slug: str,
    *,
    name: str,
    content: str,
    kind: str | None = None,
    audience: str | None = None,
    estimated_time: str | None = None,
) -> Playbook:
    require_editor(editor)
    pb = get_playbook(session, slug)
    if pb is None:
        raise ValueError("That playbook no longer exists.")
    name, content = _clean(name, content)
    before = snapshot_instance(pb)
    if name != pb.name:
        pb.slug = ensure_unique_slug(session, Playbook, generate_slug(name), exclude_id=pb.id)
    pb.name = name
    pb.content = content
    pb.kind = (kind or "").strip() or None
    pb.audience = (audience or "").strip() or None
    pb.estimated_time = (estimated_time or "").strip() or None
    session.flush()
    _prune_checks(session, pb)
    changes = diff_instance(before, snapshot_instance(pb))
    if changes:
        record_change(session, "playbook", pb.id, "update", changes)
    return pb


def delete_playbook(session: Session, editor: Membership | None, slug: str) -> str:
    require_editor(editor)
    pb = get_playbook(session, slug)
    if pb is None:
        raise ValueError("That playbook no longer exists.")
    name = pb.name
    record_change(session, "playbook", pb.id, "delete", snapshot_instance(pb))
    session.delete(pb)
    session.flush()
    return name


# ── Template gallery ──────────────────────────────────────────────────────────

def list_templates() -> list[dict]:
    """The starter gallery: [{slug, name, kind, audience, estimated_time, steps, summary}]."""
    if not TEMPLATES_DIR.exists():
        return []
    out = []
    for f in sorted(TEMPLATES_DIR.glob("*.md")):
        parsed = parse_markdown(f.read_text(encoding="utf-8"), fallback_name=_slug_to_name(f.stem))
        parsed["slug"] = f.stem
        parsed["steps"] = len(_extract_checklist_items(parsed["content"]))
        out.append(parsed)
    return out


def get_template(slug: str) -> Optional[dict]:
    # Only names the gallery actually contains — never a path built from user input.
    return next((t for t in list_templates() if t["slug"] == slug), None)


def copy_template(session: Session, editor: Membership | None, template_slug: str) -> Playbook:
    """Copy a gallery template into the account as an ordinary, editable playbook."""
    tpl = get_template(template_slug)
    if tpl is None:
        raise ValueError("That template doesn't exist.")
    return create_playbook(
        session, editor,
        name=tpl["name"], content=tpl["content"], kind=tpl["kind"],
        audience=tpl["audience"], estimated_time=tpl["estimated_time"],
    )


def import_markdown(session: Session, slug: str, text: str) -> tuple[Playbook, bool]:
    """Load one markdown playbook into the current account; (row, created). Idempotent by slug.

    For `scripts/import_playbooks.py` — an operator moving an account's files into its rows,
    so there is no member to check; the caller binds the account context.
    """
    existing = get_playbook(session, slug)
    if existing is not None:
        return existing, False
    parsed = parse_markdown(text, fallback_name=_slug_to_name(slug))
    pb = Playbook(
        name=parsed["name"][:MAX_NAME], slug=slug, content=parsed["content"],
        kind=parsed["kind"], audience=parsed["audience"],
        estimated_time=parsed["estimated_time"],
    )
    session.add(pb)
    session.flush()
    record_change(session, "playbook", pb.id, "import", {"slug": slug})
    return pb, True


def knowledge_search(query: str) -> list[dict]:
    """Full-text search across all files in the knowledge base."""
    query_lower = query.lower()
    results = []
    kb = kb_path()
    if not kb.exists():
        return results
    for f in sorted(kb.rglob("*.md")):
        try:
            content = f.read_text(errors="ignore")
        except OSError:
            continue
        if query_lower in content.lower():
            # Find surrounding context for each match
            lines = content.splitlines()
            matches = [
                (i + 1, line.strip())
                for i, line in enumerate(lines)
                if query_lower in line.lower()
            ]
            results.append({
                "file": str(f.relative_to(kb)),
                "title": _slug_to_name(f.stem),
                "matches": matches[:5],  # cap to 5 lines per file
            })
    return results


# ── Markdown parsing helpers ──────────────────────────────────────────────────

_META_KEYS = {"type": "kind", "owner": "audience", "estimated time": "estimated_time"}


def parse_markdown(text: str, *, fallback_name: str = "Untitled playbook") -> dict:
    """Split a markdown playbook into {name, kind, audience, estimated_time, content}.

    Format:
        # Title
        (blank)
        **Type**: recurring
        **Owner**: Lead Housekeeper
        ...
        ---

    The title and the recognised `**Key**: value` lines become fields; `content` keeps the whole
    text, so nothing in the original is lost and the detail page still shows it.
    """
    meta: dict = {"name": fallback_name, "kind": None, "audience": None, "estimated_time": None}
    past_title = False
    for line in text.splitlines():
        stripped = line.strip()
        if not past_title and stripped.startswith("# "):
            meta["name"] = stripped[2:].strip() or fallback_name
            past_title = True
            continue
        if stripped == "---" or (past_title and stripped.startswith("## ")):
            break
        m = re.match(r'\*\*(.+?)\*\*\s*:\s*(.+)', stripped)
        if m and m.group(1).lower() in _META_KEYS:
            meta[_META_KEYS[m.group(1).lower()]] = m.group(2).strip()[:200]
    meta["content"] = text.replace("\r\n", "\n")
    return meta


def _extract_checklist_items(content: str) -> list[dict]:
    """
    Extract all '- [ ]' checklist items from markdown, tracking section headers.
    Returns list of {title, section}.
    """
    items = []
    current_section = ""

    for line in content.splitlines():
        # Track section headers (## ### ####)
        header_match = re.match(r'^#{2,4}\s+(.+)', line)
        if header_match:
            current_section = header_match.group(1).strip()

        # Checklist items
        checklist_match = re.match(r'\s*-\s+\[\s*\]\s+(.+)', line)
        if checklist_match:
            items.append({
                "title": checklist_match.group(1).strip(),
                "section": current_section,
            })

    return items


def _slug_to_name(slug: str) -> str:
    return slug.replace("-", " ").title()
