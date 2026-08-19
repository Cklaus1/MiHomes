"""Team — invite acceptance and the account switcher (SPEC-003 §6 Steps 12, 13).

**Two different authorisation stories in one file, and that is why the split matters.**

- *Accepting* an invitation and *switching* accounts are `Access.SESSION`: the invitee is not yet
  a member of anything, and the switcher targets an account other than the current one. Neither
  can be authorised by a role within the current account, because that is precisely what is
  changing.
- *Creating* and *revoking* invitations are ordinary account actions (rows 11/12), declared
  normally and gated by the matrix like everything else.

Keeping both in one module makes the boundary visible rather than implied by directory layout.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares, declares_session
from mihomes.models.user import User
from mihomes.services import invite_service
from mihomes.services.account_switcher import available_accounts, switch_account
from mihomes.tenancy import require_user
from mihomes.web.deps import get_db, require_authenticated, templates

router = APIRouter()

_ACCEPT_REASON = (
    "An invitee is not a member of any account when they open this link, so there is no "
    "membership to authorise against — the token is the authority (D5)."
)
_SWITCH_REASON = (
    "Switching targets an account other than the current one, so authorising it against the "
    "current account's role would ask the wrong question entirely."
)


# ── Access.SESSION — before or across account selection ───────────────────────


@router.get("/invite/{token}")
@declares_session(_ACCEPT_REASON)
def show_invite(request: Request, token: str, db: Session = Depends(get_db)):
    """Show what this invitation grants, before accepting it.

    Deliberately reveals only the account name and role. The token is the authority (D5), so
    anyone holding the link reaches this page — there is nothing here worth more than what
    accepting would already give them.
    """
    invite = invite_service.find_pending(db, token)
    return templates.TemplateResponse(
        request,
        "team/invite.html",
        {"page": "invite", "invite": invite, "token": token},
    )


@router.post("/invite/{token}/accept")
@declares_session(_ACCEPT_REASON)
def accept(request: Request, token: str, db: Session = Depends(get_db)):
    """Redeem the invitation, then land in the account it granted."""
    user = db.get(User, require_user())
    try:
        membership = invite_service.accept_invite(db, token, user)
    except invite_service.InviteError as exc:
        return templates.TemplateResponse(
            request,
            "team/invite.html",
            {"page": "invite", "invite": None, "token": token, "error": str(exc)},
            status_code=400,
        )

    _select(db, request, membership.account_id, user.id)
    return RedirectResponse("/", status_code=303)


@router.post("/accounts/switch")
@declares_session(_SWITCH_REASON)
def switch(request: Request, account_id: uuid.UUID = Form(...),
           db: Session = Depends(get_db)):
    """D11 — point this session at another of the user's accounts.

    The membership check lives in `set_current_account`, server-side, because the id arrives from
    a form the user controls. A rejected switch redirects unchanged rather than erroring: the
    common cause is a stale tab whose account was revoked, and a 403 there is noise.
    """
    user_id = require_user()
    auth = _auth(db, request)
    if auth is not None:
        switch_account(db, auth.session_id, user_id, account_id)
    return RedirectResponse("/", status_code=303)


# ── Ordinary account actions — rows 11/12, gated by the matrix ────────────────


@router.post("/team/invites")
@declares("invite.create", Access.ACCOUNT)
def create(
    request: Request,
    email: str = Form(...),
    role: str = Form("staff"),
    property_ids: list[uuid.UUID] = Form(default=[]),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Row 11. A21's "staff needs a scope" refusal surfaces as a form error, not a 500."""
    try:
        invite_service.create_invite(
            db, principal.account_id, principal.user_id, email, role, property_ids
        )
    except invite_service.InviteError as exc:
        return templates.TemplateResponse(
            request, "team/index.html",
            {"page": "team", "error": str(exc), "accounts": []}, status_code=400,
        )
    return RedirectResponse("/team", status_code=303)


@router.post("/team/invites/{invite_id}/revoke")
@declares("invite.modify", Access.ACCOUNT)
def revoke(
    request: Request,
    invite_id: uuid.UUID,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Row 12. Revoking frees the seat immediately (D6)."""
    from mihomes.models.invite import Invite

    invite = db.get(Invite, invite_id)
    if invite is not None:
        invite_service.revoke_invite(db, invite)
    return RedirectResponse("/team", status_code=303)


@router.get("/team")
@declares("member.manage", Access.ACCOUNT)
def index(request: Request, principal=require_authenticated(),
          db: Session = Depends(get_db)):
    """Row 10 — the team screen. Also renders the switcher when there is one to render."""
    return templates.TemplateResponse(
        request,
        "team/index.html",
        {
            "page": "team",
            "accounts": available_accounts(db, principal.user_id),
            "current_account_id": principal.account_id,
        },
    )


def _auth(db: Session, request: Request):
    from mihomes.auth.sessions import SESSION_COOKIE, lookup_session

    return lookup_session(db, request.cookies.get(SESSION_COOKIE))


def _select(db: Session, request: Request, account_id: uuid.UUID, user_id: uuid.UUID) -> None:
    auth = _auth(db, request)
    if auth is not None:
        switch_account(db, auth.session_id, user_id, account_id)
