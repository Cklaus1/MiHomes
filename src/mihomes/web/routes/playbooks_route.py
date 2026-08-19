"""Playbooks route — view and run operational playbooks."""

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.services import property as prop_svc
from mihomes.services.playbook import get_playbook, list_playbooks, run_playbook
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _md_to_html(text: str) -> str:
    """Minimal markdown → HTML for playbook rendering."""
    lines = text.splitlines()
    out = []
    in_ul = False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for line in lines:
        # Headings
        if line.startswith("#### "):
            close_ul()
            out.append(f'<h4 class="text-sm font-semibold text-gray-700 mt-4 mb-1">{_inline(line[5:])}</h4>')
        elif line.startswith("### "):
            close_ul()
            out.append(f'<h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mt-5 mb-2">{_inline(line[4:])}</h3>')
        elif line.startswith("## "):
            close_ul()
            out.append(f'<h2 class="text-base font-bold text-gray-900 mt-6 mb-2 pt-4 border-t border-gray-200">{_inline(line[3:])}</h2>')
        elif line.startswith("# "):
            close_ul()
            out.append(f'<h1 class="text-lg font-bold text-gray-900 mb-1">{_inline(line[2:])}</h1>')
        # Horizontal rule
        elif line.strip() == "---":
            close_ul()
            out.append('<hr class="my-4 border-gray-200"/>')
        # Checklist items
        elif re.match(r'\s*-\s+\[[ x]\]\s+', line):
            checked = bool(re.match(r'\s*-\s+\[x\]', line, re.I))
            label = re.sub(r'\s*-\s+\[[ xX]\]\s+', '', line)
            if not in_ul:
                out.append('<ul class="space-y-1 my-2">')
                in_ul = True
            tick = '<svg class="w-4 h-4 text-emerald-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>' if checked else '<span class="w-4 h-4 rounded border border-gray-300 flex-shrink-0 mt-0.5 inline-block"></span>'
            out.append(f'<li class="flex items-start gap-2 text-sm text-gray-700">{tick}<span>{_inline(label)}</span></li>')
        # Bullet items
        elif re.match(r'\s*[-*]\s+', line):
            label = re.sub(r'\s*[-*]\s+', '', line, count=1)
            if not in_ul:
                out.append('<ul class="list-disc list-inside space-y-0.5 my-2 ml-2">')
                in_ul = True
            out.append(f'<li class="text-sm text-gray-700">{_inline(label)}</li>')
        # Blank line
        elif not line.strip():
            close_ul()
            out.append('<div class="h-2"></div>')
        # Paragraph
        else:
            close_ul()
            out.append(f'<p class="text-sm text-gray-700">{_inline(line)}</p>')

    close_ul()
    return "\n".join(out)


def _inline(text: str) -> str:
    """Convert inline markdown (bold, italic, code, backtick paths) to HTML."""
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Inline code / paths
    text = re.sub(r'`([^`]+)`', r'<code class="bg-gray-100 px-1 py-0.5 rounded text-xs font-mono">\1</code>', text)
    return text


@router.get("/")
@declares("task.manage", Access.COLLECTION)
def playbooks_list(request: Request, db: Session = Depends(get_db)):
    playbooks = list_playbooks()
    # Count checklist items per playbook
    enriched = []
    for pb in playbooks:
        detail = get_playbook(pb["slug"])
        enriched.append({
            **pb,
            "item_count": len(detail["checklist"]) if detail else 0,
        })
    return templates.TemplateResponse(request, "playbooks.html", {
        "page": "playbooks",
        "playbooks": enriched,
    })


@router.get("/{slug}")
@declares("task.manage", Access.COLLECTION)
def playbook_detail(request: Request, slug: str, db: Session = Depends(get_db)):
    pb = get_playbook(slug)
    if not pb:
        return RedirectResponse("/playbooks/")
    properties = prop_svc.list_properties(db)
    return templates.TemplateResponse(request, "playbook_detail.html", {
        "page": "playbooks",
        "pb": pb,
        "html_content": _md_to_html(pb["content"]),
        "properties": properties,
        "run_result": None,
    })


@router.post("/{slug}/run", response_class=HTMLResponse)
@declares("task.manage", Access.ITEM)
def playbook_run(
    request: Request,
    slug: str,
    property_slug: str = Form(...),
    start_date: str = Form(""),
    db: Session = Depends(get_db),
):
    from datetime import date
    pb = get_playbook(slug)
    if not pb:
        return RedirectResponse("/playbooks/")
    properties = prop_svc.list_properties(db)
    run_result = None
    try:
        sd = date.fromisoformat(start_date) if start_date else None
        tasks = run_playbook(db, slug, property_slug, start_date=sd)
        db.commit()
        run_result = {"ok": True, "count": len(tasks), "titles": [t.title for t in tasks]}
    except Exception as e:
        run_result = {"ok": False, "error": str(e)}

    return templates.TemplateResponse(request, "playbook_detail.html", {
        "page": "playbooks",
        "pb": pb,
        "html_content": _md_to_html(pb["content"]),
        "properties": properties,
        "run_result": run_result,
    })
