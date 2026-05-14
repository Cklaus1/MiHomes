"""Recurring expenses routes."""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.recurring_expense import ExpenseFrequency
from mihomes.services import recurring as recurring_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import vendor as vendor_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _ctx(db: Session) -> dict:
    expenses = recurring_svc.list_recurring_expenses(db, active_only=False)
    return {
        "page": "recurring",
        "expenses": expenses,
        "properties": prop_svc.list_properties(db),
        "vendors": vendor_svc.list_vendors(db),
        "frequencies": [f.value for f in ExpenseFrequency],
        "notes_map": {e.id: note_svc.list_notes(db, f"recurring:{e.id}") for e in expenses},
    }


@router.get("/")
def list_recurring(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "recurring.html", _ctx(db))


@router.post("/", response_class=HTMLResponse)
def create_recurring(
    request: Request,
    name: str = Form(...),
    amount: float = Form(...),
    frequency: str = Form(...),
    property_slug: str = Form(...),
    category: str = Form("general"),
    vendor_slug: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    recurring_svc.create_recurring_expense(
        db,
        name=name,
        amount=amount,
        frequency=ExpenseFrequency(frequency),
        property_id_or_slug=property_slug,
        category=category or "general",
        start_date=date.fromisoformat(start_date) if start_date else date.today(),
        vendor_id_or_slug=vendor_slug or None,
        end_date=date.fromisoformat(end_date) if end_date else None,
        notes=notes or None,
    )
    return templates.TemplateResponse(request, "recurring.html", _ctx(db))


@router.post("/{expense_id}/edit", response_class=HTMLResponse)
def edit_recurring(
    request: Request,
    expense_id: int,
    name: str = Form(""),
    amount: str = Form(""),
    frequency: str = Form(""),
    category: str = Form(""),
    end_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    kwargs: dict = {}
    if name:
        kwargs["name"] = name
    if amount:
        kwargs["amount"] = float(amount)
    if frequency:
        kwargs["frequency"] = ExpenseFrequency(frequency)
    if category:
        kwargs["category"] = category
    if end_date:
        kwargs["end_date"] = date.fromisoformat(end_date)
    kwargs["notes"] = notes or None
    recurring_svc.update_recurring_expense(db, expense_id, **kwargs)
    return templates.TemplateResponse(request, "recurring.html", _ctx(db))


@router.post("/generate", response_class=HTMLResponse)
def generate_transactions(request: Request, db: Session = Depends(get_db)):
    recurring_svc.generate_transactions(db)
    return templates.TemplateResponse(request, "recurring.html", _ctx(db))


@router.post("/{expense_id}/notes", response_class=HTMLResponse)
def add_note(request: Request, expense_id: int, content: str = Form(...), db: Session = Depends(get_db)):
    note_svc.add_note(db, f"recurring:{expense_id}", content)
    notes = note_svc.list_notes(db, f"recurring:{expense_id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/recurring/{expense_id}/notes",
        "delete_url_prefix": f"/recurring/{expense_id}/notes",
    })


@router.delete("/{expense_id}/notes/{note_id}", response_class=HTMLResponse)
def delete_note(request: Request, expense_id: int, note_id: int, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    notes = note_svc.list_notes(db, f"recurring:{expense_id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/recurring/{expense_id}/notes",
        "delete_url_prefix": f"/recurring/{expense_id}/notes",
    })


@router.post("/{expense_id}/delete", response_class=HTMLResponse)
def delete_recurring(request: Request, expense_id: int, db: Session = Depends(get_db)):
    recurring_svc.update_recurring_expense(db, expense_id, end_date=date.today())
    return templates.TemplateResponse(request, "recurring.html", _ctx(db))
