"""Per-tenant config UI — SPEC-003 §6 Step 15, F7 (A27). O1 closed 2026-08-20.

F7: SPEC-002 §7:614 assigns this here outright, and it matters more than it looks. **SPEC-002 D1
drops local SQLite mode and makes the CLI an operator tool** — citing `web/routes/ai.py:47` as its
own justification. With no user-facing CLI and no config UI, *"a tenant cannot configure anything
at all."*

**N11's refusal has been lifted, exactly as far as the answer reaches.** Phase 2 refused *every*
secret write from this form, because O1 — at-rest encryption — was unanswered and accepting one
would have put a fresh credential into a plaintext column through a brand-new path. That was the
specified behaviour, not a gap. U1 answered O1: secrets are Fernet-encrypted in the column
(`mihomes/crypto.py`), so the form accepts them.

What survives is the **narrow** refusal: a secret is still turned away when `MIHOMES_SECRET_KEY`
is absent, because the only remaining alternative is plaintext, written through a form whose user
believes otherwise. The message names the variable, so it reads as a fixable configuration
problem. Masking is unchanged and still separate — it addresses shoulder-surfing and pasted
terminal output, which encryption does nothing about, while encryption addresses the database
disclosure that masking never could.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from mihomes import crypto
from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.services import config_service
from mihomes.web.deps import get_db, require_authenticated, templates

router = APIRouter()

#: §6 Step 15 says "owner/admin only (matrix row 2)". Row 2 is `property.edit`, which is
#: ITEM-class and about *properties* — so what the spec is citing is row 2's **grant pattern**
#: (owner ✓, admin ✓, staff ✗), not its subject. `member.manage` is the ACCOUNT-class key with
#: exactly that pattern and the closest subject: account administration.
#:
#: This is the same vocabulary gap logged at G6 — the 21 keys have no entry for "account
#: configuration" — recorded here rather than papered over.
_SETTINGS_ACTION = "member.manage"


def _page_context(db: Session, principal, **extra):
    """The settings page's context, in one place.

    Four call sites render this template (the index plus three refusal paths), and each one
    that built its own dict was a chance to omit `account`, which the estate-name field reads.
    """
    from mihomes.entitlements import limits_for
    from mihomes.models.account import Account
    from mihomes.models.user import User
    from mihomes.services import invite_service, membership_service
    from mihomes.services.property import list_properties

    user = db.get(User, principal.user_id)
    account = db.get(Account, principal.account_id)

    context = {
        "page": "settings",
        "configs": config_service.list_config_for_display(db),
        "account": account,
        "user": user,
        "role": principal.role,
        # The template hides the email field entirely for a Google identity rather than
        # rendering one that always refuses — a control that cannot succeed is worse than no
        # control, because the reason is invisible until you try it.
        "can_change_email": user is not None and user.password_hash is not None,
        # ── Members ──────────────────────────────────────────────────────────────
        "members": membership_service.list_members(db, principal.account_id),
        "pending_invites": invite_service.list_pending_for_account(db, principal.account_id),
        # One query for every staff whitelist on the page, rather than one per row inside the
        # render loop.
        "member_scopes": membership_service.property_scopes_by_membership(
            db, principal.account_id
        ),
        # Staff scope is a whitelist of properties, so the form needs the estate's properties
        # to offer. Owner and admin see everything, so this is only read for staff rows.
        "properties": list_properties(db),
        # Both halves of the seat line. `seats_used` counts pending invites as well as active
        # memberships (D6), so showing one without the other would look like an off-by-N bug.
        "seats_used": invite_service.seats_used(db, principal.account_id),
        "seat_limit": limits_for(
            getattr(account, "plan", "free"),
            getattr(account, "subscription_status", None),
        )["max_seats"],
        # The actor's own membership, so the template can mark "you" and omit the controls R1
        # and the lockout guard would refuse anyway.
        "membership_id": principal.membership_id,
    }
    context.update(extra)
    return context


@router.get("/settings")
@declares(_SETTINGS_ACTION, Access.ACCOUNT)
def index(request: Request, principal=require_authenticated(),
          db: Session = Depends(get_db)):
    """A27 — the settings page. Staff get 403 from the enforcement dependency, not from here."""
    return templates.TemplateResponse(
        request, "settings/index.html", _page_context(db, principal)
    )


#: Editing **your own** profile, and the reason this is not `_SETTINGS_ACTION`.
#:
#: The estate rename is owner/admin — it changes what every member sees. Your own display name
#: is not: a staff member must be able to fix the spelling of their own name without being
#: granted account administration. Row 20 (`gateway.link_self`) is the existing key with exactly
#: that shape — *"allowed to everyone, narrowed to self by the mechanism"* — and the mechanism
#: here is that the route reads `principal.user_id` and accepts no user id from the request, so
#: there is no version of this call that edits somebody else.
#:
#: Reusing row 20 rather than adding a 21st: the matrix is pinned at 20 rows by
#: `test_matrix_has_twenty_rows`, and a new key for "edit your own profile" would be a second
#: spelling of a grant pattern that already exists. Recorded because the name reads oddly at
#: this call site — the vocabulary gap logged at G6, not a misuse.
_PROFILE_ACTION = "gateway.link_self"


@router.post("/settings/profile")
@declares(_PROFILE_ACTION, Access.ACCOUNT)
def update_profile_route(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    current_password: str = Form(""),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Update the signed-in user's own name and email.

    **Takes no user id.** The record edited is `principal.user_id` and nothing in the request
    can change that, which is what makes a permission open to every role safe here.

    The audit row this writes is tenant-owned while `users` is not, so the account has to be
    bound around the call — the same pairing `services/account.py` documents.
    """
    from mihomes.services.profile import ProfileError, update_profile
    from mihomes.tenancy import account_context
    from mihomes.tenancy.connection import bind_account_guc

    try:
        with account_context(principal.account_id):
            bind_account_guc(db, principal.account_id)
            update_profile(
                db,
                principal.user_id,
                name=name,
                email=email,
                current_password=current_password or None,
            )
    except ProfileError as exc:
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _page_context(db, principal, profile_error=str(exc)),
            status_code=400,
        )

    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/account")
@declares(_SETTINGS_ACTION, Access.ACCOUNT)
def rename(
    request: Request,
    name: str = Form(...),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Rename the estate — the thing onboarding's *"You can change this later"* promised.

    Until now nothing in the app wrote `accounts.name`: onboarding step 2 set it once at
    creation, and the only other way to change it was raw SQL. The copy under that field was a
    promise the app could not keep.

    Same action and route class as the config form above, so it inherits the owner/admin gate
    from the enforcement dependency rather than checking a role here. A staff member never
    reaches this handler.
    """
    from mihomes.services.account import AccountNameError, rename_account

    try:
        rename_account(db, principal.account_id, name)
    except AccountNameError as exc:
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _page_context(db, principal, error=str(exc)),
            status_code=400,
        )

    return RedirectResponse("/settings", status_code=303)


@router.post("/settings")
@declares(_SETTINGS_ACTION, Access.ACCOUNT)
def update(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Write a setting. Secrets are **encrypted** now, or refused if that is impossible.

    O1 is answered (U1), so N11's blanket refusal is lifted — but only exactly as far as the
    answer reaches. A credential is accepted when it can be encrypted; when `MIHOMES_SECRET_KEY`
    is absent it is still refused, because the alternative is writing a plaintext credential
    through a form whose user believes it is protected. The refusal message names the variable, so
    it reads as a fixable configuration problem rather than as a bug.

    The narrower refusal is the point: the previous version turned away *every* secret because the
    key-management question was open. It is now open only for the operator who has not set a key.
    """
    if config_service.is_secret(key) and crypto.secret_key() is None:
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _page_context(
                db,
                principal,
                error=(
                    f"{key} holds a credential and cannot be stored: {crypto.SECRET_KEY_ENV} is "
                    "not set, so it could only be written in plaintext. Generate a key with "
                    "`mihomes config generate-key`, put it in the environment, and try again."
                ),
            ),
            status_code=400,
        )

    config_service.set_config(db, key, value)
    return RedirectResponse("/settings", status_code=303)


# ── Members ───────────────────────────────────────────────────────────────────
#
# Five routes, three permission keys, and the split is the matrix's rather than this module's:
# inviting is row 11, revoking an invitation is row 12, changing a role is row 13, and listing
# or scoping members is row 10. Declaring each one for what it actually does is what lets the
# app-level dependency refuse a staff member before any of these bodies run — which is why none
# of them checks a role itself.
#
# **`member.change_role` carries rule R1**, which the matrix cannot express as a grant because it
# depends on who the target is relative to the actor. `membership_service.change_role` calls it;
# this layer does not reimplement it, so the rule the matrix tests assert is the rule that runs.


def _member_error(request: Request, db: Session, principal, message: str, status: int = 400):
    """Re-render settings carrying a members-section error.

    A separate slot from `error` and `profile_error`, for the reason the template already gives
    for having two: a refusal about someone's role, shown above the estate-name field, reads as
    if the rename failed.
    """
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _page_context(db, principal, members_error=message),
        status_code=status,
    )


def _member_in_account(db: Session, principal, membership_id):
    """The membership this id names, **only if it belongs to the caller's account**.

    Returns `None` otherwise, and every caller turns that into the same 404. The ids arrive from
    a form, so "belongs to another estate" and "does not exist" must be indistinguishable — the
    reasoning D9 applies to property targets, for the same reason: the pair of responses would
    otherwise reveal which membership ids are real.
    """
    from mihomes.models.membership import Membership

    membership = db.get(Membership, membership_id)
    if membership is None or membership.account_id != principal.account_id:
        return None
    return membership


@router.post("/settings/members/invite")
@declares("invite.create", Access.ACCOUNT)
def invite_member(
    request: Request,
    email: str = Form(...),
    role: str = Form("staff"),
    property_ids: list[uuid.UUID] = Form(default=[]),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Invite someone, and **show the link exactly once**.

    `create_invite` returns `(invite, raw_token)` and persists only the hash, so the plaintext
    token exists for precisely this moment in the process. The route at `team.py:114` discards
    it and there is no invite email template, which makes an invitation there a row, a consumed
    seat, and a link nobody can retrieve — the feature appears to work and does nothing.

    So this renders rather than redirects, and the template shows the link with copy saying it
    will not be shown again. That is the truth about a value that is hashed at rest, and it is
    the honest version of the feature until an invite email exists to carry it.

    The audit row is tenant-owned, so the account is bound around the call — the pairing
    `services/account.py` documents and `update_profile_route` above already follows.
    """
    from mihomes.services import invite_service
    from mihomes.tenancy import account_context
    from mihomes.tenancy.connection import bind_account_guc

    try:
        with account_context(principal.account_id):
            bind_account_guc(db, principal.account_id)
            invite, raw_token = invite_service.create_invite(
                db, principal.account_id, principal.user_id, email, role, property_ids
            )
            db.commit()
    except invite_service.InviteError as exc:
        # Covers the seat cap and A21's "a staff invite needs a property" alike. Both are
        # decisions the user can act on, so they are page messages rather than bare statuses.
        return _member_error(request, db, principal, str(exc))

    link = f"{str(request.base_url).rstrip('/')}/invite/{raw_token}"
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _page_context(db, principal, invite_link=link, invite_email=invite.email),
    )


@router.post("/settings/members/{membership_id}/role")
@declares("member.change_role", Access.ACCOUNT)
def change_member_role(
    request: Request,
    membership_id: uuid.UUID,
    role: str = Form(...),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Row 13. **R1 is enforced in the service**, which is why the actor is loaded as a row.

    `change_role` takes two `Membership` objects rather than a role string, because R1 compares
    the actor's membership *id* to the target's — "nobody may change their own" cannot be
    answered from a role. Passing `principal.role` would not satisfy its signature, and a
    version that took only the role could not implement the rule at all.
    """
    from mihomes.models.membership import Membership
    from mihomes.services import membership_service
    from mihomes.tenancy import account_context
    from mihomes.tenancy.connection import bind_account_guc

    target = _member_in_account(db, principal, membership_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")

    actor = db.get(Membership, principal.membership_id)
    if actor is None:  # pragma: no cover - a resolved principal always has one
        raise HTTPException(status_code=404, detail="Not found")

    try:
        with account_context(principal.account_id):
            bind_account_guc(db, principal.account_id)
            membership_service.change_role(db, actor, target, role)
            db.commit()
    except membership_service.MembershipError as exc:
        return _member_error(request, db, principal, str(exc))

    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/members/{membership_id}/scope")
@declares(_SETTINGS_ACTION, Access.ACCOUNT)
def set_member_scope(
    request: Request,
    membership_id: uuid.UUID,
    property_ids: list[uuid.UUID] = Form(default=[]),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Which properties a staff member may see — the D5 whitelist, rewritten wholesale.

    An empty submission is refused by the service rather than accepted as "none", because zero
    scope rows means zero properties visible and the fail-closed direction of D3 means the fix
    cannot be "grant all". Unchecking every box is therefore an error the user can see, not a
    silent lockout of the person they were trying to configure.
    """
    from mihomes.services import membership_service

    target = _member_in_account(db, principal, membership_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        membership_service.set_property_scope(db, target, property_ids)
        db.commit()
    except membership_service.MembershipError as exc:
        return _member_error(request, db, principal, str(exc))

    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/members/{membership_id}/revoke")
@declares(_SETTINGS_ACTION, Access.ACCOUNT)
def revoke_member(
    request: Request,
    membership_id: uuid.UUID,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Revoke a membership — a **soft** revoke, so the account keeps their work.

    **Refuses self-revocation, and that guard lives here because nothing else has it.** R1
    covers role changes but `offboard` has no self-check, so an admin clicking revoke on their
    own row would lock themselves out of the estate in one click, with no confirmation and no
    way back in. The owner is safe only incidentally — D4 guarantees exactly one active owner,
    so `_is_last_active_owner` already refuses them — and "safe by accident" does not extend to
    admins.

    The template omits the control on your own row; this refuses it regardless, because the form
    is not the gate.
    """
    from mihomes.services import membership_service
    from mihomes.tenancy import account_context
    from mihomes.tenancy.connection import bind_account_guc

    target = _member_in_account(db, principal, membership_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")

    if target.id == principal.membership_id:
        return _member_error(
            request, db, principal,
            "you cannot remove your own access — ask another owner or admin to do it",
        )

    try:
        with account_context(principal.account_id):
            bind_account_guc(db, principal.account_id)
            membership_service.offboard(db, target)
            db.commit()
    except membership_service.MembershipError as exc:
        # A22: the last active owner. Transferring ownership first is the documented path, and
        # the service's message says so.
        return _member_error(request, db, principal, str(exc))

    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/invites/{invite_id}/revoke")
@declares("invite.modify", Access.ACCOUNT)
def revoke_pending_invite(
    request: Request,
    invite_id: uuid.UUID,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Row 12 — revoke a pending invitation, freeing its seat immediately (D6).

    Idempotent in the service, which matters because this is exactly the button that gets
    double-clicked when someone is trying to free a seat at the cap.
    """
    from mihomes.models.invite import Invite
    from mihomes.services import invite_service
    from mihomes.tenancy import account_context
    from mihomes.tenancy.connection import bind_account_guc

    invite = db.get(Invite, invite_id)
    if invite is None or invite.account_id != principal.account_id:
        raise HTTPException(status_code=404, detail="Not found")

    with account_context(principal.account_id):
        bind_account_guc(db, principal.account_id)
        invite_service.revoke_invite(db, invite)
        db.commit()

    return RedirectResponse("/settings", status_code=303)
