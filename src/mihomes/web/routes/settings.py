"""Per-tenant settings UI — SPEC-003 §6 Step 15, F7 (A27); reworked 2026-10-15.

F7: SPEC-002 §7:614 assigns this here outright, and it matters more than it looks. **SPEC-002 D1
drops local SQLite mode and makes the CLI an operator tool** — citing `web/routes/ai.py:47` as its
own justification. With no user-facing CLI and no config UI, *"a tenant cannot configure anything
at all."*

**Tenant preferences, not backend configuration (2026-10-15).** The page used to be a raw
key/value editor over every `configurations` row, and its form wrote any key — so an owner could
repoint `ai.provider`, read the Telegram poll state, or store credentials. It now offers only the
user-facing keys in `config_service.USER_SETTING_KEYS` (currency, time zone, weather location,
notification preferences), each validated. AI, retention, calendar and gateway configuration are
the operator's, set with `mihomes config set`; credentials are no longer written from the web at
all, so the encryption refusal this route used to carry has nothing left to guard.

Messenger connections live on `/settings/connections`, the one settings page every role can open.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.services import config_service
from mihomes.services.account_switcher import available_accounts
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

#: Editing **your own** profile and messenger links, and the reason this is not
#: `_SETTINGS_ACTION`.
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
        # Only the user-facing keys (`config_service.USER_SETTING_KEYS`). Backend configuration —
        # AI provider and model, retention, Telegram poll state — is never rendered here.
        "settings": config_service.get_user_settings(db),
        "currencies": config_service.CURRENCIES,
        "timezones": config_service.timezone_choices(),
        "notification_channels": config_service.NOTIFICATION_CHANNELS,
        "notification_events": config_service.NOTIFICATION_EVENTS,
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
        # Owner and admin see their own Messenger connections here too (settings/_personal.html).
        "telegram_links": _own_telegram_links(db, principal),
        "sign_in": _sign_in_context(db, user),
        # The Staff invite option is shown disabled with a Pro badge when the plan lacks it,
        # rather than offered and then refused by the paywall after the form is filled in.
        "staff_invites_entitled": _staff_invites_entitled(account),
        # The account switcher, moved here from the retired /team page (A24: absent below two).
        "accounts": available_accounts(db, principal.user_id),
        "current_account_id": principal.account_id,
        "pending_deletion": (
            _pending_deletion(db, principal.account_id) if principal.role == "owner" else None
        ),
    }
    context.update(extra)
    return context


#: Success messages for `?notice=<key>` after a redirect. A fixed map, never the query text
#: itself, so a crafted link cannot put words on the page.
_NOTICES = {
    "saved": "Settings saved",
    "account_saved": "Account name saved",
    "profile_saved": "Profile saved",
    "role_changed": "Role changed",
    "access_saved": "Property access saved",
    "member_removed": "Access removed",
    "invite_revoked": "Invitation revoked",
    "telegram_unlinked": "Telegram unlinked",
    "deletion_requested": "Account deletion scheduled",
    "deletion_cancelled": "Account deletion cancelled",
}


def _notice(request: Request) -> str | None:
    key = request.query_params.get("notice") or (
        "saved" if request.query_params.get("saved") == "1" else ""
    )
    return _NOTICES.get(key)


def _staff_invites_entitled(account) -> bool:
    from mihomes.entitlements import Allowed, can

    return account is not None and isinstance(can(account, "invite.staff"), Allowed)


def _pending_deletion(db: Session, account_id):
    """The account's live deletion request — not cancelled, not purged — or None."""
    from sqlalchemy import select

    from mihomes.models.account_deletion import AccountDeletionRequest

    return db.execute(
        select(AccountDeletionRequest)
        .where(
            AccountDeletionRequest.account_id == account_id,
            AccountDeletionRequest.cancelled_at.is_(None),
            AccountDeletionRequest.purged_at.is_(None),
        )
        .order_by(AccountDeletionRequest.requested_at)
    ).scalars().first()


def _sign_in_context(db: Session, user, request: Request | None = None, *,
                     notice: str | None = None, error: str | None = None) -> dict:
    """What `settings/_personal.html`'s "Sign-in methods" section needs.

    `recent` decides whether a Google-only user sees the add-password form or the "Confirm with
    Google" button. Without the request (a refusal re-render elsewhere) it is False, which only
    ever costs an extra confirmation, never skips one.
    """
    from mihomes.auth.password_identity import MIN_PASSWORD_LENGTH
    from mihomes.auth.sessions import SESSION_COOKIE
    from mihomes.auth.sign_in_methods import NOTICES, message_for, signed_in_recently

    email = user.email if user is not None else ""
    raw = request.cookies.get(SESSION_COOKIE) if request is not None else None
    if request is not None and error is None:
        error = message_for(request.query_params.get("signin_error"), email)
    if request is not None and notice is None:
        notice = NOTICES.get(request.query_params.get("signin", ""))
    return {
        "has_password": user is not None and user.password_hash is not None,
        "has_google": user is not None and user.google_sub is not None,
        "password_set_at": getattr(user, "password_set_at", None),
        "recent": signed_in_recently(db, raw),
        "min_length": MIN_PASSWORD_LENGTH,
        "notice": notice,
        "error": error,
    }


def _own_telegram_links(db: Session, principal):
    """The signed-in member's Telegram links — never anyone else's (R2)."""
    from sqlalchemy import select

    from mihomes.models.telegram_link import TelegramLink

    return db.execute(
        select(TelegramLink).where(TelegramLink.membership_id == principal.membership_id)
    ).scalars().all()


@router.get("/settings")
@declares(_PROFILE_ACTION, Access.ACCOUNT)
def index(request: Request, principal=require_authenticated(),
          db: Session = Depends(get_db)):
    """A27 — the settings page, for owner and admin.

    Declared as `gateway.link_self` (every role) rather than `member.manage`, so that staff
    following the sidebar's Settings link are **redirected** to their own page instead of
    hitting a 403 — the link is shown to everyone. The owner/admin gate is then this check;
    every write route on this page still declares `member.manage`, so a staff member who
    reached a form still could not submit it.
    """
    if principal.role not in ("owner", "admin"):
        return RedirectResponse("/settings/connections", status_code=303)
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _page_context(
            db, principal,
            notice=_notice(request),
            sign_in=_sign_in_context(db, db.get(_user_model(), principal.user_id), request),
        ),
    )


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
        if principal.role not in ("owner", "admin"):
            return templates.TemplateResponse(
                request,
                "settings/connections.html",
                _connections_context(db, principal, profile_error=str(exc)),
                status_code=400,
            )
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _page_context(db, principal, profile_error=str(exc)),
            status_code=400,
        )

    return RedirectResponse(_home_for(principal) + "?notice=profile_saved", status_code=303)


def _home_for(principal) -> str:
    """The settings page this role lands on: the full page for owner/admin, connections for staff."""
    return "/settings" if principal.role in ("owner", "admin") else "/settings/connections"


# ── Messenger connections — every role, own membership only ────────────────────
#
# `gateway.link_self` (row 20, rule R2): allowed to everyone, narrowed to self by the mechanism.
# The mechanism here is the same as `/settings/profile`'s — every route reads
# `principal.membership_id` and accepts no membership id from the request, so there is no
# version of these calls that links or unlinks somebody else.


def _connections_context(db: Session, principal, **extra):
    from mihomes.models.account import Account
    from mihomes.models.user import User

    user = db.get(User, principal.user_id)
    context = {
        "page": "settings",
        "account": db.get(Account, principal.account_id),
        "user": user,
        "role": principal.role,
        "telegram_links": _own_telegram_links(db, principal),
        "can_change_email": user is not None and user.password_hash is not None,
        "sign_in": _sign_in_context(db, user),
        # Staff with a second account switch here too; the switcher used to live on /team,
        # which staff could not open.
        "accounts": available_accounts(db, principal.user_id),
        "current_account_id": principal.account_id,
    }
    context.update(extra)
    return context


@router.get("/settings/connections")
@declares(_PROFILE_ACTION, Access.ACCOUNT)
def connections(request: Request, principal=require_authenticated(),
                db: Session = Depends(get_db)):
    """Messenger connections and profile — the settings page staff can open.

    Owner and admin have the same two sections on `/settings`, so they are sent there.
    """
    if principal.role in ("owner", "admin"):
        return RedirectResponse("/settings#connections", status_code=303)
    return templates.TemplateResponse(
        request, "settings/connections.html",
        _connections_context(
            db, principal,
            notice=_notice(request),
            sign_in=_sign_in_context(db, db.get(_user_model(), principal.user_id), request),
        ),
    )


@router.post("/settings/connections/telegram/code")
@declares(_PROFILE_ACTION, Access.ACCOUNT)
def telegram_link_code(request: Request, principal=require_authenticated(),
                       db: Session = Depends(get_db)):
    """Issue a one-time Telegram link code for **your own** membership and show it once.

    The code is minted against `principal.membership_id`, so redeeming it can only ever bind a
    chat to the person who asked for it (R2). Only its hash is stored (`linking.py`, A4) — this
    response is the one place the raw code exists, which is why it is rendered rather than
    redirected.
    """
    from mihomes.services.gateways.linking import DEFAULT_TTL_MINUTES, issue_link_token

    code = issue_link_token(
        db, principal.account_id, principal.membership_id, gateway="telegram"
    )
    shown = {"link_code": code, "link_ttl_minutes": DEFAULT_TTL_MINUTES}
    if principal.role in ("owner", "admin"):
        return templates.TemplateResponse(
            request, "settings/index.html", _page_context(db, principal, **shown)
        )
    return templates.TemplateResponse(
        request, "settings/connections.html", _connections_context(db, principal, **shown)
    )


@router.post("/settings/connections/telegram/{link_id}/unlink")
@declares(_PROFILE_ACTION, Access.ACCOUNT)
def telegram_unlink(link_id: uuid.UUID, principal=require_authenticated(),
                    db: Session = Depends(get_db)):
    """Remove one of **your own** Telegram links.

    A link that belongs to another member, or does not exist, is the same 404 — the reasoning
    `_member_in_account` gives: the pair of responses would otherwise confirm which ids are real.
    """
    from mihomes.models.telegram_link import TelegramLink

    link = db.get(TelegramLink, link_id)
    if link is None or link.membership_id != principal.membership_id:
        raise HTTPException(status_code=404)
    db.delete(link)
    db.flush()
    return RedirectResponse(
        _home_for(principal) + "?notice=telegram_unlinked#connections", status_code=303
    )


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

    return RedirectResponse("/settings?notice=account_saved#account", status_code=303)


@router.post("/settings")
@declares(_SETTINGS_ACTION, Access.ACCOUNT)
async def update(
    request: Request,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Save the Preferences or Notifications form. **Only `USER_SETTING_KEYS` are writable.**

    This route used to take a free-form `key`/`value` pair and write it, which let an owner
    repoint `ai.provider` or invent keys. Backend configuration now belongs to the operator
    (`mihomes config set`); a field outside the allowlist is refused with 400, never ignored, so
    a crafted request learns nothing by being half-applied.

    Every field is validated before any is written, so one bad value leaves the others unsaved
    rather than half the form applied. `notifications.events` arrives as repeated checkboxes and
    is joined here; an unticked set is posted as the `_events_present` marker, which is how "no
    events" is told apart from "this form had no events field".
    """
    form = await request.form()
    submitted: dict[str, str] = {}
    for key in form.keys():
        if key in ("csrf_token", "notifications.events", "_events_present"):
            continue
        submitted[key] = str(form.get(key) or "")
    if "_events_present" in form:
        submitted["notifications.events"] = ",".join(
            str(v) for v in form.getlist("notifications.events")
        )

    if not submitted:
        return RedirectResponse("/settings", status_code=303)

    try:
        cleaned = {
            key: config_service.validate_user_setting(key, value)
            for key, value in submitted.items()
        }
    except config_service.SettingError as exc:
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _page_context(db, principal, error=str(exc)),
            status_code=400,
        )

    for key, value in cleaned.items():
        config_service.set_config(db, key, value)
    return RedirectResponse("/settings?notice=saved", status_code=303)


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


def _member_error(request: Request, db: Session, principal, message: str, status: int = 400,
                  **extra):
    """Re-render settings carrying a members-section error.

    A separate slot from `error` and `profile_error`, for the reason the template already gives
    for having two: a refusal about someone's role, shown above the estate-name field, reads as
    if the rename failed.
    """
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _page_context(db, principal, members_error=message, **extra),
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
        # What they typed comes back with it, so fixing one field is not retyping three.
        return _member_error(
            request, db, principal, str(exc),
            invite_form={"email": email, "role": role, "property_ids": property_ids},
        )

    link = f"{str(request.base_url).rstrip('/')}/invite/{raw_token}"
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _page_context(db, principal, invite_link=link, invite_email=invite.email,
                      notice="Invitation created"),
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

    return RedirectResponse("/settings?notice=role_changed#members", status_code=303)


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

    return RedirectResponse("/settings?notice=access_saved#members", status_code=303)


@router.post("/settings/members/{membership_id}/playbooks")
@declares(_SETTINGS_ACTION, Access.ACCOUNT)
def set_member_playbook_editor(
    request: Request,
    membership_id: uuid.UUID,
    enabled: str = Form(""),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Let a staff member create and edit playbooks without making them an admin.

    A checkbox: present ("on") grants, absent withdraws. `member.manage`, so only owners and
    admins reach it — the same people who set a staff member's properties.
    """
    from mihomes.services import membership_service

    target = _member_in_account(db, principal, membership_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        membership_service.set_playbook_editor(db, target, enabled == "on")
        db.commit()
    except membership_service.MembershipError as exc:
        return _member_error(request, db, principal, str(exc))

    return RedirectResponse("/settings?notice=access_saved#members", status_code=303)


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

    return RedirectResponse("/settings?notice=member_removed#members", status_code=303)


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

    return RedirectResponse("/settings?notice=invite_revoked#members", status_code=303)


# ── Sign-in methods — every role, own user only (2026-10-19, SPEC-010 U2) ──────
#
# Same narrowing as `/settings/profile`: the user changed is `principal.user_id`, and nothing in
# the request names another. These are credential changes, so unlike the rest of this file they
# also verify the CSRF token. Connecting Google and confirming with Google go through the OAuth
# routes in `routes/auth.py`; the rules for all three live in `auth/sign_in_methods.py`.


def _user_model():
    from mihomes.models.user import User

    return User


def _render_sign_in_refusal(request: Request, db: Session, principal, user, exc):
    sign_in = _sign_in_context(db, user, request, error=exc.message(user.email))
    if principal.role in ("owner", "admin"):
        return templates.TemplateResponse(
            request, "settings/index.html",
            _page_context(db, principal, sign_in=sign_in), status_code=400,
        )
    return templates.TemplateResponse(
        request, "settings/connections.html",
        _connections_context(db, principal, sign_in=sign_in), status_code=400,
    )


def _require_csrf(request: Request, csrf_token: str) -> None:
    from mihomes.auth.csrf import CSRF_COOKIE, tokens_match

    if not tokens_match(request.cookies.get(CSRF_COOKIE), csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


def _settings_url(request: Request, principal) -> str:
    return f"{str(request.base_url).rstrip('/')}{_home_for(principal)}"


@router.post("/settings/sign-in/password")
@declares(_PROFILE_ACTION, Access.ACCOUNT)
def add_password_route(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
    csrf_token: str = Form(""),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """A Google-only user adds a password, only within `RECENT_SIGNIN` of signing in."""
    from mihomes.auth.sessions import SESSION_COOKIE
    from mihomes.auth.sign_in_methods import (
        SignInMethodError,
        add_password,
        notify_sign_in_change,
        signed_in_recently,
    )

    _require_csrf(request, csrf_token)
    user = db.get(_user_model(), principal.user_id)
    try:
        if not signed_in_recently(db, request.cookies.get(SESSION_COOKIE)):
            raise SignInMethodError("reauth_needed")
        add_password(db, user, password, confirm)
    except SignInMethodError as exc:
        db.rollback()
        return _render_sign_in_refusal(request, db, principal, user, exc)

    db.commit()
    notify_sign_in_change(
        db, to=user.email, account_id=principal.account_id,
        change="A password was added", settings_url=_settings_url(request, principal),
    )
    return RedirectResponse(f"{_home_for(principal)}?signin=password_added#sign-in",
                            status_code=303)


@router.post("/settings/sign-in/password/change")
@declares(_PROFILE_ACTION, Access.ACCOUNT)
def change_password_route(
    request: Request,
    current_password: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    csrf_token: str = Form(""),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Change a password. Needs the current one, throttled like login; signs out other devices.

    Every session is revoked and this one re-minted, as a reset does: a password change is
    usually prompted by worry that someone else has it, and leaving their sessions alive would
    make the change cosmetic.
    """
    from mihomes.auth.ratelimit import (
        TooManyAttempts,
        check_login_attempt,
        clear_attempts,
        record_failure,
    )
    from mihomes.auth.session_flow import establish_session
    from mihomes.auth.sessions import revoke_all_sessions
    from mihomes.auth.sign_in_methods import (
        SignInMethodError,
        change_password,
        notify_sign_in_change,
    )

    _require_csrf(request, csrf_token)
    user = db.get(_user_model(), principal.user_id)
    ip = request.client.host if request.client else "unknown"
    try:
        try:
            check_login_attempt(db, email=user.email, ip=ip)
        except TooManyAttempts:
            raise SignInMethodError("too_many") from None
        change_password(db, user, current_password, password, confirm)
    except SignInMethodError as exc:
        db.rollback()
        if exc.code == "wrong_current":
            record_failure(db, email=user.email, ip=ip)
            db.commit()
        return _render_sign_in_refusal(request, db, principal, user, exc)

    clear_attempts(db, email=user.email)
    revoke_all_sessions(db, user.id)
    response = establish_session(
        db, request, user.id,
        destination=f"{_home_for(principal)}?signin=password_changed#sign-in",
        keep_account=principal.account_id,
    )
    db.commit()
    notify_sign_in_change(
        db, to=user.email, account_id=principal.account_id,
        change="Your password was changed", settings_url=_settings_url(request, principal),
    )
    return response
