"""Budget & finance routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.services import budget as budget_svc
from mihomes.services import property as prop_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


@router.get("/")
def budget_overview(request: Request, db: Session = Depends(get_db)):
    properties = prop_svc.list_properties(db)
    reports = budget_svc.get_budget_report(db)
    transactions = budget_svc.list_transactions(db)
    return templates.TemplateResponse(
        "budget.html",
        {
            "request": request,
            "page": "budget",
            "properties": properties,
            "reports": reports,
            "transactions": transactions[:20],
        },
    )


@router.post("/transactions", response_class=HTMLResponse)
def add_transaction(
    request: Request,
    property_id: int = Form(...),
    description: str = Form(...),
    amount: float = Form(...),
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    budget_svc.add_transaction(
        db,
        property_id=property_id,
        description=description,
        amount=amount,
        category=category or None,
    )
    transactions = budget_svc.list_transactions(db)
    return templates.TemplateResponse(
        "partials/transaction_list.html",
        {"request": request, "transactions": transactions[:20]},
    )
