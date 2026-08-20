"""Per-tenant config UI — SPEC-003 §6 Step 15, F7 (A27), carrying O1.

F7: SPEC-002 §7:614 assigns this here outright, and it matters more than it looks. **SPEC-002 D1
drops local SQLite mode and makes the CLI an operator tool** — citing `web/routes/ai.py:47` as its
own justification. With no user-facing CLI and no config UI, *"a tenant cannot configure anything
at all."*

**O1 is open, and N11 says what to do about it rather than leaving it to judgement:** *"Do not
write secret config values to a plaintext column from a new web form until O1 is answered. The
masking half of Step 15 proceeds."* So the read path masks, and the write path **refuses secret
keys with a message naming the reason**. That refusal is the specified Phase 2 behaviour, not a
gap — the same shape as SPEC-002's `UnsupportedBackendError` on SQLite: closing a hole by
declining to open it.

**This does not make the secrets safe** (§10). They remain plaintext in `configurations.value`;
masking stops shoulder-surfing and pasted terminal output, and nothing more.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

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
    """Write a non-secret setting. **Secret keys are refused while O1 is open** (N11).

    Refusing is not a placeholder for the real implementation — it *is* the Phase 2 behaviour the
    spec asks for. Accepting the write would put a fresh credential into a plaintext column
    through a new path, multiplying the exposure F7 describes while O1's key-management question
    is still unanswered. A user who needs to set one can still do it through the operator CLI,
    where the decision to store it in plaintext is at least explicit.
    """
    if config_service.is_secret(key):
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            {
                "page": "settings",
                "configs": config_service.list_config_for_display(db),
                "error": (
                    f"{key} holds a credential, and MiHomes will not store one from this form "
                    "yet — at-rest encryption is an open decision (O1). Set it with "
                    "`mihomes config set` for now."
                ),
            },
            status_code=400,
        )

    config_service.set_config(db, key, value)
    return RedirectResponse("/settings", status_code=303)
