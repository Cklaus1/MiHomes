"""Team — invite acceptance and the account switcher (SPEC-003 §6 Steps 12, 13).

**Two different authorisation stories in one file, and that is why the split matters.**

- *Accepting* an invitation and *switching* accounts are `Access.SESSION`: the invitee is not yet
  a member of anything, and the switcher targets an account other than the current one. Neither
  can be authorised by a role within the current account, because that is precisely what is
  changing.
- Creating and revoking invitations live in Settings → Members (`routes/settings.py`); `/team`
  itself is now only a redirect there.

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
from mihomes.services.account_switcher import switch_account
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
    from mihomes.models.account import Account

    invite = invite_service.find_pending(db, token)
    # `accounts` is a global table, so the invitee can read its name without being a member.
    account = db.get(Account, invite.account_id) if invite is not None else None
    return templates.TemplateResponse(
        request,
        "team/invite.html",
        {
            "page": "invite",
            "invite": invite,
            "token": token,
            "account_name": account.name if account is not None else None,
        },
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

    _notify_inviter(db, token, membership, user)
    _select(db, request, membership.account_id, user.id)
    return RedirectResponse("/", status_code=303)


def _notify_inviter(db: Session, token: str, membership, user) -> None:
    """Tell whoever sent the invitation that it was accepted, with §6.3's mismatch notice.

    Queued in the acceptance's own transaction, under the account it just joined (`accept_invite`
    has already bound that account's GUC). Never raises: the membership is real either way, and
    a missing mail provider must not turn a successful join into an error page. No inviter (an
    invite minted outside the web app) means no one to tell.
    """
    import logging

    from sqlalchemy import select

    from mihomes.models.account import Account
    from mihomes.models.invite import Invite
    from mihomes.services.email import EmailService, get_email_provider
    from mihomes.services.email.provider import EmailProviderError
    from mihomes.tenancy import account_context

    inviter = db.get(User, membership.invited_by) if membership.invited_by else None
    if inviter is None or not inviter.email:
        return
    try:
        with account_context(membership.account_id):
            invite = db.execute(
                select(Invite).where(Invite.token_hash == invite_service.hash_token(token))
            ).scalar_one_or_none()
            account = db.get(Account, membership.account_id)
            EmailService(get_email_provider(), session=db).send_invite_accepted(
                inviter.email,
                account_name=account.name if account is not None else "your estate",
                member_email=user.email,
                role=membership.role,
                # Only on a real mismatch: the template compares exactly, and a capital letter
                # would otherwise raise a false "accepted by someone else" warning.
                invited_email=(
                    invite.email
                    if invite is not None and invite_service.email_mismatch(invite, user)
                    else None
                ),
            )
    except EmailProviderError:
        logging.getLogger(__name__).exception("invite-accepted email could not be queued")


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


# ── The old team screen — now part of Settings ────────────────────────────────


@router.get("/team")
@declares("member.manage", Access.ACCOUNT)
def index(principal=require_authenticated()):
    """Row 10. Members, invitations and the account switcher all live in Settings now.

    The team page's invite form discarded the one-time link `create_invite` returns, so an
    invitation made here was a consumed seat nobody could redeem; Settings shows the link. Kept
    as a redirect for old bookmarks, and still declared, so a staff member gets the same 403 and
    an account-less session the same onboarding redirect as before.
    """
    return RedirectResponse("/settings#members", status_code=303)


def _auth(db: Session, request: Request):
    from mihomes.auth.sessions import SESSION_COOKIE, lookup_session

    return lookup_session(db, request.cookies.get(SESSION_COOKIE))


def _select(db: Session, request: Request, account_id: uuid.UUID, user_id: uuid.UUID) -> None:
    auth = _auth(db, request)
    if auth is not None:
        switch_account(db, auth.session_id, user_id, account_id)
