"""The 6-step onboarding wizard — SPEC-003 §6 Step 11 (A17, A18).

Every route here is `Access.SESSION`: onboarding steps 1-2 run **before an account exists**, so
the enforcement dependency must not try to resolve one. See `authz/actions.py`'s `Access.SESSION`
for why that class had to be added rather than forced into `ACCOUNT`.

The wizard is a thin shell over `onboarding_service`, which owns the resumability logic — the
service decides *where* the user is, these routes only render it and post the answers back.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from mihomes.auth.sessions import SESSION_COOKIE, lookup_session, set_current_account
from mihomes.authz.declare import declares_session
from mihomes.models.user import User
from mihomes.services import onboarding_service as onboarding
from mihomes.tenancy import account_context, require_user
from mihomes.web.deps import get_db, templates

router = APIRouter()

_SESSION_REASON = (
    "Onboarding steps 1-2 run before the account exists, so there is no account to "
    "authorise against and no membership to look up."
)


def _current_user(db: Session) -> User:
    return db.get(User, require_user())


def _account_for(db: Session, user: User):
    """The account this user is onboarding, or None before step 2.

    Read with a Core select on `memberships`: the ORM path is tenant-filtered and there is no
    tenant bound yet — the same bootstrap problem `auth/sessions.py` solves the same way.
    """
    from sqlalchemy import select

    from mihomes.models.account import Account
    from mihomes.models.membership import Membership

    row = db.execute(
        select(Membership.__table__.c.account_id).where(
            Membership.__table__.c.user_id == user.id,
            Membership.__table__.c.status == "active",
        )
    ).first()
    return db.get(Account, row.account_id) if row else None


@router.get("/")
@declares_session(_SESSION_REASON)
def resume(request: Request, db: Session = Depends(get_db)):
    """Land the user wherever they left off (A17)."""
    user = _current_user(db)
    account = _account_for(db, user)

    if account is None:
        return templates.TemplateResponse(
            request,
            "onboarding/account.html",
            {"page": "onboarding", "suggested_name": onboarding.suggested_account_name(user)},
        )

    step = onboarding.current_step(db, account.id)
    if step == onboarding.STEP_DASHBOARD:
        return RedirectResponse("/", status_code=303)

    template = {
        onboarding.STEP_ADD_HOME: "onboarding/property.html",
        onboarding.STEP_SPACES: "onboarding/spaces.html",
        onboarding.STEP_INVITE: "onboarding/invite.html",
    }[step]
    return templates.TemplateResponse(
        request, template, {"page": "onboarding", "account": account, "step": step}
    )


@router.post("/account")
@declares_session(_SESSION_REASON)
def create_account(
    request: Request,
    name: str = Form(""),
    account_type: str = Form("household"),
    db: Session = Depends(get_db),
):
    """Step 2 — the account plus its owner membership, in one transaction."""
    user = _current_user(db)
    if _account_for(db, user) is not None:
        # Idempotent: a double-submit must not mint a second account, and SPEC-002 D4's partial
        # unique index would refuse the second owner anyway — better to redirect than to 500.
        return RedirectResponse("/onboarding/", status_code=303)

    account = onboarding.create_account_step(db, user, name, account_type)

    # **Bind this session to the account it just created**, or the wizard finishes and `/` 403s
    # with "No account selected" — `create_session` left `current_account_id` NULL and nothing
    # since has set it.
    #
    # `establish_session` now binds at sign-in, but that cannot help here: this account did not
    # exist when the session was minted. This is the second half of the same hole, and it is the
    # one a brand-new user hits, because signing up and onboarding happen in one sitting.
    current = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    if current is not None:
        set_current_account(db, current.session_id, account.id)

    return RedirectResponse("/onboarding/", status_code=303)


@router.post("/property")
@declares_session(_SESSION_REASON)
def add_property(
    request: Request,
    name: str = Form(...),
    address: str = Form(""),
    db: Session = Depends(get_db),
):
    """Step 3 — *"require only the home name"*; address and type are editable later."""
    from mihomes.services.property import create_property

    user = _current_user(db)
    account = _account_for(db, user)
    if account is None:
        return RedirectResponse("/onboarding/", status_code=303)

    with account_context(account.id, user.id):
        create_property(db, name, address=address or None)
    onboarding.complete_step(db, account.id, onboarding.STEP_ADD_HOME)
    return RedirectResponse("/onboarding/", status_code=303)


@router.post("/skip")
@declares_session(_SESSION_REASON)
def skip_step(request: Request, step: int = Form(...), db: Session = Depends(get_db)):
    """A18 — skipping is a **first-class path**, not a way out of a dead end.

    Recorded as *completed* so the wizard does not ask again: `current_step` cannot otherwise
    distinguish "not yet asked" from "asked and declined", and would re-prompt on every sign-in.
    """
    user = _current_user(db)
    account = _account_for(db, user)
    if account is None:
        return RedirectResponse("/onboarding/", status_code=303)

    if step in (onboarding.STEP_SPACES, onboarding.STEP_INVITE):
        onboarding.complete_step(db, account.id, step)
    return RedirectResponse("/onboarding/", status_code=303)


@router.post("/finish")
@declares_session(_SESSION_REASON)
def finish(request: Request, db: Session = Depends(get_db)):
    """Step 6 — land on the dashboard. Billing never blocks this (`ONBOARDING:143`)."""
    user = _current_user(db)
    account = _account_for(db, user)
    if account is not None:
        onboarding.finish(db, account.id)
        _select_account(db, request, account.id, user.id)
    return RedirectResponse("/", status_code=303)


def _select_account(db: Session, request: Request, account_id: uuid.UUID, user_id: uuid.UUID):
    """Point the session at the account onboarding just created.

    Without this the user finishes the wizard and lands on a dashboard with no account selected —
    the session still carries `current_account_id = NULL` from before step 2 existed.
    """
    from mihomes.auth.sessions import SESSION_COOKIE, lookup_session
    from mihomes.services.account_switcher import switch_account

    auth = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    if auth is not None:
        switch_account(db, auth.session_id, user_id, account_id)
