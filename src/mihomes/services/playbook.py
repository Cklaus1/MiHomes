"""Playbook service — markdown-first operational playbooks with task execution."""

import re
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from mihomes.models.task import Task, TaskPriority
from mihomes.models.property import Property
from mihomes.services.slug import resolve_identifier
from mihomes.services.task import create_task

# Default knowledge base location — project root / knowledge, or override via env
# __file__ is src/mihomes/services/playbook.py → go up 4 levels to project root
_DEFAULT_KB = Path(__file__).resolve().parents[3] / "knowledge"


def kb_path() -> Path:
    """Return the knowledge base directory."""
    import os
    override = os.environ.get("MIHOMES_KB")
    return Path(override) if override else _DEFAULT_KB


def playbooks_dir() -> Path:
    return kb_path() / "playbooks"


def list_playbooks() -> list[dict]:
    """List all available playbooks from the knowledge/playbooks/ directory."""
    d = playbooks_dir()
    if not d.exists():
        return []
    results = []
    for f in sorted(d.glob("*.md")):
        meta = _parse_frontmatter(f)
        results.append({
            "slug": f.stem,
            "name": meta.get("name") or _slug_to_name(f.stem),
            "type": meta.get("type", "—"),
            "owner": meta.get("owner", "—"),
            "file": str(f),
        })
    return results


def get_playbook(slug: str) -> Optional[dict]:
    """Load a playbook by slug (filename without .md)."""
    path = playbooks_dir() / f"{slug}.md"
    if not path.exists():
        # Try partial match
        matches = list(playbooks_dir().glob(f"*{slug}*.md"))
        if not matches:
            return None
        path = matches[0]
    meta = _parse_frontmatter(path)
    content = path.read_text(encoding="utf-8")
    checklist = _extract_checklist_items(content)
    return {
        "slug": path.stem,
        "name": meta.get("name") or _slug_to_name(path.stem),
        "type": meta.get("type", "—"),
        "owner": meta.get("owner", "—"),
        "estimated_time": meta.get("estimated time", "—"),
        "content": content,
        "checklist": checklist,
        "file": str(path),
    }


def run_playbook(
    session: Session,
    slug: str,
    property_id_or_slug: str,
    *,
    priority: TaskPriority = TaskPriority.MEDIUM,
    start_date: Optional[date] = None,
    assignee_id: Optional[int] = None,
) -> list[Task]:
    """
    Instantiate a playbook into tasks for a property.

    Extracts all checklist items (lines starting with '- [ ]') from the
    playbook markdown and creates one Task per item, staggered by section.
    """
    pb = get_playbook(slug)
    if not pb:
        raise ValueError(f"Playbook '{slug}' not found in {playbooks_dir()}")

    if not pb["checklist"]:
        raise ValueError(f"Playbook '{slug}' has no checklist items (lines starting with '- [ ]')")

    # Validate property
    prop = resolve_identifier(session, Property, property_id_or_slug)
    base_date = start_date or date.today()
    tasks = []

    for i, item in enumerate(pb["checklist"]):
        task = create_task(
            session,
            item["title"],
            property_id_or_slug,
            priority=priority,
            due_date=base_date + timedelta(days=item.get("day_offset", 0)),
            description=f"Playbook: {pb['name']} › {item.get('section', '')}".rstrip(" ›"),
        )
        if assignee_id:
            task.assignee_id = assignee_id
        tasks.append(task)

    return tasks


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

def _parse_frontmatter(path: Path) -> dict:
    """
    Extract key: value metadata from a markdown playbook file.

    Playbooks use the format:
        # Title
        (blank)
        **Type**: daily
        **Owner**: Lead Housekeeper
        ...
        ---

    Reads metadata lines after the title heading until a `---` separator
    or a second `##` heading is encountered.
    """
    meta = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    past_title = False
    for line in lines:
        stripped = line.strip()
        # Skip the first # heading (title line)
        if not past_title and stripped.startswith("# "):
            past_title = True
            continue
        # Stop at horizontal rule or any ## subheading
        if stripped == "---" or (past_title and stripped.startswith("## ")):
            break
        if not past_title or not stripped:
            continue
        # **Key**: value
        m = re.match(r'\*\*(.+?)\*\*\s*:\s*(.+)', stripped)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
    return meta


def _extract_checklist_items(content: str) -> list[dict]:
    """
    Extract all '- [ ]' checklist items from markdown, tracking section headers.
    Returns list of {title, section, day_offset}.
    """
    items = []
    current_section = ""
    day_offset = 0
    seen_sections: list[str] = []

    for line in content.splitlines():
        # Track section headers (## or ###)
        header_match = re.match(r'^#{2,4}\s+(.+)', line)
        if header_match:
            current_section = header_match.group(1).strip()
            # Increment day offset when we enter a new top-level section
            if line.startswith("## ") and current_section not in seen_sections:
                seen_sections.append(current_section)
                day_offset = len(seen_sections) - 1

        # Checklist items
        checklist_match = re.match(r'\s*-\s+\[\s*\]\s+(.+)', line)
        if checklist_match:
            items.append({
                "title": checklist_match.group(1).strip(),
                "section": current_section,
                "day_offset": day_offset,
            })

    return items


def _slug_to_name(slug: str) -> str:
    return slug.replace("-", " ").title()
