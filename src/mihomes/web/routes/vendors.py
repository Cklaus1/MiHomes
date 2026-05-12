"""Vendor routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.services import vendor as vendor_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


@router.get("/")
def list_vendors(request: Request, db: Session = Depends(get_db)):
    vendors = vendor_svc.list_vendors(db)
    return templates.TemplateResponse(
        "vendors.html",
        {"request": request, "page": "vendors", "vendors": vendors},
    )


@router.post("/", response_class=HTMLResponse)
def create_vendor(
    request: Request,
    company_name: str = Form(...),
    service_type: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    vendor_svc.create_vendor(
        db,
        company_name=company_name,
        service_type=service_type or None,
        phone=phone or None,
        email=email or None,
    )
    vendors = vendor_svc.list_vendors(db)
    return templates.TemplateResponse(
        "partials/vendor_list.html",
        {"request": request, "vendors": vendors},
    )


@router.post("/{slug}/rate", response_class=HTMLResponse)
def rate_vendor(
    request: Request,
    slug: str,
    score: int = Form(...),
    comment: str = Form(""),
    db: Session = Depends(get_db),
):
    vendor_svc.rate_vendor(db, slug, score=score, comment=comment or None)
    vendor = vendor_svc.get_vendor(db, slug)
    ratings = vendor_svc.get_vendor_ratings(db, slug)
    return templates.TemplateResponse(
        "partials/vendor_rating.html",
        {"request": request, "vendor": vendor, "ratings": ratings},
    )
