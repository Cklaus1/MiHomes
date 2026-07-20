"""Calendar routes — appointments and property events in a monthly view."""

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.appointment import AppointmentType
from mihomes.services import appointment as appt_svc
from mihomes.services import contract as contract_svc
from mihomes.services import event as event_svc
from mihomes.services import property as prop_svc
from mihomes.services import vendor as vendor_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()

def _us_holidays(year: int) -> dict[date, str]:
    """Return US federal holidays for the given year."""
    import calendar as _cal

    def nth_weekday(y: int, month: int, weekday: int, n: int) -> date:
        d = date(y, month, 1)
        delta = (weekday - d.weekday()) % 7
        return d + timedelta(days=delta + (n - 1) * 7)

    def last_weekday(y: int, month: int, weekday: int) -> date:
        last = date(y, month, _cal.monthrange(y, month)[1])
        return last - timedelta(days=(last.weekday() - weekday) % 7)

    return {
        date(year, 1, 1): "New Year's Day",
        nth_weekday(year, 1, 0, 3): "MLK Day",
        nth_weekday(year, 2, 0, 3): "Presidents' Day",
        last_weekday(year, 5, 0): "Memorial Day",
        date(year, 6, 19): "Juneteenth",
        date(year, 7, 4): "Independence Day",
        nth_weekday(year, 9, 0, 1): "Labor Day",
        nth_weekday(year, 11, 3, 4): "Thanksgiving",
        date(year, 12, 25): "Christmas Day",
    }


_TYPE_COLORS = {
    "vendor_visit": ("bg-emerald-100 text-emerald-700", "bg-emerald-500"),
    "inspection": ("bg-orange-100 text-orange-700", "bg-orange-500"),
    "delivery": ("bg-purple-100 text-purple-700", "bg-purple-500"),
    "maintenance": ("bg-blue-100 text-blue-700", "bg-blue-500"),
    "other": ("bg-gray-100 text-gray-600", "bg-gray-400"),
}
_EVENT_COLOR = ("bg-teal-100 text-teal-700", "bg-teal-500")


def _build_calendar_weeks(month_date: date) -> list[list[date]]:
    """Return a 6-row × 7-col grid of dates for the given month (Mon-start)."""
    first = month_date.replace(day=1)
    # Sunday=0 offset: (weekday+1)%7 gives days to subtract back to Sunday
    start = first - timedelta(days=(first.weekday() + 1) % 7)
    weeks = []
    day = start
    for _ in range(6):
        week = []
        for _ in range(7):
            week.append(day)
            day = day + timedelta(days=1)
        weeks.append(week)
    return weeks


def _ctx(db: Session, month_date: date, property_id: int | None = None) -> dict:
    month_start = month_date.replace(day=1)
    # Last day of month
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1, day=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1, day=1)
    month_end = month_end - timedelta(days=1)

    prop_filter = str(property_id) if property_id else None
    appointments = appt_svc.list_appointments(
        db,
        property_id_or_slug=prop_filter,
        date_from=month_start,
        date_to=month_end,
    )
    events = event_svc.list_events(db, property_id_or_slug=prop_filter)
    # Filter events overlapping this month
    month_events = [
        e for e in events
        if e.event_date <= month_end and (e.end_date or e.event_date) >= month_start
    ]

    # Build day → items maps
    day_appointments: dict[date, list] = {}
    for a in appointments:
        day_appointments.setdefault(a.date, []).append(a)

    day_events: dict[date, list] = {}
    for e in month_events:
        d = e.event_date
        end_d = e.end_date or e.event_date
        while d <= end_d and d <= month_end:
            if d >= month_start:
                day_events.setdefault(d, []).append(e)
            d = d + timedelta(days=1)

    # Pull live Google Calendar events — sync times back + show external events
    day_gcal: dict[date, list] = {}
    from mihomes.services.calendar_sync import _get_provider, is_google_auth_available
    if is_google_auth_available():
        try:
            provider = _get_provider()
            start_dt = datetime(month_start.year, month_start.month, month_start.day, tzinfo=timezone.utc)
            end_dt = datetime(month_end.year, month_end.month, month_end.day, 23, 59, tzinfo=timezone.utc)
            raw = provider.list_events(start_dt, end_dt)

            # Index appointments by gcal_event_id for fast lookup
            appts_by_gcal_id = {a.gcal_event_id: a for a in appointments if a.gcal_event_id}

            for ev in raw:
                ev_id = ev.get("id", "")
                ev_start = ev.get("start")
                ev_end = ev.get("end") or ev_start
                if not ev_start:
                    continue

                if ev.get("title", "").startswith("[MiHomes]"):
                    # Our appointment — sync time back if Google's copy changed
                    if ev_id in appts_by_gcal_id:
                        appt = appts_by_gcal_id[ev_id]
                        gcal_time = ev_start.time() if isinstance(ev_start, datetime) else None
                        if gcal_time and gcal_time != appt.start_time:
                            appt_svc.update_appointment(db, appt.id, start_time=gcal_time)
                            appt.start_time = gcal_time  # update in-memory for this render
                    continue

                # External event — show on calendar
                d = ev_start.date() if isinstance(ev_start, datetime) else ev_start
                end_d = ev_end.date() if isinstance(ev_end, datetime) else ev_end
                while d <= end_d and d <= month_end:
                    if d >= month_start:
                        day_gcal.setdefault(d, []).append(ev)
                    d += timedelta(days=1)
        except Exception:
            pass

    # Build holiday map for the displayed month (spanning year boundary if needed)
    all_holidays = _us_holidays(month_start.year)
    if month_end.year != month_start.year:
        all_holidays.update(_us_holidays(month_end.year))
    day_holidays = {d: name for d, name in all_holidays.items()
                    if month_start <= d <= month_end}

    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = month_end + timedelta(days=1)

    return {
        "page": "calendar",
        "month_date": month_date,
        "month_start": month_start,
        "month_end": month_end,
        "prev_month": prev_month.strftime("%Y-%m"),
        "next_month": next_month.strftime("%Y-%m"),
        "calendar_weeks": _build_calendar_weeks(month_date),
        "day_appointments": day_appointments,
        "day_events": day_events,
        "day_gcal": day_gcal,
        "day_holidays": day_holidays,
        "month_events": month_events,
        "appointments": appointments,
        "properties": prop_svc.list_properties(db),
        "vendors": vendor_svc.list_vendors(db),
        "contracts": contract_svc.list_contracts(db),
        "appointment_types": [(t.value, t.value.replace("_", " ").title()) for t in AppointmentType],
        "type_colors": _TYPE_COLORS,
        "event_color": _EVENT_COLOR,
        "filter_property": property_id,
        "today": date.today(),
    }


@router.get("/")
def calendar_view(
    request: Request,
    month: str | None = None,
    property_id: int | None = None,
    db: Session = Depends(get_db),
):
    if month:
        try:
            month_date = date.fromisoformat(month + "-01")
        except ValueError:
            month_date = date.today().replace(day=1)
    else:
        month_date = date.today().replace(day=1)
    return templates.TemplateResponse(request, "calendar.html", _ctx(db, month_date, property_id))


@router.post("/appointments/", response_class=HTMLResponse)
def create_appointment(
    request: Request,
    title: str = Form(...),
    property_id: int = Form(...),
    appt_date: str = Form(...),
    vendor_id: str = Form(""),
    contract_id: str = Form(""),
    start_time: str = Form(""),
    appointment_type: str = Form("vendor_visit"),
    notes: str = Form(""),
    month: str = Form(""),
    filter_property_id: str = Form(""),
    db: Session = Depends(get_db),
):
    parsed_time = None
    if start_time:
        h, m = start_time.split(":")
        parsed_time = time(int(h), int(m))
    appt_svc.create_appointment(
        db,
        title=title,
        property_id_or_slug=str(property_id),
        appt_date=date.fromisoformat(appt_date),
        vendor_id=int(vendor_id) if vendor_id else None,
        contract_id=int(contract_id) if contract_id else None,
        start_time=parsed_time,
        appointment_type=appointment_type,
        notes=notes or None,
    )
    try:
        month_date = date.fromisoformat(month + "-01") if month else date.today().replace(day=1)
    except ValueError:
        month_date = date.today().replace(day=1)
    # Use the calendar's active filter, not the appointment's property
    cal_filter = int(filter_property_id) if filter_property_id else None
    return templates.TemplateResponse(request, "calendar.html", _ctx(db, month_date, cal_filter))


@router.post("/appointments/{appointment_id}/edit", response_class=HTMLResponse)
def edit_appointment(
    request: Request,
    appointment_id: int,
    title: str = Form(...),
    appt_date: str = Form(...),
    vendor_id: str = Form(""),
    contract_id: str = Form(""),
    start_time: str = Form(""),
    appointment_type: str = Form("vendor_visit"),
    notes: str = Form(""),
    month: str = Form(""),
    db: Session = Depends(get_db),
):
    parsed_time = None
    if start_time:
        h, m = start_time.split(":")
        parsed_time = time(int(h), int(m))
    kwargs = dict(
        title=title,
        date=date.fromisoformat(appt_date),
        vendor_id=int(vendor_id) if vendor_id else None,
        contract_id=int(contract_id) if contract_id else None,
        start_time=parsed_time,
        appointment_type=appointment_type,
        notes=notes or None,
    )
    appt_svc.update_appointment(db, appointment_id, **kwargs)
    try:
        month_date = date.fromisoformat(month + "-01") if month else date.today().replace(day=1)
    except ValueError:
        month_date = date.today().replace(day=1)
    return templates.TemplateResponse(request, "calendar.html", _ctx(db, month_date))


@router.post("/appointments/{appointment_id}/mark-serviced", response_class=HTMLResponse)
def mark_appointment_serviced(
    request: Request,
    appointment_id: int,
    actual_cost: str = Form(""),
    month: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        appt_svc.mark_appointment_serviced(
            db, appointment_id,
            actual_cost=float(actual_cost) if actual_cost else None,
        )
    except ValueError:
        pass
    try:
        month_date = date.fromisoformat(month + "-01") if month else date.today().replace(day=1)
    except ValueError:
        month_date = date.today().replace(day=1)
    return templates.TemplateResponse(request, "calendar.html", _ctx(db, month_date))


@router.post("/appointments/{appointment_id}/delete", response_class=HTMLResponse)
def delete_appointment(
    request: Request,
    appointment_id: int,
    month: str = Form(""),
    db: Session = Depends(get_db),
):
    appt_svc.delete_appointment(db, appointment_id)
    try:
        month_date = date.fromisoformat(month + "-01") if month else date.today().replace(day=1)
    except ValueError:
        month_date = date.today().replace(day=1)
    return templates.TemplateResponse(request, "calendar.html", _ctx(db, month_date))


@router.post("/events/{event_id}/delete", response_class=HTMLResponse)
def delete_event_from_calendar(
    request: Request,
    event_id: int,
    month: str = Form(""),
    db: Session = Depends(get_db),
):
    event_svc.delete_event(db, str(event_id))
    try:
        month_date = date.fromisoformat(month + "-01") if month else date.today().replace(day=1)
    except ValueError:
        month_date = date.today().replace(day=1)
    return templates.TemplateResponse(request, "calendar.html", _ctx(db, month_date))
