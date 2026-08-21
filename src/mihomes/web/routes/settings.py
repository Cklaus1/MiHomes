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


@router.get("/settings")
@declares(_SETTINGS_ACTION, Access.ACCOUNT)
def index(request: Request, principal=require_authenticated(),
          db: Session = Depends(get_db)):
    """A27 — the settings page. Staff get 403 from the enforcement dependency, not from here."""
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {"page": "settings", "configs": config_service.list_config_for_display(db)},
    )


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
            {
                "page": "settings",
                "configs": config_service.list_config_for_display(db),
                "error": (
                    f"{key} holds a credential and cannot be stored: {crypto.SECRET_KEY_ENV} is "
                    "not set, so it could only be written in plaintext. Generate a key with "
                    "`mihomes config generate-key`, put it in the environment, and try again."
                ),
            },
            status_code=400,
        )

    config_service.set_config(db, key, value)
    return RedirectResponse("/settings", status_code=303)
