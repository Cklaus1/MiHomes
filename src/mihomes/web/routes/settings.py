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

from fastapi import APIRouter, Depends, Form, Request
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
    from mihomes.models.account import Account
    from mihomes.models.user import User

    user = db.get(User, principal.user_id)
    context = {
        "page": "settings",
        "configs": config_service.list_config_for_display(db),
        "account": db.get(Account, principal.account_id),
        "user": user,
        "role": principal.role,
        # The template hides the email field entirely for a Google identity rather than
        # rendering one that always refuses — a control that cannot succeed is worse than no
        # control, because the reason is invisible until you try it.
        "can_change_email": user is not None and user.password_hash is not None,
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
