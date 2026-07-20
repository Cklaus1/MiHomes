"""Staff PTO service — request creation, approval, denial, and balance tracking."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from mihomes.models.staff_pto import PTOStatus, StaffPTORequest
from mihomes.models.staff import Staff
from mihomes.models.task import Task, TaskStatus
from mihomes.services.slug import resolve_identifier


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


def approve_pto(session: Session, request_id: int, decided_by: str = "admin") -> StaffPTORequest:
    req = session.get(StaffPTORequest, request_id)
    if not req:
        raise ValueError(f"PTO request #{request_id} not found")
    req.status = PTOStatus.APPROVED
    req.decided_at = datetime.now(timezone.utc)
    req.decided_by = decided_by
    session.flush()
    _sync_to_calendar(req)
    return req


def deny_pto(session: Session, request_id: int, decided_by: str = "admin", reason: str | None = None) -> StaffPTORequest:
    req = session.get(StaffPTORequest, request_id)
    if not req:
        raise ValueError(f"PTO request #{request_id} not found")
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
        from mihomes.services.calendar_sync import is_google_auth_available, _get_provider
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
    """Send a WhatsApp message to the configured PTO approver."""
    try:
        from mihomes.services.config_service import get_config
        approver_phone = (
            get_config(session, "staff.pto_approver_phone")
            or get_config(session, "owner.whatsapp_phone")
        )
        if not approver_phone:
            return False

        from mihomes.services.gateways.whatsapp.client import WhatsAppClient
        client = WhatsAppClient()
        staff_name = req.staff.name if req.staff else "Unknown"
        dates_str = ", ".join(req.dates) if req.dates else "unknown dates"
        msg = f"🏠 PTO request from {staff_name}: {dates_str}. Reply:\nAPPROVE {req.id}\nor\nDENY {req.id}"
        if req.coverage_warning:
            msg += f"\n\n⚠️ {req.coverage_warning}"
        client.send_message(approver_phone, msg)
        return True
    except Exception:
        return False


def notify_staff(session: Session, req: StaffPTORequest) -> bool:
    """Send a WhatsApp message back to the staff member with the decision."""
    try:
        staff = req.staff
        if not staff or not staff.whatsapp_phone:
            return False
        from mihomes.services.gateways.whatsapp.client import WhatsAppClient
        client = WhatsAppClient()
        dates_str = ", ".join(req.dates) if req.dates else "your requested dates"
        if req.status == PTOStatus.APPROVED:
            msg = f"🏠 Your PTO request for {dates_str} has been approved ✓"
        else:
            msg = f"🏠 Your PTO request for {dates_str} has been denied."
            if req.notes:
                reason = [l for l in req.notes.splitlines() if l.startswith("Denied:")]
                if reason:
                    msg += f" {reason[-1].replace('Denied: ', '')}"
        client.send_message(staff.whatsapp_phone, msg)
        return True
    except Exception:
        return False
