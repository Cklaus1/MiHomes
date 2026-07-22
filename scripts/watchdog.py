"""
MiHomes Watchdog — keeps the Telegram monitor alive.
Runs as a background process, checks every 60s, restarts the monitor if it dies.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
LOG_DIR = Path(os.path.expanduser("~/.mihomes"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
PID_FILE = LOG_DIR / "monitor.pid"
WATCHDOG_PID_FILE = LOG_DIR / "watchdog.pid"
CHECK_INTERVAL = 60       # seconds between monitor health checks
CALENDAR_SYNC_INTERVAL = 900  # 15 minutes between Google Calendar syncs
INVENTORY_DIGEST_DAY = 0  # Monday (weekday index)


def _is_zombie(pid: int) -> bool:
    """True if the pid exists but is a reaped-pending zombie (Linux only).

    os.kill(pid, 0) succeeds for zombies because they still occupy a slot in
    the process table, so a dead-but-unreaped monitor would look alive. Parse
    /proc/<pid>/status to tell a real process from a defunct one.
    """
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    # e.g. "State:\tZ (zombie)" -> token "Z"
                    return line.split(":", 1)[1].strip().startswith("Z")
    except Exception:
        pass
    return False


def _pid_running(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                capture_output=True, text=True,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists but owned by another user — treat as running
    except Exception:
        return False
    return not _is_zombie(pid)


def _bot_reachable() -> bool:
    """Quick health check — verify the bot token is valid and Telegram API is reachable."""
    try:
        import urllib.request
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from mihomes.db import get_session
        from mihomes.services.config_service import get_config
        with get_session() as session:
            token = get_config(session, "telegram.bot_token")
        if not token:
            return False
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return data.get("ok", False)
    except Exception:
        return False


def _hidden_popen_kwargs():
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": 0x08000000, "startupinfo": si}


def _start_monitor():
    env = os.environ.copy()
    env["MIHOMES_MONITOR"] = "1"

    monitor_log = open(LOG_DIR / "monitor.log", "a")
    proc = subprocess.Popen(
        [sys.executable, "-c", "from mihomes.cli import app; app()",
         "telegram", "monitor"],
        env=env,
        cwd=str(PROJECT_ROOT),
        stdout=monitor_log, stderr=monitor_log,
        **_hidden_popen_kwargs(),
    )
    PID_FILE.write_text(str(proc.pid))
    return proc.pid


def run():
    WATCHDOG_PID_FILE.write_text(str(os.getpid()))
    log = open(LOG_DIR / "watchdog.log", "a")

    def _log(msg):
        from datetime import datetime
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line, file=log, flush=True)

    _log("Watchdog started")

    last_calendar_sync = 0
    last_inventory_digest_date = None

    while True:
        try:
            # --- Check Telegram monitor ---
            monitor_up = False
            if PID_FILE.exists():
                try:
                    pid = int(PID_FILE.read_text().strip())
                    monitor_up = _pid_running(pid)
                except (ValueError, OSError):
                    pass

            if not monitor_up:
                _log("Telegram monitor down — restarting...")
                new_pid = _start_monitor()
                _log(f"Telegram monitor started (PID {new_pid})")

            # --- Google Calendar sync (every 15 min) ---
            now = time.time()
            if now - last_calendar_sync >= CALENDAR_SYNC_INTERVAL:
                try:
                    sys.path.insert(0, str(PROJECT_ROOT / "src"))
                    from mihomes.db import get_session
                    from mihomes.services.calendar_sync import auto_sync
                    with get_session() as session:
                        result = auto_sync(session)
                    if result["pushed"] or result["pulled"] or result.get("tasks_created"):
                        _log(f"Calendar sync — pushed: {result['pushed']}, pulled: {result['pulled']}, tasks: {result.get('tasks_created', 0)}")
                    for err in result.get("errors", []):
                        _log(f"Calendar sync error: {err}")
                    last_calendar_sync = now
                except Exception as e:
                    _log(f"Calendar sync failed: {e}")
                    last_calendar_sync = now

            # --- Weekly inventory digest (Monday morning) ---
            from datetime import date as _date
            today = _date.today()
            if (today.weekday() == INVENTORY_DIGEST_DAY
                    and last_inventory_digest_date != today):
                try:
                    sys.path.insert(0, str(PROJECT_ROOT / "src"))
                    from mihomes.db import get_session
                    from mihomes.services.config_service import get_config
                    from mihomes.services.consumable import get_reorder_list
                    from mihomes.services.gateways.telegram.client import TelegramClient

                    with get_session() as session:
                        token = get_config(session, "telegram.bot_token")
                        owner_chat_id = get_config(session, "telegram.owner_chat_id")
                        items = get_reorder_list(session)

                    if token and owner_chat_id and items:
                        lines = ["Weekly Order List"]
                        for item in items:
                            stock_str = f"{item.quantity_in_stock} {item.unit or ''}".strip() if item.quantity_in_stock is not None else ""
                            order_str = f"order {item.quantity_to_order} {item.unit or ''}".strip() if item.quantity_to_order else ""
                            detail = " — ".join(filter(None, [stock_str and f"in stock: {stock_str}", order_str, item.status.value]))
                            lines.append(f"• {item.name} ({item.property.name}){' — ' + detail if detail else ''}")
                        client = TelegramClient(token)
                        client.send_message(owner_chat_id, "\n".join(lines))
                        _log(f"Inventory digest sent to owner ({len(items)} items)")
                    elif token and owner_chat_id and not items:
                        _log("Inventory digest: nothing to reorder this week")
                    else:
                        _log("Inventory digest skipped: telegram.bot_token or telegram.owner_chat_id not configured")

                    last_inventory_digest_date = today
                except Exception as e:
                    _log(f"Inventory digest failed: {e}")
                    last_inventory_digest_date = today

        except Exception as e:
            _log(f"Watchdog error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
