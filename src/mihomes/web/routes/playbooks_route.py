"""Playbooks route — each account's own playbooks: read, tick, and (for editors) write.

A playbook is a document, not a task template (2026-10-22): nothing here creates tasks. Viewing
and ticking a step are `task.manage` — anyone who can open a playbook can tick it, which is how
staff use one. Writing and Reset are `playbook.manage`, which lets staff through the gate and
leaves the per-member rule to `services.playbook` (`require_editor`) — owners and admins always,
staff only with `can_edit_playbooks`.
"""

import html
import logging
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import Markup
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.membership import Membership
from mihomes.models.playbook import Playbook
from mihomes.services import playbook as pb_svc
from mihomes.web.deps import get_db, require_authenticated, templates

logger = logging.getLogger(__name__)

router = APIRouter()


_STEP = re.compile(r'\s*-\s+\[\s*\]\s+(.+)')       # same pattern as services.playbook
_DONE = re.compile(r'\s*-\s+\[[xX]\]\s+(.+)')       # written as already done: static tick


def _when(at) -> str:
    """"Oct 22, 9:04 pm" — in the estate's time zone (the service converted it)."""
    hour = at.hour % 12 or 12
    return f"{at:%b} {at.day}, {hour}:{at.minute:02d} {'am' if at.hour < 12 else 'pm'}"


def _step_li(label: str, key: str, slug: str, info: dict | None, can_tick: bool) -> str:
    """One interactive step. Also what `POST /{slug}/check` returns, so a tick swaps just this line.

    The checkbox posts itself on change (htmx); an unticked box sends nothing, a ticked one sends
    `checked=1`, and the server sets the step to match. The markup is the `step` macro in
    `partials/_playbook_step.html` (Tailwind only sees classes in templates); the label arrives
    already escaped from `_inline`, and Jinja escapes the name, key and slug.
    """
    who = {"by": info.get("by") or "Someone", "when": _when(info["at"])} if info else None
    macro = templates.env.get_template("partials/_playbook_step.html").module.step
    return str(macro(Markup(_inline(label)), key, slug, who, can_tick))


def _md_to_html(text: str, *, slug: str | None = None, checks: dict | None = None,
                can_tick: bool = False) -> str:
    """Minimal markdown → HTML for playbook rendering.

    With `slug` and `checks` (the detail page) each `- [ ]` step is a live tick box; without them
    (editor preview, template gallery) it is the static empty square — nothing there can be ticked.
    """
    interactive = slug is not None and checks is not None
    keys = [k for k, _ in pb_svc.step_keys(text)] if interactive else []
    step_no = 0
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
        elif _STEP.match(line) or _DONE.match(line):
            done = _DONE.match(line)
            label = (done or _STEP.match(line)).group(1)
            if not in_ul:
                out.append('<ul class="space-y-1 my-2 list-none">')
                in_ul = True
            if done:
                tick = '<svg class="w-4 h-4 text-emerald-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
                out.append(f'<li class="flex items-start gap-2 text-sm text-gray-700">{tick}<span>{_inline(label)}</span></li>')
            elif interactive and step_no < len(keys):
                key = keys[step_no]
                step_no += 1
                out.append(_step_li(label, key, slug, checks.get(key), can_tick))
            else:
                step_no += 1
                tick = '<span class="w-4 h-4 rounded border border-gray-300 flex-shrink-0 mt-0.5 inline-block"></span>'
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
    """Convert inline markdown (bold, italic, code, backtick paths) to HTML.

    **Escapes first.** Every rendered line passes through here and the result is output with
    `| safe`, and since 2026-10-21 playbook text is written by users — so a `<script>` in a step
    would run for everyone who opened the playbook. Escaping before the markdown regexes means the
    only tags in the output are the ones this function adds.
    """
    text = html.escape(text, quote=True)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Inline code / paths
    text = re.sub(r'`([^`]+)`', r'<code class="bg-gray-100 px-1 py-0.5 rounded text-xs font-mono">\1</code>', text)
    return text


#: Fixed notices after a redirect — keyed, never echoed from the query string.
_NOTICES = {
    "created": "Playbook created.",
    "saved": "Playbook saved.",
    "deleted": "Playbook deleted.",
    "copied": "Template added to your playbooks — edit it to fit your home.",
    "reset": "All steps unticked — ready for a fresh pass.",
}


def _editor(db: Session, principal) -> Membership | None:
    return db.get(Membership, principal.membership_id)


def _view(pb: Playbook) -> dict:
    """The detail/list templates' shape, from a row."""
    return {
        "slug": pb.slug,
        "name": pb.name,
        "type": pb.kind or "—",
        "owner": pb.audience or "—",
        "estimated_time": pb.estimated_time or "—",
        "content": pb.content,
        "checklist": pb_svc.checklist(pb),
    }


def _not_allowed(request: Request, db: Session, exc: Exception):
    return templates.TemplateResponse(request, "playbooks.html", {
        "page": "playbooks",
        "playbooks": [_view(p) for p in pb_svc.list_playbooks(db)],
        "can_edit": False,
        "error": str(exc),
    }, status_code=403)


@router.get("/")
@declares("task.manage", Access.COLLECTION)
def playbooks_list(
    request: Request,
    notice: str = "",
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(request, "playbooks.html", {
        "page": "playbooks",
        "playbooks": [_view(p) for p in pb_svc.list_playbooks(db)],
        "can_edit": pb_svc.can_edit(_editor(db, principal)),
        "template_count": len(pb_svc.list_templates()),
        "notice": _NOTICES.get(notice),
    })


@router.get("/templates")
@declares("task.manage", Access.COLLECTION)
def playbook_templates(
    request: Request,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(request, "playbook_templates.html", {
        "page": "playbooks",
        "templates": pb_svc.list_templates(),
        "can_edit": pb_svc.can_edit(_editor(db, principal)),
    })


@router.get("/templates/{template_slug}")
@declares("task.manage", Access.COLLECTION)
def playbook_template_preview(
    request: Request,
    template_slug: str,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    tpl = pb_svc.get_template(template_slug)
    if tpl is None:
        return RedirectResponse("/playbooks/templates", status_code=303)
    return templates.TemplateResponse(request, "playbook_template_preview.html", {
        "page": "playbooks",
        "tpl": tpl,
        "html_content": _md_to_html(tpl["content"]),
        "can_edit": pb_svc.can_edit(_editor(db, principal)),
    })


@router.post("/templates/{template_slug}/use")
@declares("playbook.manage", Access.ACCOUNT)
def use_template(
    request: Request,
    template_slug: str,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    try:
        pb = pb_svc.copy_template(db, _editor(db, principal), template_slug)
        db.commit()
    except pb_svc.PlaybookPermissionError as exc:
        return _not_allowed(request, db, exc)
    except ValueError:
        return RedirectResponse("/playbooks/templates", status_code=303)
    return RedirectResponse(f"/playbooks/{pb.slug}/edit?notice=copied", status_code=303)


def _form_page(request: Request, *, form: dict, mode: str, slug: str | None = None,
               error: str | None = None, notice: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(request, "playbook_form.html", {
        "page": "playbooks",
        "form": form,
        "mode": mode,
        "slug": slug,
        "error": error,
        "notice": notice,
        "preview_html": _md_to_html(form.get("content") or ""),
        "step_count": len(pb_svc._extract_checklist_items(form.get("content") or "")),
    }, status_code=status_code)


_STARTER = """# New playbook

## First section
- [ ] First step
- [ ] Second step
"""


@router.get("/new")
@declares("playbook.manage", Access.ACCOUNT)
def new_playbook_form(
    request: Request,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    try:
        pb_svc.require_editor(_editor(db, principal))
    except pb_svc.PlaybookPermissionError as exc:
        return _not_allowed(request, db, exc)
    return _form_page(request, mode="new", form={"name": "", "kind": "", "audience": "",
                                                 "estimated_time": "", "content": _STARTER})


@router.post("/new")
@declares("playbook.manage", Access.ACCOUNT)
def create_playbook(
    request: Request,
    name: str = Form(""),
    kind: str = Form(""),
    audience: str = Form(""),
    estimated_time: str = Form(""),
    content: str = Form(""),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    form = {"name": name, "kind": kind, "audience": audience,
            "estimated_time": estimated_time, "content": content}
    try:
        pb = pb_svc.create_playbook(db, _editor(db, principal), **form)
        db.commit()
    except pb_svc.PlaybookPermissionError as exc:
        return _not_allowed(request, db, exc)
    except ValueError as exc:
        return _form_page(request, mode="new", form=form, error=str(exc), status_code=400)
    return RedirectResponse(f"/playbooks/{pb.slug}?notice=created", status_code=303)


@router.get("/{slug}/edit")
@declares("playbook.manage", Access.ACCOUNT)
def edit_playbook_form(
    request: Request,
    slug: str,
    notice: str = "",
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    try:
        pb_svc.require_editor(_editor(db, principal))
    except pb_svc.PlaybookPermissionError as exc:
        return _not_allowed(request, db, exc)
    pb = pb_svc.get_playbook(db, slug)
    if pb is None:
        return RedirectResponse("/playbooks/", status_code=303)
    form = {"name": pb.name, "kind": pb.kind or "", "audience": pb.audience or "",
            "estimated_time": pb.estimated_time or "", "content": pb.content}
    return _form_page(request, mode="edit", slug=slug, form=form, notice=_NOTICES.get(notice))


@router.post("/{slug}/edit")
@declares("playbook.manage", Access.ACCOUNT)
def update_playbook(
    request: Request,
    slug: str,
    name: str = Form(""),
    kind: str = Form(""),
    audience: str = Form(""),
    estimated_time: str = Form(""),
    content: str = Form(""),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    form = {"name": name, "kind": kind, "audience": audience,
            "estimated_time": estimated_time, "content": content}
    try:
        pb = pb_svc.update_playbook(db, _editor(db, principal), slug, **form)
        db.commit()
    except pb_svc.PlaybookPermissionError as exc:
        return _not_allowed(request, db, exc)
    except ValueError as exc:
        return _form_page(request, mode="edit", slug=slug, form=form, error=str(exc),
                          status_code=400)
    return RedirectResponse(f"/playbooks/{pb.slug}?notice=saved", status_code=303)


@router.post("/{slug}/delete")
@declares("playbook.manage", Access.ACCOUNT)
def delete_playbook(
    request: Request,
    slug: str,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    try:
        pb_svc.delete_playbook(db, _editor(db, principal), slug)
        db.commit()
    except pb_svc.PlaybookPermissionError as exc:
        return _not_allowed(request, db, exc)
    except ValueError:
        pass
    return RedirectResponse("/playbooks/?notice=deleted", status_code=303)


@router.post("/preview", response_class=HTMLResponse)
@declares("playbook.manage", Access.ACCOUNT)
def preview_playbook(request: Request, content: str = Form("")):
    """The editor's Preview tab: the same renderer the detail page uses, so what you see is what
    gets saved. Renders text only — nothing is stored."""
    return HTMLResponse(_md_to_html(content[:pb_svc.MAX_CONTENT]))


def _detail(request: Request, db: Session, principal, pb: Playbook, notice: str | None = None):
    checks = pb_svc.get_checks(db, pb)
    can_tick = True  # anyone who can open a playbook can tick it
    steps = pb_svc.checklist(pb)
    return templates.TemplateResponse(request, "playbook_detail.html", {
        "page": "playbooks",
        "pb": _view(pb),
        "html_content": _md_to_html(pb.content, slug=pb.slug, checks=checks, can_tick=can_tick),
        "can_edit": pb_svc.can_edit(_editor(db, principal)),
        "ticked": len(checks),
        "total": len(steps),
        "notice": notice,
    })


@router.get("/{slug}")
@declares("task.manage", Access.COLLECTION)
def playbook_detail(
    request: Request,
    slug: str,
    notice: str = "",
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    pb = pb_svc.get_playbook(db, slug)
    if pb is None:
        return RedirectResponse("/playbooks/")
    return _detail(request, db, principal, pb, notice=_NOTICES.get(notice))


@router.post("/{slug}/check", response_class=HTMLResponse)
@declares("task.manage", Access.COLLECTION)
def tick_step(
    request: Request,
    slug: str,
    key: str = Form(...),
    checked: str = Form(""),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Tick or untick one step; answers with just that line so the page swaps it in place."""
    pb = pb_svc.get_playbook(db, slug)
    if pb is None:
        return HTMLResponse("That playbook no longer exists.", status_code=404)
    try:
        pb_svc.set_check(db, pb, principal.user_id, key, checked == "1")
        db.commit()
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=409)
    title = dict(pb_svc.step_keys(pb.content))[key]
    info = pb_svc.get_checks(db, pb).get(key)
    return HTMLResponse(_step_li(title, key, pb.slug, info, True))


@router.post("/{slug}/reset")
@declares("playbook.manage", Access.ACCOUNT)
def reset_playbook_checks(
    request: Request,
    slug: str,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Untick every step (a fresh pass through the playbook). Editors only."""
    try:
        pb_svc.reset_checks(db, _editor(db, principal), slug)
        db.commit()
    except pb_svc.PlaybookPermissionError as exc:
        return _not_allowed(request, db, exc)
    except ValueError:
        return RedirectResponse("/playbooks/", status_code=303)
    return RedirectResponse(f"/playbooks/{slug}?notice=reset", status_code=303)
