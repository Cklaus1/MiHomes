"""Telegram CLI commands — bot setup, chat linking, monitoring, and review."""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success, severity_color
from mihomes.db import get_session
from mihomes.services.gateways.telegram.client import TelegramClient, TelegramError
import logging

logger = logging.getLogger(__name__)

app = typer.Typer(name="telegram", help="Telegram bot gateway")

_LOG_DIR = Path(os.path.expanduser("~/.mihomes"))


def _get_token(session=None) -> str | None:
    from mihomes.services.config_service import get_config
    if session:
        return get_config(session, "telegram.bot_token")
    with get_session() as s:
        return get_config(s, "telegram.bot_token")


def _get_client() -> TelegramClient:
    token = _get_token()
    if not token:
        raise TelegramError(
            "Bot token not configured. Run: mihomes config set telegram.bot_token <token>"
        )
    return TelegramClient(token)


def _load_chat_links(session) -> dict:
    from mihomes.services.config_service import get_config
    raw = get_config(session, "telegram.chat_links") or "{}"
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}


def _save_chat_links(session, links: dict) -> None:
    from mihomes.services.config_service import set_config
    set_config(session, "telegram.chat_links", json.dumps(links))


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

@app.command("setup")
def setup_cmd():
    """Instructions for creating a Telegram bot and configuring the token."""
    console.print(Panel(
        "[bold]Telegram Bot Setup[/bold]\n\n"
        "1. Open Telegram and message [cyan]@BotFather[/cyan]\n"
        "2. Send [cyan]/newbot[/cyan] — give it a name (e.g. MiHomes) and a username\n"
        "3. BotFather gives you a token — copy it\n"
        "4. Run: [cyan]mihomes config set telegram.bot_token <token>[/cyan]\n"
        "5. Add the bot to your household group(s) and make it an [bold]admin[/bold]\n"
        "   (Admin access lets the bot read all messages in the group)\n"
        "6. Link chats to properties: [cyan]mihomes telegram link-chat <chat-id> --property <slug>[/cyan]\n"
        "7. Start monitoring: [cyan]mihomes telegram monitor[/cyan]\n\n"
        "[dim]To find a group's chat ID, run: mihomes telegram status (after adding the bot)[/dim]",
        title="Telegram Bot Setup",
        expand=False,
    ))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command("status")
def status_cmd():
    """Show bot info and linked chats."""
    try:
        client = _get_client()
        bot = client.get_me()
    except TelegramError as e:
        format_error(str(e))
        raise typer.Exit(1) from e

    with get_session() as session:
        chat_links = _load_chat_links(session)

    watchdog_pid_file = _LOG_DIR / "watchdog.pid"
    local_watchdog_line = "[dim]Not running[/dim]"
    if watchdog_pid_file.exists():
        try:
            pid = int(watchdog_pid_file.read_text().strip())
            if _pid_running(pid):
                local_watchdog_line = f"[green]Running[/green] (PID {pid})"
        except (ValueError, OSError):
            pass

    console.print(Panel(
        f"Bot: [bold]@{bot.get('username', '?')}[/bold] (ID: {bot.get('id', '?')})\n"
        f"Name: {bot.get('first_name', '?')}\n"
        f"Status: [green]Connected[/green]\n"
        f"Linked chats: {len(chat_links)}\n"
        f"Local watchdog: {local_watchdog_line}",
        title="Telegram Bot",
        expand=False,
    ))

    if chat_links:
        table = Table(title="Linked Chats")
        table.add_column("Chat ID", style="dim")
        table.add_column("Property", style="bold")
        for chat_id, slug in chat_links.items():
            table.add_row(chat_id, slug)
        console.print(table)
    else:
        console.print("[dim]No chats linked yet. Add the bot to a group, then run:[/dim]")
        console.print("[dim]  mihomes telegram link-chat <chat-id> --property <slug>[/dim]")


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

@app.command("discover")
def discover_cmd():
    """Fetch recent updates and show all chats the bot has seen.

    Use this to find a group's chat ID after adding the bot.
    Send any message (or /start) in the group first, then run this command.
    """
    try:
        client = _get_client()
    except TelegramError as e:
        format_error(str(e))
        raise typer.Exit(1) from e

    with get_session() as session:
        chat_links = _load_chat_links(session)
        from mihomes.services.config_service import get_config
        stored = get_config(session, "telegram.last_update_id")

    # Fetch the most recent 100 updates without advancing the offset
    # so we don't skip messages the monitor hasn't processed yet
    last_id = int(stored) if stored else None
    offset = (last_id - 100) if last_id else None
    if offset and offset < 0:
        offset = None

    try:
        updates = client.get_updates(offset=offset, timeout=0, limit=100)
    except TelegramError as e:
        format_error(str(e))
        raise typer.Exit(1) from e

    if not updates:
        console.print(
            "[yellow]No recent updates found.[/yellow]\n\n"
            "The bot hasn't received any messages yet. Try:\n"
            "  1. Send [bold]/start[/bold] in the group\n"
            "  2. Run [cyan]mihomes telegram discover[/cyan] again"
        )
        return

    # Collect unique chats from updates
    seen: dict[str, dict] = {}
    for update in updates:
        msg = update.get("message")
        if not msg:
            continue
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if chat_id and chat_id not in seen:
            seen[chat_id] = {
                "id": chat_id,
                "title": chat.get("title") or chat.get("first_name") or "Direct Message",
                "type": chat.get("type", "unknown"),
                "linked_to": chat_links.get(chat_id, ""),
            }

    if not seen:
        console.print("[yellow]Updates found but no message chats — make sure the bot can read messages (disable privacy mode or make it admin).[/yellow]")
        return

    table = Table(title="Chats the Bot Has Seen")
    table.add_column("Chat ID", style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Linked To")
    for info in seen.values():
        linked = f"[green]{info['linked_to']}[/green]" if info["linked_to"] else "[dim]not linked[/dim]"
        table.add_row(info["id"], info["title"], info["type"], linked)
    console.print(table)

    unlinked = [c for c in seen.values() if not c["linked_to"]]
    if unlinked:
        console.print("\n[dim]To link a chat:[/dim]")
        for c in unlinked:
            console.print(f"[dim]  mihomes telegram link-chat --chat-id {c['id']} --property <slug>[/dim]")


# ---------------------------------------------------------------------------
# link-chat / unlink-chat
# ---------------------------------------------------------------------------

@app.command("link-chat")
def link_chat(
    chat_id: str = typer.Option(..., "--chat-id", "-c", help="Telegram chat ID (e.g. -5211888862)"),
    property: str = typer.Option(..., "--property", "-p", help="Property slug to link to"),
):
    """Link a Telegram group chat to a property."""
    with get_session() as session:
        links = _load_chat_links(session)
        links[chat_id] = property
        _save_chat_links(session, links)
    format_success(f"Chat {chat_id} linked to property '{property}'")
    console.print("[dim]Tip: restart monitor if it's running so it picks up the new link.[/dim]")


@app.command("unlink-chat")
def unlink_chat(
    chat_id: str = typer.Option(..., "--chat-id", "-c", help="Telegram chat ID to unlink"),
):
    """Unlink a Telegram chat from its property."""
    with get_session() as session:
        links = _load_chat_links(session)
        if chat_id not in links:
            format_error(f"Chat {chat_id} is not linked")
            raise typer.Exit(1)
        del links[chat_id]
        _save_chat_links(session, links)
    format_success(f"Chat {chat_id} unlinked")


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

@app.command("send")
def send_message(
    chat_id: str = typer.Argument(..., help="Telegram chat ID"),
    message: str = typer.Argument(..., help="Message text"),
):
    """Send a message to a Telegram chat."""
    try:
        client = _get_client()
        client.send_message(chat_id, message)
        format_success(f"Message sent to chat {chat_id}")
    except TelegramError as e:
        format_error(str(e))
        raise typer.Exit(1) from e


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------

@app.command("monitor")
def monitor(
    interval: int = typer.Option(15, "--interval", "-i", help="Poll interval in seconds"),
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Scope to a property slug"),
):
    """Monitor linked Telegram groups and auto-respond with AI analysis.

    Polls for new messages, logs issues/tasks, and sends AI advisory replies
    back into the group. Press Ctrl+C to stop.
    """
    from mihomes.services.gateways.telegram.responder import process_and_respond

    try:
        client = _get_client()
        bot = client.get_me()
    except TelegramError as e:
        format_error(str(e))
        raise typer.Exit(1) from e

    console.print(
        f"[bold green]Monitoring Telegram (@{bot.get('username', '?')})[/bold green] "
        f"(polling every {interval}s) — Ctrl+C to stop\n"
    )

    from mihomes.services.config_service import get_config, set_config
    from mihomes.services.gateways.dedup import PoisonGuard, ProcessedIdStore

    # One shared, insertion-ordered store per gateway (M22/M23): the extractor
    # uses this same key, and pruning drops the oldest ids, never the newest.
    _id_store = ProcessedIdStore("telegram.processed_ids", cap=2000)
    # M21 poison guard: quarantine updates that repeatedly crash processing.
    _poison = PoisonGuard("telegram", max_attempts=3)
    processed_ids: set = set(_id_store.load())

    # Load last_update_id from config so restarts don't replay old messages
    with get_session() as session:
        stored = get_config(session, "telegram.last_update_id")
        last_update_id: int | None = int(stored) if stored else None
        chat_links = _load_chat_links(session)

    def _save_update_id(uid: int) -> None:
        with get_session() as s:
            set_config(s, "telegram.last_update_id", str(uid))

    def _run_loop():
        nonlocal last_update_id, processed_ids, chat_links

        while True:
            try:
                # Reload chat links each cycle so link-chat changes take effect without restart
                with get_session() as s:
                    chat_links = _load_chat_links(s)

                offset = last_update_id + 1 if last_update_id is not None else None
                updates = client.get_updates(offset=offset, timeout=0, limit=50)

                if updates:
                    new_last_id = max(u["update_id"] for u in updates)

                    messages = []
                    for update in updates:
                        msg = client.normalize_update(update, chat_links)
                        if msg and msg.get("id") not in processed_ids:
                            messages.append(msg)

                    if property:
                        messages = [m for m in messages if m.get("propertySlug") == property]

                    # M21 poison guard: record an attempt for each id BEFORE
                    # processing so a crash mid-batch leaves the counter bumped
                    # (retry on restart → no loss). Ids past the retry cap are
                    # quarantined and dropped so they can't hot-loop the monitor.
                    live, poisoned = _poison.partition(m["id"] for m in messages if m.get("id"))
                    if poisoned:
                        console.print(
                            f"  [red]⚠[/red] quarantined {len(poisoned)} poison message(s) after repeated failures"
                        )
                        live_set = set(live)
                        messages = [m for m in messages if m.get("id") in live_set]

                    if messages:
                        from collections import defaultdict
                        by_property: dict = defaultdict(list)
                        for m in messages:
                            slug = m.get("propertySlug")
                            if slug:
                                by_property[slug].append(m)

                        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                        total_logged = total_replied = 0
                        all_errors: list = []

                        for prop_slug, prop_msgs in by_property.items():
                            console.print(f"[dim]{now_str}[/dim] {len(prop_msgs)} message(s) for '{prop_slug}' — analyzing...")
                            with get_session() as session:
                                result = process_and_respond(session, prop_msgs, property_slug=prop_slug)
                            total_logged += result["logged"]
                            total_replied += result["replied"]
                            all_errors.extend(result["errors"])

                        if total_logged:
                            console.print(f"  [green]✓[/green] {total_logged} item(s) logged")
                        if total_replied:
                            console.print(f"  [green]✓[/green] {total_replied} reply(ies) sent")
                        for err in all_errors:
                            console.print(f"  [yellow]⚠[/yellow] {err}")

                        new_ids = [m["id"] for m in messages if m.get("id")]
                        _id_store.add(new_ids)
                        processed_ids.update(new_ids)
                        # Processing succeeded — clear these ids' attempt
                        # counters so a future reuse isn't pre-poisoned.
                        _poison.clear(new_ids)

                    # Advance the offset only AFTER processing (or after
                    # quarantining an all-poison batch): a crash above leaves the
                    # offset where it was, so the batch is refetched and retried
                    # on restart (M21 — no silent loss).
                    last_update_id = new_last_id
                    _save_update_id(new_last_id)

                time.sleep(interval)

            except KeyboardInterrupt:
                raise
            except TelegramError as e:
                console.print(f"[yellow]{datetime.now(timezone.utc).strftime('%H:%M:%S')} Telegram error: {e} — retrying in 30s...[/yellow]")
                time.sleep(30)
            except Exception as e:
                console.print(f"[red]{datetime.now(timezone.utc).strftime('%H:%M:%S')} Unexpected error: {e} — restarting in 30s...[/red]")
                time.sleep(30)
                raise

    while True:
        try:
            _run_loop()
        except KeyboardInterrupt:
            console.print("\n[dim]Monitor stopped.[/dim]")
            break
        except Exception:
            console.print(f"[yellow]{datetime.now(timezone.utc).strftime('%H:%M:%S')} Monitor restarting...[/yellow]")
            time.sleep(30)


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

@app.command("review")
def review_messages(
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Property slug"),
    chat_id: Optional[str] = typer.Option(None, "--chat", "-c", help="Filter to a specific chat ID"),
    accept: Optional[str] = typer.Option(None, "--accept", help="Comma-separated item numbers to create (e.g. 1,3)"),
    auto: bool = typer.Option(False, "--auto", help="Auto-create all actionable items without prompting"),
    limit: int = typer.Option(200, "--limit", "-n", help="Number of recent updates to fetch"),
):
    """Review AI-extracted issues/tasks from Telegram group conversations."""
    from mihomes.services.ai.provider import AIAuthError, AIProviderError
    from mihomes.services.gateways.telegram.review import analyze_messages

    try:
        client = _get_client()
    except TelegramError as e:
        format_error(str(e))
        raise typer.Exit(1) from e

    with get_session() as session:
        chat_links = _load_chat_links(session)
        stored = get_config_val(session, "telegram.last_update_id")

    last_update_id: int | None = int(stored) if stored else None
    offset = (last_update_id - limit) if last_update_id else None
    if offset and offset < 0:
        offset = None

    try:
        updates = client.get_updates(offset=offset, timeout=0, limit=limit)
    except TelegramError as e:
        format_error(str(e))
        raise typer.Exit(1) from e

    messages = []
    for update in updates:
        msg = client.normalize_update(update, chat_links)
        if msg:
            messages.append(msg)

    if chat_id:
        messages = [m for m in messages if m.get("jid") == str(chat_id)]
    if property:
        messages = [m for m in messages if m.get("propertySlug") == property]

    if not messages:
        console.print("[dim]No messages to review.[/dim]")
        return

    with get_session() as session:
        try:
            with console.status("[bold blue]Analyzing conversations...", spinner="dots"):
                result = analyze_messages(session, messages, property_name=property, property_slug=property)
        except (AIAuthError, AIProviderError) as e:
            format_error(str(e))
            raise typer.Exit(1) from e

        items = result.get("items", [])
        skipped = result.get("skipped", [])

        if not items:
            console.print("[green]No actionable items found.[/green]")
            return

        table = Table(title=f"Telegram Review Queue ({len(items)} items)")
        table.add_column("#", style="dim")
        table.add_column("Category")
        table.add_column("Title", style="bold")
        table.add_column("Severity")
        table.add_column("Reported By")
        for i, item in enumerate(items, 1):
            sev = item.get("severity", "-")
            sev_display = f"[{severity_color(sev)}]{sev}[/{severity_color(sev)}]" if sev != "-" else "-"
            table.add_row(
                str(i), item.get("category", "-"),
                item.get("title", "-"), sev_display,
                item.get("reported_by", "-"),
            )
        console.print(table)

        if skipped:
            console.print(f"\n[dim]Skipped {len(skipped)} non-actionable messages[/dim]")

        if auto:
            _create_items(session, items, list(range(1, len(items) + 1)), property)
        elif accept:
            indices = [int(x.strip()) for x in accept.split(",") if x.strip().isdigit()]
            _create_items(session, items, indices, property)
        else:
            console.print("\n[dim]Use --accept 1,2,3 to create specific items, or --auto to create all.[/dim]")


def get_config_val(session, key: str) -> str | None:
    from mihomes.services.config_service import get_config
    return get_config(session, key)


def _create_items(session, items: list[dict], indices: list[int], property_slug: str | None):
    from mihomes.models.issue import IssueSeverity
    from mihomes.services.issue import create_issue
    from mihomes.services.task import create_task

    created = 0
    for idx in indices:
        if idx < 1 or idx > len(items):
            continue
        item = items[idx - 1]
        cat = item.get("category", "")
        title = item.get("title", "Unknown")

        if cat in ("informational", "task_completion", "vendor_activity"):
            continue

        if not property_slug:
            console.print(f"[yellow]Skipping '{title}' — no property specified[/yellow]")
            continue

        try:
            if cat == "issue":
                sev_val = item.get("severity", "medium")
                try:
                    sev = IssueSeverity(sev_val)
                except ValueError:
                    sev = IssueSeverity.MEDIUM
                create_issue(session, title, property_slug, severity=sev, description=item.get("description"))
                created += 1
            elif cat in ("task", "supply_need"):
                create_task(session, title, property_slug, description=item.get("description"))
                created += 1
        except Exception as e:
            console.print(f"[yellow]Failed to create '{title}': {e}[/yellow]")

    if created:
        format_success(f"{created} item(s) created from Telegram review")


# ---------------------------------------------------------------------------
# watchdog / autostart
# ---------------------------------------------------------------------------

@app.command("watchdog")
def watchdog_cmd():
    """Start the watchdog now (keeps monitor alive silently in background)."""
    watchdog_script = Path(__file__).parents[3] / "scripts" / "watchdog.py"
    if not watchdog_script.exists():
        format_error(f"Watchdog script not found: {watchdog_script}")
        raise typer.Exit(1)

    with get_session() as session:
        from mihomes.services.config_service import get_config
        monitor_property = get_config(session, "telegram.monitor_property") or "belle-estate"

    _start_watchdog_now(watchdog_script, monitor_property)
    format_success("Watchdog started — monitor running silently in background")


@app.command("stop")
def stop_cmd():
    """Stop the local watchdog + monitor on this device and remove Windows autostart, if present."""
    from mihomes.services.config_service import set_config

    stopped_any = False
    still_running = False
    for label, filename in (("watchdog", "watchdog.pid"), ("monitor", "monitor.pid")):
        pid_file = _LOG_DIR / filename
        if not pid_file.exists():
            continue
        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            pid_file.unlink(missing_ok=True)
            continue
        if not _pid_running(pid):
            pid_file.unlink(missing_ok=True)
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            format_success(f"Stopped local {label} (PID {pid})")
            stopped_any = True
            pid_file.unlink(missing_ok=True)
        except (ProcessLookupError, PermissionError, OSError) as e:
            console.print(
                f"[yellow]Could not stop {label} (PID {pid}):[/yellow] {e} "
                f"— it may need an elevated terminal (e.g. Task Manager 'Run as administrator')"
            )
            still_running = True

    # The shared watchdog supervises the WhatsApp monitor + bridge too, so a full
    # stop must reap them as well — otherwise they are orphaned (spec M31).
    try:
        from mihomes.cli.whatsapp import stop_whatsapp_processes
        if stop_whatsapp_processes():
            stopped_any = True
    except Exception as e:  # pragma: no cover - defensive: never block telegram stop
        console.print(f"[yellow]Could not stop WhatsApp processes:[/yellow] {e}")

    if not stopped_any and not still_running:
        console.print("[dim]No local watchdog or monitor was running.[/dim]")

    task_result = _remove_autostart_task()
    if task_result == "removed":
        format_success("Removed Windows startup task 'MiHomesWatchdog'")
    elif task_result == "error":
        console.print(
            "[yellow]Could not remove Windows startup task 'MiHomesWatchdog':[/yellow] "
            "access denied — run from an elevated terminal (Task Scheduler > MiHomesWatchdog > Disable/Delete)"
        )
    elif sys.platform == "win32":
        console.print("[dim]No Windows startup task was registered.[/dim]")

    with get_session() as session:
        set_config(session, "telegram.autostart", "false")


@app.command("autostart")
def autostart_cmd(
    enable: bool = typer.Argument(..., help="true to enable, false to disable"),
    property: str = typer.Option("belle-estate", "--property", "-p", help="Property to monitor"),
):
    """Register (or remove) the MiHomes watchdog as a Windows login startup task."""
    from mihomes.services.config_service import set_config
    with get_session() as session:
        set_config(session, "telegram.autostart", "true" if enable else "false")
        set_config(session, "telegram.monitor_property", property)

    task_name = "MiHomesWatchdog"
    watchdog_script = Path(__file__).parents[3] / "scripts" / "watchdog.py"

    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        def _run(cmd):
            return subprocess.run(cmd, capture_output=True, text=True,
                                  creationflags=0x08000000, startupinfo=si)

        if enable:
            tr = f'"{sys.executable}" "{watchdog_script}"'
            result = _run([
                "schtasks", "/create", "/tn", task_name,
                "/tr", tr, "/sc", "ONLOGON", "/rl", "HIGHEST", "/f",
            ])
            if result.returncode != 0:
                console.print(f"[yellow]Task Scheduler registration failed:[/yellow] {result.stderr.strip()}")
                console.print("[dim]You can still start manually: mihomes telegram watchdog[/dim]")
            else:
                format_success(f"Watchdog registered as Windows startup task '{task_name}'")
            _start_watchdog_now(watchdog_script, property)
            console.print("[dim]Watchdog started — monitor will run silently in the background.[/dim]")
        else:
            task_result = _remove_autostart_task()
            if task_result == "removed":
                format_success(f"Startup task '{task_name}' removed")
            elif task_result == "error":
                console.print(
                    f"[yellow]Could not remove startup task '{task_name}':[/yellow] "
                    "access denied — run from an elevated terminal"
                )
            else:
                console.print("[dim]Task not found or already removed.[/dim]")
    else:
        status = "enabled" if enable else "disabled"
        format_success(f"Telegram autostart {status} (property: {property})")
        if enable:
            console.print(f"[dim]Add to cron or systemd: {sys.executable} {watchdog_script}[/dim]")


def _remove_autostart_task() -> str:
    """Remove the Windows login startup task, if registered.

    Returns "removed", "not_found", or "error" (e.g. access denied — the task
    was registered with /rl HIGHEST and needs an elevated caller to delete).
    """
    if sys.platform != "win32":
        return "not_found"
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", "MiHomesWatchdog", "/f"],
        capture_output=True, text=True,
        creationflags=0x08000000, startupinfo=si,
    )
    if result.returncode == 0:
        return "removed"
    if "access is denied" in result.stderr.lower():
        return "error"
    return "not_found"


def _pid_running(pid: int) -> bool:
    """Cross-platform liveness check for a previously recorded PID."""
    if sys.platform == "win32":
        try:
            si_check = subprocess.STARTUPINFO()
            si_check.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si_check.wShowWindow = subprocess.SW_HIDE
            from mihomes.services.gateways.pid import tasklist_has_pid
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                               capture_output=True, text=True,
                               creationflags=0x08000000, startupinfo=si_check)
            return tasklist_has_pid(r.stdout, pid)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def _start_watchdog_now(watchdog_script: Path, monitor_property: str = "belle-estate"):
    """Start the watchdog process silently."""
    log_dir = _LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    pid_file = log_dir / "watchdog.pid"

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _pid_running(pid):
                return
        except (ValueError, OSError):
            pass

    env = os.environ.copy()
    env["MIHOMES_MONITOR_PROPERTY"] = monitor_property
    try:
        with get_session() as s:
            from mihomes.services.config_service import get_config as _gc
            key = _gc(s, "ai.nim_api_key") or ""
        if key:
            env["NVIDIA_API_KEY"] = key
    except Exception:
        logger.exception("_start_watchdog_now: suppressed exception")

    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        log = open(log_dir / "watchdog.log", "a")
        proc = subprocess.Popen(
            [sys.executable, str(watchdog_script)],
            stdout=log, stderr=log, env=env,
            creationflags=0x08000000, startupinfo=si,
        )
    else:
        log = open(log_dir / "watchdog.log", "a")
        proc = subprocess.Popen(
            [sys.executable, str(watchdog_script)],
            stdout=log, stderr=log, env=env,
        )
    pid_file.write_text(str(proc.pid))
