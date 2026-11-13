"""`POST /waitlist` on the product app — waitlist mode's sign-up form (2026-11-13).

The Phase 0 landing app has the same route; this one is what the homepage posts to when
``MIHOMES_SIGNUP_MODE=waitlist`` replaces "Start free" with the waitlist form
(`auth/signup_gate.py`). It reuses `services/waitlist.signup`, so the rows are the same rows.

Two differences from the landing route, both deliberate:

* **No confirmation email.** Mail is not live yet, and a confirmation nobody receives would
  leave every row unconfirmed. Rows are stored as they arrive; housekeeping only purges
  unconfirmed rows that were actually *sent* a confirmation, so these are kept.
* **One page for every outcome** (§7-N3, unchanged): a new address, a repeat and a malformed one
  all get the same "you're on the list" page, so the form cannot be used to test which
  addresses are already on it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from mihomes.landing.ratelimit import TokenBucket, client_ip
from mihomes.web.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Public and writes rows, so it is throttled per IP — the landing app's bucket, same defaults.
_bucket = TokenBucket()
_NUM_HOMES = {"1", "2-3", "4+"}  # the form's options; anything else is stored as unanswered


@router.post("/waitlist", response_class=HTMLResponse)
def join_waitlist(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(""),
    name: str = Form(""),
    num_homes: str = Form(""),
    has_staff: str = Form(""),
    utm_campaign: str = Form(""),
    utm_source: str = Form(""),
    utm_medium: str = Form(""),
):
    # Sync on purpose: a sync route runs in the threadpool, so the database call never blocks
    # the event loop (the session-touch deadlock, 81efb95, was a sync query on the loop).
    from mihomes.landing.templates_env import render_page
    from mihomes.services import waitlist as waitlist_service

    ip = client_ip(request)
    if not _bucket.allow(ip):
        return JSONResponse(
            {"detail": "Too many requests. Please try again shortly."}, status_code=429
        )

    # Bounded to the column widths: an over-long value would be a database error (a 500), not a
    # validation message, and nothing typed here deserves one.
    utm = {"utm_campaign": utm_campaign, "utm_source": utm_source, "utm_medium": utm_medium}
    try:
        if len(email) > 320:
            raise ValueError("email too long")
        waitlist_service.add(
            db,
            email=email,
            name=name.strip()[:200] or None,
            num_homes=num_homes if num_homes in _NUM_HOMES else None,
            has_staff={"yes": True, "no": False}.get(has_staff.lower()),
            source="app",
            utm={key: value[:200] for key, value in utm.items() if value},
            signup_ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
    except ValueError:
        # Malformed address: same page, no row (N3).
        logger.info("waitlist: rejected an implausible address")

    return HTMLResponse(render_page("submitted.html", {"no_email": True}))
