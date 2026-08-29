"""Privacy routes — data export and account deletion (SPEC-005 §5.4, D8).

**Owner-only, via row 16's `account.delete`.** Not a new matrix key: the row already exists,
already reads `(owner=ALLOW, admin=DENY, staff=DENY)`, and already means "may end this account's
relationship with the product". Exporting every row an account holds is the same authority — an
admin who could download the whole estate but not delete it is a distinction without a security
difference, since the export is what makes the data portable in the first place.

## The export is assembled, never streamed from a file

`build_export` walks the ORM under the scoped session (D14). Two functions in this tree already
look like "export" and neither may be routed here: `csv_io.export_csv` covers 5 of 28 model
modules with no account filter (F4), and `backup.create_backup` tars the whole database and media
directory (F5) — an operator tool that under multitenancy would be a total cross-tenant breach
wearing the name of a feature (N4).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from mihomes.authz.declare import Access, declares
from mihomes.services.privacy import build_export, cancel_deletion, request_deletion
from mihomes.web.deps import get_db, require_authenticated

logger = logging.getLogger(__name__)

router = APIRouter()

#: Row 16. Owner-only — see the module docstring on why this is not a new key.
PRIVACY_ACTION = "account.delete"


@router.get("/privacy/export")
@declares(PRIVACY_ACTION, Access.ACCOUNT)
def export_account(
    request: Request,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Download every row this account owns, as JSON.

    A direct download rather than a background job with an emailed link. The bundle is one
    account's rows, assembled in a single scoped pass — for an estate-sized account that is
    tens of thousands of rows, not millions, and the simpler shape has no queue to drain, no
    link to expire, and no window in which a half-built file is downloadable.

    `send_export_ready` (§5.2) exists for the day that stops being true; §7 keeps the async
    variant out of this phase deliberately.
    """
    bundle = build_export(db, principal.account_id)

    logger.info(
        "export downloaded: account=%s rows=%d",
        bundle.account_id, bundle.row_count,
    )

    payload = {
        "account_id": bundle.account_id,
        "generated_at": bundle.generated_at.isoformat(),
        "tables": bundle.tables,
        "documents": bundle.documents,
    }
    return Response(
        content=json.dumps(payload, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="mihomes-export-{bundle.account_id}.json"'
            )
        },
    )


@router.post("/privacy/delete")
@declares(PRIVACY_ACTION, Access.ACCOUNT)
def request_account_deletion(
    request: Request,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Start the deletion clock. **Deletes nothing** (D15).

    `PRICING` §4.4 requires the export to be offered first, and the response says so rather than
    assuming the caller knows: a customer who deletes without exporting has lost data they were
    entitled to take with them, and there is no second chance to mention it.

    The grace period is O2 — open, and a config value either way. What is fixed here is the
    state machine: `requested` now, `purged` only after `purge_after`, cancellable throughout.
    """
    record = request_deletion(db, principal.account_id, principal.user_id)

    return JSONResponse(
        {
            "state": "requested",
            "requested_at": record.requested_at.isoformat(),
            "purge_after": record.purge_after.isoformat(),
            "export_first": "/privacy/export",
            "cancel": "/privacy/delete/cancel",
        }
    )


#: Row 19. Owner and admin ALLOW, staff DENY — the key already exists and already means
#: "may take this account's data out". Reused rather than adding a 21st, for the same reason
#: `PRIVACY_ACTION` reuses row 16.
#:
#: **This is the RBAC half only.** D10 keeps the two gates independent and requires both to
#: pass: `export.data` says *this role* may export, `audit.export` says *this plan* includes
#: the audit log. An owner on Pro passes the first and fails the second, which is the whole
#: point of the Estate tier.
AUDIT_EXPORT_ACTION = "export.data"


@router.get("/privacy/audit-export")
@declares(AUDIT_EXPORT_ACTION, Access.ACCOUNT)
def export_audit_log(
    request: Request,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Download this account's audit log. **Estate-only** (SPEC-005 Step 14, A34).

    The surface half of G12's gate: A12 proved the service denies, this proves a user who
    reaches for it is told *what to do about it*. `PRICING` rule 4 — every `Denied` names the
    plan that would allow it — is a statement about this response, not about the decision
    object, and a 402 with no `upgrade_target` is where a paywall becomes a dead end.

    **402, not 403.** 403 is the role gate's answer and is already spoken by `@declares` above;
    reusing it here would make "your plan does not include this" indistinguishable from "your
    role may not do this" — two different problems with two different fixes, one of which the
    customer can solve by paying and the other they cannot.
    """
    from mihomes.entitlements.service import EntitlementDenied
    from mihomes.models.account import Account
    from mihomes.services.audit import export as export_audit

    account = db.get(Account, principal.account_id)

    try:
        entries = export_audit(db, account=account)
    except EntitlementDenied as denied:
        # The decision travels intact from `can()` to here — which is why G12 raises
        # `EntitlementDenied` rather than a bare `PermissionError`. A message string would have
        # arrived with nothing to offer.
        logger.info(
            "audit export denied: account=%s upgrade_target=%s",
            principal.account_id, denied.upgrade_target,
        )
        return JSONResponse(
            status_code=402,
            content={
                "error": "plan_required",
                "reason": str(denied),
                "upgrade_target": denied.upgrade_target,
            },
        )

    payload = [
        {
            "timestamp": entry.timestamp.isoformat(),
            "entity_type": entry.entity_type,
            "entity_id": str(entry.entity_id),
            "action": entry.action,
            "actor": entry.actor,
            "changes": entry.changes,
        }
        for entry in entries
    ]

    logger.info("audit export downloaded: account=%s rows=%d",
                principal.account_id, len(payload))

    return Response(
        content=json.dumps(payload, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="mihomes-audit-{principal.account_id}.json"'
            )
        },
    )


@router.post("/privacy/delete/cancel")
@declares(PRIVACY_ACTION, Access.ACCOUNT)
def cancel_account_deletion(
    request: Request,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Stop a pending deletion (A9). Idempotent; refuses once the purge has run."""
    cancelled = cancel_deletion(db, principal.account_id)

    return JSONResponse(
        {
            "state": "cancelled" if cancelled else "nothing_to_cancel",
            "cancelled": cancelled,
        }
    )
