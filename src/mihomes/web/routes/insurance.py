"""Insurance routes."""

from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.insurance import InsuranceType
from mihomes.services import insurance as insurance_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _ctx(db: Session, expiring_days: int | None = None) -> dict:
    all_policies = insurance_svc.list_policies(db)
    policies = (
        insurance_svc.list_policies(db, expiring_days=expiring_days)
        if expiring_days
        else all_policies
    )
    return {
        "page": "insurance",
        "all_policies": all_policies,
        "policies": policies,
        "properties": prop_svc.list_properties(db),
        "insurance_types": [t.value for t in InsuranceType],
        "notes_map": {p.id: note_svc.list_notes(db, f"insurance:{p.id}") for p in policies},
        "filter_expiring": expiring_days,
        "today": date.today(),
    }


@router.get("/")
def list_policies(request: Request, expiring: int | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "insurance.html", _ctx(db, expiring))


@router.post("/", response_class=HTMLResponse)
def create_policy(
    request: Request,
    carrier: str = Form(...),
    insurance_type: str = Form(...),
    property_slug: str = Form(""),
    policy_number: str = Form(""),
    agent_contact: str = Form(""),
    coverage_limit: str = Form(""),
    deductible: str = Form(""),
    annual_premium: str = Form(""),
    renewal_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    insurance_svc.create_policy(
        db,
        carrier=carrier,
        insurance_type=InsuranceType(insurance_type),
        property_id_or_slug=property_slug or None,
        policy_number=policy_number or None,
        agent_contact=agent_contact or None,
        coverage_limit=float(coverage_limit) if coverage_limit else None,
        deductible=float(deductible) if deductible else None,
        annual_premium=float(annual_premium) if annual_premium else None,
        renewal_date=date.fromisoformat(renewal_date) if renewal_date else None,
        notes=notes or None,
    )
    return templates.TemplateResponse(request, "insurance.html", _ctx(db))


@router.post("/{policy_id}/edit", response_class=HTMLResponse)
def edit_policy(
    request: Request,
    policy_id: int,
    carrier: str = Form(""),
    agent_contact: str = Form(""),
    policy_number: str = Form(""),
    coverage_limit: str = Form(""),
    deductible: str = Form(""),
    annual_premium: str = Form(""),
    renewal_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    kwargs: dict = {}
    if carrier:
        kwargs["carrier"] = carrier
    if agent_contact:
        kwargs["agent_contact"] = agent_contact
    if policy_number:
        kwargs["policy_number"] = policy_number
    if coverage_limit:
        kwargs["coverage_limit"] = float(coverage_limit)
    if deductible:
        kwargs["deductible"] = float(deductible)
    if annual_premium:
        kwargs["annual_premium"] = float(annual_premium)
    if renewal_date:
        kwargs["renewal_date"] = date.fromisoformat(renewal_date)
    kwargs["notes"] = notes or None
    insurance_svc.update_policy(db, policy_id, **kwargs)
    return templates.TemplateResponse(request, "insurance.html", _ctx(db))


@router.post("/{policy_id}/notes", response_class=HTMLResponse)
def add_note(request: Request, policy_id: int, content: str = Form(...), db: Session = Depends(get_db)):
    note_svc.add_note(db, f"insurance:{policy_id}", content)
    notes = note_svc.list_notes(db, f"insurance:{policy_id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/insurance/{policy_id}/notes",
        "delete_url_prefix": f"/insurance/{policy_id}/notes",
    })


@router.delete("/{policy_id}/notes/{note_id}", response_class=HTMLResponse)
def delete_note(request: Request, policy_id: int, note_id: int, db: Session = Depends(get_db)):
    note_svc.delete_note(db, note_id)
    notes = note_svc.list_notes(db, f"insurance:{policy_id}")
    return templates.TemplateResponse(request, "partials/notes_section.html", {
        "notes": notes,
        "post_url": f"/insurance/{policy_id}/notes",
        "delete_url_prefix": f"/insurance/{policy_id}/notes",
    })


@router.post("/{policy_id}/delete", response_class=HTMLResponse)
def delete_policy(request: Request, policy_id: int, db: Session = Depends(get_db)):
    insurance_svc.delete_policy(db, policy_id)
    return templates.TemplateResponse(request, "insurance.html", _ctx(db))
