"""Staff PTO service — request creation, approval, denial, and balance tracking."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mihomes.models.staff import Staff
from mihomes.models.staff_pto import PTOStatus, StaffPTORequest
from mihomes.models.task import Task, TaskStatus
from mihomes.services.slug import get_by_id, resolve_identifier

_log = logging.getLogger("mihomes.staff_pto")


def _coverage_warning(session: Session, staff_id: int, dates: list[str]) -> str | None:
    """Return a warning string if the staff member has tasks during the requested dates."""
    from datetime import date as date_cls
    date_objs = []
    for d in dates:
        try:
            date_objs.append(date_cls.fromisoformat(d))
        except ValueError:
            pass
    if not date_objs:
        return None

    tasks = (
        session.query(Task)
        .filter(
            Task.assignee_id == staff_id,
            Task.due_date.in_(date_objs),
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )
        .all()
    )
    if not tasks:
        return None

    props = {t.property.name for t in tasks if t.property}
    return f"{len(tasks)} task(s) assigned during this period at: {', '.join(sorted(props))}"


def create_pto_request(
    session: Session,
    staff_id_or_slug: str,
    dates: list[str],
    notes: str | None = None,
) -> StaffPTORequest:
    staff = resolve_identifier(session, Staff, staff_id_or_slug)
    warning = _coverage_warning(session, staff.id, dates)
    req = StaffPTORequest(
        staff_id=staff.id,
        dates=dates,
        status=PTOStatus.PENDING,
        notes=notes,
        coverage_warning=warning,
    )
    session.add(req)
    session.flush()
    return req


def _pto_request(session: Session, request_id: uuid.UUID | str) -> StaffPTORequest:
    """Load a PTO request, treating a malformed id as "not found".

    G6.1 made these ids UUIDv7. The gateway hands us whatever the approver typed into a
    chat reply, so a non-UUID string has to raise the same ValueError as a valid-but-unknown
    id rather than reaching the driver as a bad UUID literal — callers already turn
    ValueError into "could not approve, try again".

    The coercion itself lives in `get_by_id` now: this was the first of three places that
    needed it (contract and insurance deletes were failing the same way, with
    `CannotCoerce` surfacing as a 500), so it became one helper instead of three.
    """
    req = get_by_id(session, StaffPTORequest, request_id)
    if not req:
        raise ValueError(f"PTO request #{request_id} not found")
    return req


def approve_pto(
    session: Session, request_id: uuid.UUID | str, decided_by: str = "admin"
) -> StaffPTORequest:
    req = _pto_request(session, request_id)
    if req.status != PTOStatus.PENDING:
        raise ValueError(f"PTO request #{request_id} is not pending (status: {req.status.value})")
    req.status = PTOStatus.APPROVED
    req.decided_at = datetime.now(timezone.utc)
    req.decided_by = decided_by
    session.flush()
    _sync_to_calendar(req)
    return req


def deny_pto(
    session: Session,
    request_id: uuid.UUID | str,
    decided_by: str = "admin",
    reason: str | None = None,
) -> StaffPTORequest:
    req = _pto_request(session, request_id)
    if req.status != PTOStatus.PENDING:
        raise ValueError(f"PTO request #{request_id} is not pending (status: {req.status.value})")
    req.status = PTOStatus.DENIED
    req.decided_at = datetime.now(timezone.utc)
    req.decided_by = decided_by
    if reason:
        req.notes = (req.notes + f"\nDenied: {reason}") if req.notes else f"Denied: {reason}"
    session.flush()
    return req


def list_pto_requests(
    session: Session,
    staff_id_or_slug: str | None = None,
    status: PTOStatus | None = None,
) -> list[StaffPTORequest]:
    query = session.query(StaffPTORequest)
    if staff_id_or_slug:
        staff = resolve_identifier(session, Staff, staff_id_or_slug)
        query = query.filter(StaffPTORequest.staff_id == staff.id)
    if status:
        query = query.filter(StaffPTORequest.status == status)
    return query.order_by(StaffPTORequest.created_at.desc()).all()


def get_pto_balance(session: Session, staff_id_or_slug: str) -> dict:
    """Return days used and pending for the current calendar year."""
    from datetime import date
    staff = resolve_identifier(session, Staff, staff_id_or_slug)
    year = date.today().year
    year_str = str(year)

    requests = (
        session.query(StaffPTORequest)
        .filter(StaffPTORequest.staff_id == staff.id)
        .all()
    )

    approved_days, pending_days = 0, 0
    for req in requests:
        days_this_year = [d for d in (req.dates or []) if d.startswith(year_str)]
        if req.status == PTOStatus.APPROVED:
            approved_days += len(days_this_year)
        elif req.status == PTOStatus.PENDING:
            pending_days += len(days_this_year)

    return {
        "staff": staff,
        "year": year,
        "approved_days": approved_days,
        "pending_days": pending_days,
        "requests": requests,
    }


def _sync_to_calendar(req: StaffPTORequest) -> None:
    """Push approved PTO to Google Calendar as an all-day event."""
    import logging
    log = logging.getLogger("mihomes.staff_pto")
    try:
        from mihomes.services.calendar_sync import _get_provider, is_google_auth_available
        if not is_google_auth_available():
            return
        from datetime import date, datetime, timezone
        provider = _get_provider()
        dates = sorted(req.dates or [])
        if not dates:
            return
        staff_name = req.staff.name if req.staff else "Staff"
        title = f"[MiHomes] PTO — {staff_name}"
        for d in dates:
            try:
                dt = date.fromisoformat(d)
                start = datetime(dt.year, dt.month, dt.day, 0, 0, tzinfo=timezone.utc)
                end = datetime(dt.year, dt.month, dt.day, 23, 59, tzinfo=timezone.utc)
                provider.create_event(title=title, start=start, end=end)
            except Exception as e:
                log.warning("Failed to sync PTO date %s to calendar: %s", d, e)
    except Exception as e:
        log.warning("PTO calendar sync failed: %s", e)


def notify_approver(session: Session, req: StaffPTORequest) -> bool:
    """Notify the configured PTO approver over whichever gateway is set up.

    H35: this used to hardcode ``WhatsAppClient`` and read only the approver
    phone, so a Telegram-only install (phone unset, ``telegram.pto_approver_id``
    set) had a silently dead approval loop. A configured phone still means a
    WhatsApp install and wins; Telegram is the fallback. Failures are logged
    rather than silently swallowed.
    """
    from mihomes.services.config_service import get_config

    staff_name = req.staff.name if req.staff else "Unknown"
    dates_str = ", ".join(req.dates) if req.dates else "unknown dates"
    msg = f"🏠 PTO request from {staff_name}: {dates_str}. Reply:\nAPPROVE {req.id}\nor\nDENY {req.id}"
    if req.coverage_warning:
        msg += f"\n\n⚠️ {req.coverage_warning}"

    approver_phone = (
        get_config(session, "staff.pto_approver_phone")
        or get_config(session, "owner.whatsapp_phone")
    )
    if approver_phone:
        try:
            from mihomes.services.gateways.whatsapp.client import WhatsAppClient
            WhatsAppClient().send_message(approver_phone, msg)
            return True
        except Exception:
            _log.exception("notify_approver: WhatsApp send failed")
            return False

    approver_chat_id = get_config(session, "telegram.pto_approver_id")
    if approver_chat_id:
        try:
            from mihomes.services.gateways.telegram.responder import _get_client
            _get_client(session).send_message(str(approver_chat_id).strip(), msg)
            return True
        except Exception:
            _log.exception("notify_approver: Telegram send failed")
            return False

    return False


def notify_staff(session: Session, req: StaffPTORequest) -> bool:
    """Tell the staff member their PTO was decided, over whichever gateway is set up.

    **SPEC-006 Step 8 / A21 — this is F9's bug.** `notify_approver` above was given a
    WhatsApp→Telegram ladder under H35 because a Telegram-only install had a silently dead
    approval loop. This function was left WhatsApp-only, so on the same install the *other*
    half stayed dead: a staff member's PTO was approved or denied and **they were never told**.
    No error, no log line anybody reads — the request simply looked unanswered from their side.

    The ladder mirrors `notify_approver`'s deliberately, including its precedence: a configured
    phone means a WhatsApp install and wins; Telegram is the fallback. Two things differ, and
    both follow from *who* is being messaged rather than from style:

    * The approver is one configured person, so their id lives in `telegram.pto_approver_id`.
      A staff member is a **row**, and `Staff` has no Telegram column — so the fallback resolves
      through the chat the estate already uses. Per-staff Telegram identity is what
      `gateway_link_tokens` will eventually supply (Step 3); until a staff member is linked,
      the group is the only address we have for them.
    * The message therefore names the staff member. In a group, an unaddressed "your PTO was
      approved" is ambiguous between everyone reading it.

    Returns True if some gateway accepted the message. False means **nobody was told**, which
    is worth acting on rather than ignoring.
    """
    from mihomes.services.config_service import get_config

    staff = req.staff
    dates_str = ", ".join(req.dates) if req.dates else "your requested dates"

    if req.status == PTOStatus.APPROVED:
        detail = f"PTO for {dates_str} has been approved ✓"
    else:
        detail = f"PTO for {dates_str} has been denied."
        if req.notes:
            reason = [
                line for line in req.notes.splitlines() if line.startswith("Denied:")
            ]
            if reason:
                detail += f" {reason[-1].replace('Denied: ', '')}"

    # --- WhatsApp: a direct message, so it can address the staff member as "your" -------
    if staff and staff.whatsapp_phone:
        try:
            from mihomes.services.gateways.whatsapp.client import WhatsAppClient

            WhatsAppClient().send_message(staff.whatsapp_phone, f"🏠 Your {detail}")
            return True
        except Exception:
            # Fall through to Telegram rather than returning False: a WhatsApp install whose
            # bridge is down should still reach a staff member if a chat is configured. H35's
            # ladder returns False here, correctly — the approver has no second address.
            _log.exception("notify_staff: WhatsApp send failed, trying Telegram")

    # --- Telegram: the estate's chat, so the message must name whose PTO it is ----------
    chat_id = get_config(session, "telegram.pto_approver_id") or get_config(
        session, "telegram.staff_chat_id"
    )
    if chat_id:
        try:
            from mihomes.services.gateways.telegram.responder import _get_client

            name = staff.name if staff else "A staff member"
            _get_client(session).send_message(str(chat_id).strip(), f"🏠 {name}: {detail}")
            return True
        except Exception:
            _log.exception("notify_staff: Telegram send failed")
            return False

    # Nobody was told. Logged at warning rather than returned silently: this is the F9
    # condition itself, and an operator seeing it can configure a gateway.
    _log.warning(
        "notify_staff: no gateway configured — staff member was NOT told their PTO was decided "
        "(request %s)",
        req.id,
    )
    return False
