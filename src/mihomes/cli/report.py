"""Report CLI commands — financial reporting and analytics."""

from datetime import date
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error
from mihomes.db import get_session
from mihomes.services import financial_report as report_svc
from mihomes.services.slug import EntityNotFoundError

app = typer.Typer(name="report", help="Financial reports and analytics")


@app.command("spending")
def spending_report(
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    by: str = typer.Option("category", "--by", "-b", help="Group by: category or vendor"),
    start: Optional[str] = typer.Option(None, "--start", help="Start date YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD"),
):
    """Show spending report grouped by category or vendor."""
    period_start = date.fromisoformat(start) if start else date(date.today().year, 1, 1)
    period_end = date.fromisoformat(end) if end else date.today()
    with get_session() as session:
        try:
            if by == "vendor":
                rows = report_svc.spending_by_vendor(session, property, period_start, period_end)
                table = Table(title=f"Spending by Vendor ({period_start} to {period_end})")
                table.add_column("Vendor", style="bold")
                table.add_column("Transactions", justify="right")
                table.add_column("Total", justify="right", style="green")
                for r in rows:
                    table.add_row(r["vendor"], str(r["transaction_count"]), f"{r['total']:,.2f}")
            else:
                rows = report_svc.spending_by_category(session, property, period_start, period_end)
                table = Table(title=f"Spending by Category ({period_start} to {period_end})")
                table.add_column("Category", style="bold")
                table.add_column("Transactions", justify="right")
                table.add_column("Total", justify="right", style="green")
                for r in rows:
                    table.add_row(r["category"], str(r["transaction_count"]), f"{r['total']:,.2f}")
            console.print(table)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("compare")
def compare_properties(
    start: Optional[str] = typer.Option(None, "--start", help="Start date YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD"),
):
    """Compare spending across all properties."""
    period_start = date.fromisoformat(start) if start else date(date.today().year, 1, 1)
    period_end = date.fromisoformat(end) if end else date.today()
    with get_session() as session:
        rows = report_svc.property_comparison(session, period_start, period_end)
        if not rows:
            console.print("[dim]No spending data found.[/dim]")
            return
        table = Table(title=f"Property Spending Comparison ({period_start} to {period_end})")
        table.add_column("Property", style="bold")
        table.add_column("Transactions", justify="right")
        table.add_column("Total Spending", justify="right", style="green")
        table.add_column("Currency")
        for r in rows:
            table.add_row(
                r["property"], str(r["transaction_count"]),
                f"{r['total_spending']:,.2f}", r["currency"],
            )
        console.print(table)


@app.command("forecast")
def forecast_spending(
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    months: int = typer.Option(6, "--months", "-m", help="Number of months to forecast"),
):
    """Forecast future spending based on historical data."""
    with get_session() as session:
        try:
            result = report_svc.forecast(session, property, months=months)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)

        console.print(f"\n[bold]Spending Forecast: {result['property']}[/bold]")
        console.print(f"Historical period: {result['historical_period']}")
        console.print(f"Monthly average: [green]{result['monthly_average']:,.2f}[/green]")
        console.print(f"Forecast ({result['forecast_months']} months): [bold green]{result['forecast_total']:,.2f}[/bold green]")

        if result["by_category"]:
            console.print()
            table = Table(title="Forecast by Category")
            table.add_column("Category", style="bold")
            table.add_column("Monthly Avg", justify="right")
            table.add_column(f"{months}-Month Forecast", justify="right", style="green")
            for cat in result["by_category"]:
                table.add_row(
                    cat["category"],
                    f"{cat['monthly_avg']:,.2f}",
                    f"{cat['forecast_total']:,.2f}",
                )
            console.print(table)
