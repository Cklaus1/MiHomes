"""Reports route."""

import json
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from mihomes.services import financial_report as report_svc
from mihomes.services import property as prop_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()

CHART_COLORS = [
    "#0ea5e9", "#10b981", "#f59e0b", "#8b5cf6",
    "#ef4444", "#06b6d4", "#84cc16", "#f97316",
    "#ec4899", "#6366f1",
]


@router.get("/")
def reports(
    request: Request,
    tab: str = "comparison",
    property_id: str = "",
    start: str = "",
    end: str = "",
    db: Session = Depends(get_db),
):
    today = date.today()
    start_date = date.fromisoformat(start) if start else date(today.year, 1, 1)
    end_date = date.fromisoformat(end) if end else today

    properties = prop_svc.list_properties(db)

    selected_prop = None
    if property_id:
        selected_prop = next((p for p in properties if str(p.id) == property_id), None)
    if not selected_prop and properties:
        selected_prop = properties[0]

    category_data: list[dict] = []
    vendor_data: list[dict] = []
    comparison_data: list[dict] = []
    category_chart = "{}"
    vendor_chart = "{}"
    comparison_chart = "{}"

    if tab == "category" and selected_prop:
        category_data = report_svc.spending_by_category(
            db, str(selected_prop.id), start_date, end_date,
        )
        if category_data:
            labels = [d["category"].replace("-", " ").title() for d in category_data]
            values = [d["total"] for d in category_data]
            category_chart = json.dumps({
                "labels": labels,
                "datasets": [{
                    "label": "Spending",
                    "data": values,
                    "backgroundColor": CHART_COLORS[:len(values)],
                    "borderRadius": 4,
                }],
            })

    elif tab == "vendor" and selected_prop:
        vendor_data = report_svc.spending_by_vendor(
            db, str(selected_prop.id), start_date, end_date,
        )
        if vendor_data:
            labels = [d["vendor"] for d in vendor_data]
            values = [d["total"] for d in vendor_data]
            vendor_chart = json.dumps({
                "labels": labels,
                "datasets": [{
                    "label": "Spending",
                    "data": values,
                    "backgroundColor": CHART_COLORS[:len(values)],
                    "borderRadius": 4,
                }],
            })

    else:  # comparison
        comparison_data = report_svc.property_comparison(db, start_date, end_date)
        if comparison_data:
            labels = [d["property"] for d in comparison_data]
            values = [d["total_spending"] for d in comparison_data]
            comparison_chart = json.dumps({
                "labels": labels,
                "datasets": [{
                    "label": "Total Spending",
                    "data": values,
                    "backgroundColor": CHART_COLORS[:len(values)],
                    "borderRadius": 4,
                }],
            })

    return templates.TemplateResponse(request, "reports.html", {
        "page": "reports",
        "tab": tab,
        "properties": properties,
        "selected_prop": selected_prop,
        "property_id": str(selected_prop.id) if selected_prop else "",
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "category_data": category_data,
        "vendor_data": vendor_data,
        "comparison_data": comparison_data,
        "category_chart": category_chart,
        "vendor_chart": vendor_chart,
        "comparison_chart": comparison_chart,
    })
