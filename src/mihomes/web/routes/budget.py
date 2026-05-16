"""Budget & finance routes."""

import json
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.budget import BudgetPeriod
from mihomes.models.recurring_expense import ExpenseFrequency
from mihomes.services import budget as budget_svc
from mihomes.services import financial_report as report_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import recurring as recurring_svc
from mihomes.services import vendor as vendor_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()

CHART_COLORS = [
    "#0ea5e9", "#10b981", "#f59e0b", "#8b5cf6",
    "#ef4444", "#06b6d4", "#84cc16", "#f97316",
    "#ec4899", "#6366f1",
]


def _make_chart(labels: list, values: list, label: str = "Spending") -> str:
    return json.dumps({
        "labels": labels,
        "datasets": [{
            "label": label,
            "data": values,
            "backgroundColor": CHART_COLORS[: len(values)],
            "borderRadius": 4,
        }],
    })


def _ctx(db: Session, active_tab: str = "overview") -> dict:
    properties = prop_svc.list_properties(db)
    today = date.today()
    period_start = today.replace(day=1)
    period_end = today
    reports = []
    for prop in properties:
        try:
            rows = budget_svc.get_budget_report(db, prop.slug, period_start, period_end)
            for row in rows:
                row["property"] = prop.name
                row["property_slug"] = prop.slug
            reports.extend(rows)
        except Exception:
            pass
    transactions = budget_svc.list_transactions(db)
    expenses = recurring_svc.list_recurring_expenses(db, active_only=False)

    # Spending analysis — current year to date
    year_start = date(today.year, 1, 1)
    comparison_data = report_svc.property_comparison(db, year_start, today)
    comparison_chart = _make_chart(
        [d["property"] for d in comparison_data],
        [d["total_spending"] for d in comparison_data],
        "Total Spending",
    ) if comparison_data else "{}"

    analysis_prop = properties[0] if properties else None
    category_data: list = []
    category_chart = "{}"
    vendor_data: list = []
    vendor_chart = "{}"
    if analysis_prop:
        try:
            category_data = report_svc.spending_by_category(db, str(analysis_prop.id), year_start, today)
            if category_data:
                category_chart = _make_chart(
                    [d["category"].replace("-", " ").title() for d in category_data],
                    [d["total"] for d in category_data],
                )
            vendor_data = report_svc.spending_by_vendor(db, str(analysis_prop.id), year_start, today)
            if vendor_data:
                vendor_chart = _make_chart(
                    [d["vendor"] for d in vendor_data],
                    [d["total"] for d in vendor_data],
                )
        except Exception:
            pass

    return {
        "page": "budget",
        "active_tab": active_tab,
        "properties": properties,
        "reports": reports,
        "transactions": transactions[:20],
        "periods": [p.value for p in BudgetPeriod],
        "expenses": expenses,
        "vendors": vendor_svc.list_vendors(db),
        "frequencies": [f.value for f in ExpenseFrequency],
        "notes_map": {e.id: note_svc.list_notes(db, f"recurring:{e.id}") for e in expenses},
        "comparison_data": comparison_data,
        "comparison_chart": comparison_chart,
        "category_data": category_data,
        "category_chart": category_chart,
        "vendor_data": vendor_data,
        "vendor_chart": vendor_chart,
        "analysis_prop": analysis_prop,
        "analysis_year": today.year,
    }


@router.get("/")
def budget_overview(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "budget.html", _ctx(db))


@router.post("/transactions", response_class=HTMLResponse)
def add_transaction(
    request: Request,
    property_id: int = Form(...),
    description: str = Form(...),
    amount: float = Form(...),
    category: str = Form("general"),
    db: Session = Depends(get_db),
):
    budget_svc.add_transaction(
        db,
        amount=amount,
        property_id_or_slug=str(property_id),
        category=category or "general",
        tx_date=date.today(),
        description=description,
    )
    transactions = budget_svc.list_transactions(db)
    return templates.TemplateResponse(request, "partials/transaction_list.html", {"transactions": transactions[:20]})


@router.post("/set", response_class=HTMLResponse)
def set_budget(
    request: Request,
    property_id: int = Form(...),
    category: str = Form(...),
    period: str = Form("monthly"),
    amount: float = Form(...),
    db: Session = Depends(get_db),
):
    today = date.today()
    if period == "annual":
        period_start = date(today.year, 1, 1)
    elif period == "quarterly":
        q_month = ((today.month - 1) // 3) * 3 + 1
        period_start = date(today.year, q_month, 1)
    else:
        period_start = today.replace(day=1)

    budget_svc.set_budget(
        db,
        property_id_or_slug=str(property_id),
        category=category,
        period=BudgetPeriod(period),
        amount=amount,
        period_start=period_start,
    )

    properties = prop_svc.list_properties(db)
    period_end = today
    reports = []
    for prop in properties:
        try:
            rows = budget_svc.get_budget_report(db, prop.slug, period_start, period_end)
            for row in rows:
                row["property"] = prop.name
            reports.extend(rows)
        except Exception:
            pass

    return templates.TemplateResponse(request, "partials/budget_report.html", {"reports": reports})
