"""Weather CLI commands."""

from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success, severity_color
from mihomes.db import get_session

app = typer.Typer(name="weather", help="Weather forecasts and alerts for your properties")

_TIMEZONE_NAMES = {
    "America/New_York":    "Eastern Time",
    "America/Chicago":     "Central Time",
    "America/Denver":      "Mountain Time",
    "America/Phoenix":     "Mountain Time (no DST)",
    "America/Los_Angeles": "Pacific Time",
    "America/Anchorage":   "Alaska Time",
    "America/Honolulu":    "Hawaii Time",
    "Europe/London":       "GMT / London",
    "Europe/Paris":        "Central European Time",
    "Europe/Berlin":       "Central European Time",
    "Asia/Tokyo":          "Japan Time",
    "Asia/Dubai":          "Gulf Time",
    "Australia/Sydney":    "Australian Eastern Time",
}

def _friendly_timezone(tz: str) -> str:
    return _TIMEZONE_NAMES.get(tz, tz)


@app.command("show")
def show_weather(
    property_slug: str = typer.Argument(..., help="Property slug or ID"),
    days: int = typer.Option(7, "--days", "-d", help="Forecast days to show (1-7)"),
):
    """Show current conditions and forecast for a property."""
    from mihomes.models.property import Property
    from mihomes.services.slug import resolve_identifier
    from mihomes.services.weather import get_forecast_for_property

    days = max(1, min(days, 7))

    with get_session() as session:
        try:
            prop = resolve_identifier(session, Property, property_slug)
        except Exception:
            format_error(f"Property not found: {property_slug}")
            raise typer.Exit(1)

        if not prop.address:
            format_error(f"{prop.name} has no address set — cannot fetch weather.")
            console.print("[dim]Set an address with: mihomes property edit <slug> --address \"...\""
                          "[/dim]")
            raise typer.Exit(1)

        console.print(f"[dim]Fetching weather for {prop.name}...[/dim]")

        forecast = get_forecast_for_property(session, prop)
        if forecast is None:
            format_error("Could not fetch weather. Check your internet connection or verify the address.")
            raise typer.Exit(1)

    c = forecast.current
    console.print(Panel(
        f"[bold]{c.description}[/bold]  {c.temperature:.0f}°F (feels like {c.feels_like:.0f}°F)\n"
        f"Humidity: {c.humidity}%   Wind: {c.wind_speed:.0f} mph   Gusts: {c.wind_gusts:.0f} mph\n"
        f"Precipitation: {c.precipitation:.2f}\"",
        title=f"Current — {forecast.property_name}",
        subtitle=f"{forecast.latitude:.4f}, {forecast.longitude:.4f}  |  {_friendly_timezone(forecast.timezone)}",
    ))

    table = Table(title=f"{days}-Day Forecast", show_header=True, header_style="bold")
    table.add_column("Date")
    table.add_column("Conditions")
    table.add_column("High", justify="right")
    table.add_column("Low", justify="right")
    table.add_column("Rain", justify="right")
    table.add_column("Rain%", justify="right")
    table.add_column("Gusts", justify="right")

    for day in forecast.daily[:days]:
        low_style = ""
        if day.temp_low <= 20:
            low_style = "bold red"
        elif day.temp_low <= 32:
            low_style = "cyan"

        rain_style = ""
        if day.precipitation >= 2.0:
            rain_style = "red"
        elif day.precipitation >= 1.0:
            rain_style = "yellow"

        gust_style = ""
        if day.wind_gusts >= 60:
            gust_style = "bold red"
        elif day.wind_gusts >= 45:
            gust_style = "yellow"

        table.add_row(
            str(day.date),
            day.description,
            f"{day.temp_high:.0f}°F",
            f"[{low_style}]{day.temp_low:.0f}°F[/{low_style}]" if low_style else f"{day.temp_low:.0f}°F",
            f"[{rain_style}]{day.precipitation:.2f}\"[/{rain_style}]" if rain_style else f"{day.precipitation:.2f}\"",
            f"{day.precip_probability}%",
            f"[{gust_style}]{day.wind_gusts:.0f} mph[/{gust_style}]" if gust_style else f"{day.wind_gusts:.0f} mph",
        )

    console.print(table)


@app.command("alerts")
def weather_alerts(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be alerted without saving"),
):
    """Check all properties for notable weather and generate alerts."""
    from mihomes.services.weather import generate_weather_alerts, get_forecast_for_property
    from mihomes.models.property import Property

    if dry_run:
        with get_session() as session:
            props = session.query(Property).all()
            found = False
            for prop in props:
                forecast = get_forecast_for_property(session, prop)
                if forecast is None:
                    console.print(f"[dim]{prop.name}: no location data[/dim]")
                    continue
                from mihomes.services.weather import _assess_day
                for day in forecast.daily:
                    items = _assess_day(day, prop.name)
                    for severity, message, _ in items:
                        sev_style = severity_color(severity.value)
                        console.print(f"[{sev_style}][{severity.value}][/{sev_style}] {message} — {day.date}")
                        found = True
            if not found:
                console.print("[green]No notable weather events in the 7-day forecast.[/green]")
        return

    with get_session() as session:
        count = generate_weather_alerts(session)

    if count:
        format_success(f"{count} weather alert(s) generated — run 'mihomes alerts' to view")
    else:
        console.print("[green]No new weather alerts — all clear for the next 7 days.[/green]")


@app.command("suggest")
def suggest_tasks(
    property_slug: Optional[str] = typer.Argument(None, help="Property slug/ID (omit for all properties)"),
    accept: Optional[str] = typer.Option(None, "--accept", "-a", help="Comma-separated suggestion numbers to create as tasks"),
    auto: bool = typer.Option(False, "--auto", help="Auto-create all suggestions without prompting"),
):
    """Use AI to suggest maintenance tasks based on the weather forecast.

    Examples:\n
      mihomes weather suggest beach-house\n
      mihomes weather suggest beach-house --accept 1,3\n
      mihomes weather suggest beach-house --auto
    """
    from mihomes.models.property import Property
    from mihomes.services.slug import resolve_identifier
    from mihomes.services.weather import get_forecast_for_property
    from mihomes.services.weather_tasks import (
        suggest_tasks_for_weather,
        generate_suggestions_all_properties,
        create_tasks_from_suggestions,
    )
    from mihomes.services.ai.provider import AIProviderError

    # ── Single property ──────────────────────────────────────────────────
    if property_slug:
        with get_session() as session:
            try:
                prop = resolve_identifier(session, Property, property_slug)
            except Exception:
                format_error(f"Property not found: {property_slug}")
                raise typer.Exit(1)

            if not prop.address:
                format_error(f"{prop.name} has no address — cannot fetch weather.")
                raise typer.Exit(1)

            console.print(f"[dim]Fetching weather for {prop.name}...[/dim]")
            forecast = get_forecast_for_property(session, prop)
            if forecast is None:
                format_error("Could not fetch weather data.")
                raise typer.Exit(1)

            console.print("[dim]Asking AI for task suggestions...[/dim]")
            try:
                suggestions = suggest_tasks_for_weather(session, prop, forecast)
            except AIProviderError as e:
                format_error(f"AI error: {e}")
                raise typer.Exit(1)

            if not suggestions:
                console.print(f"[green]No weather-triggered tasks needed for {prop.name}.[/green]")
                return

            _display_suggestions(prop.name, suggestions)

            if auto:
                indices = None
            elif accept:
                indices = _parse_indices(accept, len(suggestions))
                if indices is None:
                    format_error("Invalid --accept value. Use comma-separated numbers e.g. --accept 1,3")
                    raise typer.Exit(1)
            else:
                console.print("\n[dim]Use --accept 1,2,3 or --auto to create tasks.[/dim]")
                return

            created = create_tasks_from_suggestions(session, prop.slug, suggestions, indices)
            created_info = [(t.id, t.title, t.due_date) for t in created]

        if created_info:
            format_success(f"{len(created_info)} task(s) created from weather suggestions")
            for tid, title, due in created_info:
                console.print(f"  [dim]#{tid}[/dim] {title} (due {due})")
        return

    # ── All properties ───────────────────────────────────────────────────
    # L6: --accept selects suggestions *by number*, but numbering restarts per
    # property, so a single --accept list can't mean anything coherent across
    # all properties (it would apply the same indices to every one). Reject it
    # up front and steer the user to per-property acceptance. --auto is fine —
    # it means "all of them" regardless of property.
    if accept:
        format_error(
            "--accept needs a specific property (numbering is per-property). "
            "Run 'mihomes weather suggest <property> --accept ...', or use --auto to create all."
        )
        raise typer.Exit(1)

    console.print("[dim]Fetching weather and generating suggestions for all properties...[/dim]")
    try:
        with get_session() as session:
            results = generate_suggestions_all_properties(session)
    except AIProviderError as e:
        format_error(f"AI error: {e}")
        raise typer.Exit(1)

    if not results:
        console.print("[green]No weather-triggered tasks needed for any property.[/green]")
        return

    total = sum(len(s) for s in results.values())
    console.print(f"\n[bold]{total} suggestion(s) across {len(results)} propert(ies)[/bold]\n")

    for slug, suggestions in results.items():
        _display_suggestions(slug, suggestions)

    # --accept was rejected above, so here only --auto ("create all") applies.
    if not auto:
        console.print("\n[dim]Run 'mihomes weather suggest <property> --accept ...' to create tasks.[/dim]")
        return

    with get_session() as session:
        total_created = 0
        for slug, suggestions in results.items():
            created = create_tasks_from_suggestions(session, slug, suggestions, indices=None)
            total_created += len(created)

    if total_created:
        format_success(f"{total_created} task(s) created across all properties")


def _display_suggestions(property_name: str, suggestions) -> None:
    """Render suggestions as a Rich table."""
    from mihomes.cli.formatters import severity_color

    table = Table(
        title=f"Weather Task Suggestions — {property_name}",
        show_header=True,
        header_style="bold",
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Task")
    table.add_column("Priority", width=8)
    table.add_column("Category", width=12)
    table.add_column("Due In", width=8, justify="right")
    table.add_column("Trigger", style="dim")

    for i, s in enumerate(suggestions, 1):
        pstyle = severity_color(s.priority)
        table.add_row(
            str(i),
            s.title,
            f"[{pstyle}]{s.priority}[/{pstyle}]",
            s.category,
            f"{s.due_days}d",
            s.weather_trigger[:60] + ("…" if len(s.weather_trigger) > 60 else ""),
        )
        if s.description:
            table.add_row("", f"[dim]{s.description[:120]}[/dim]", "", "", "", "")

    console.print(table)


def _parse_indices(value: str, max_count: int) -> list[int] | None:
    """Parse '1,3,5' into [1, 3, 5], validating against max_count."""
    try:
        indices = [int(x.strip()) for x in value.split(",")]
        if all(1 <= i <= max_count for i in indices):
            return indices
    except ValueError:
        pass
    return None


@app.command("all")
def all_properties(
    days: int = typer.Option(3, "--days", "-d", help="Forecast days to show"),
):
    """Show a brief weather summary for all properties."""
    from mihomes.models.property import Property
    from mihomes.services.weather import get_forecast_for_property

    days = max(1, min(days, 7))

    with get_session() as session:
        props = session.query(Property).all()
        if not props:
            console.print("[dim]No properties found.[/dim]")
            return

        for prop in props:
            if not prop.address:
                console.print(f"[dim]{prop.name}: no address set[/dim]")
                continue

            console.print(f"[dim]Fetching {prop.name}...[/dim]", end="\r")
            forecast = get_forecast_for_property(session, prop)
            if forecast is None:
                console.print(f"[yellow]{prop.name}: unable to fetch weather[/yellow]")
                continue

            c = forecast.current
            lines = [
                f"Now: {c.temperature:.0f}°F, {c.description}, "
                f"wind {c.wind_speed:.0f}/{c.wind_gusts:.0f} mph gusts"
            ]
            for day in forecast.daily[:days]:
                lines.append(
                    f"  {day.date}: {day.description} "
                    f"{day.temp_high:.0f}/{day.temp_low:.0f}°F, "
                    f"{day.precipitation:.2f}\" rain"
                )

            console.print(Panel("\n".join(lines), title=prop.name, expand=False))
