"""
MiHomes Watchdog — keeps the messaging monitors alive.
Runs as a background process, checks every 60s, restarts a monitor if it dies.
Supervises the Telegram monitor always, and the WhatsApp monitor when
`whatsapp.autostart` is enabled (both gateways can run side by side).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
# L10: make `mihomes` importable once, at import time — the loop previously
# re-ran sys.path.insert(0, …) on every tick, growing sys.path unboundedly.
_SRC = str(PROJECT_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
LOG_DIR = Path(os.path.expanduser("~/.mihomes"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
PID_FILE = LOG_DIR / "monitor.pid"
WA_PID_FILE = LOG_DIR / "whatsapp_monitor.pid"
WATCHDOG_PID_FILE = LOG_DIR / "watchdog.pid"
# L10: persist the last digest date so a Monday restart doesn't re-send the
# weekly inventory digest (it was tracked in-memory only, resetting on restart).
DIGEST_MARKER_FILE = LOG_DIR / "last_inventory_digest"
CHECK_INTERVAL = 60       # seconds between monitor health checks
CALENDAR_SYNC_INTERVAL = 900  # 15 minutes between Google Calendar syncs
INVENTORY_DIGEST_DAY = 0  # Monday (weekday index)
WA_BACKOFF_BASE = CHECK_INTERVAL   # first retry delay after a WhatsApp monitor death
WA_BACKOFF_MAX = 900               # cap WhatsApp restart backoff at 15 min


def _pid_running(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            from mihomes.services.gateways.pid import tasklist_has_pid
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                capture_output=True, text=True,
            )
            return tasklist_has_pid(result.stdout, pid)
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


def _read_digest_marker():
    """Last date the inventory digest was sent, or None (L10 persistence)."""
    from datetime import date as _date
    try:
        return _date.fromisoformat(DIGEST_MARKER_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _write_digest_marker(day) -> None:
    try:
        DIGEST_MARKER_FILE.write_text(day.isoformat())
    except OSError:
        pass


def _hidden_popen_kwargs():
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": 0x08000000, "startupinfo": si}


# Handle to the Telegram monitor we spawned, so we can reap it (poll) instead
# of probing os.kill(pid, 0) — which reports a zombie child as alive forever.
_monitor_proc: "subprocess.Popen | None" = None
_wa_monitor_proc: "subprocess.Popen | None" = None


def _start_monitor():
    global _monitor_proc
    env = os.environ.copy()
    env["MIHOMES_MONITOR"] = "1"

    # Close over the log handle: the child inherits the fd, and the parent's
    # copy is closed on `with` exit, so repeated restarts don't leak fds.
    with open(LOG_DIR / "monitor.log", "a") as monitor_log:
        proc = subprocess.Popen(
            [sys.executable, "-c", "from mihomes.cli import app; app()",
             "telegram", "monitor"],
            env=env,
            cwd=str(PROJECT_ROOT),
            stdout=monitor_log, stderr=monitor_log,
            **_hidden_popen_kwargs(),
        )
    _monitor_proc = proc
    PID_FILE.write_text(str(proc.pid))
    return proc.pid


def _monitor_alive() -> bool:
    """True only if the Telegram monitor we manage is genuinely running.

    Prefers the retained Popen handle (`poll()` reaps a dead child so it never
    lingers as a zombie); falls back to the recorded PID when this watchdog
    instance didn't spawn it (e.g. after its own restart)."""
    if _monitor_proc is not None:
        return _monitor_proc.poll() is None
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return False
        return _pid_running(pid)
    return False


def _wa_monitor_alive() -> bool:
    """WhatsApp counterpart of `_monitor_alive` — reap via the retained handle."""
    if _wa_monitor_proc is not None:
        return _wa_monitor_proc.poll() is None
    if WA_PID_FILE.exists():
        try:
            wa_pid = int(WA_PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return False
        return _pid_running(wa_pid)
    return False


def _whatsapp_autostart_enabled() -> bool:
    """True only when the operator has opted WhatsApp into supervision."""
    try:
        from mihomes.db import get_session
        from mihomes.services.config_service import get_config
        with get_session() as session:
            return get_config(session, "whatsapp.autostart") == "true"
    except Exception:
        return False


def _whatsapp_bridge_running() -> bool:
    """The WhatsApp monitor exits immediately if the Baileys bridge is down,
    so check the bridge before (re)starting it to avoid a restart hot-loop."""
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:7867/status", timeout=2)
        return True
    except Exception:
        return False


def _start_whatsapp_monitor():
    global _wa_monitor_proc
    env = os.environ.copy()
    env["MIHOMES_MONITOR"] = "1"

    with open(LOG_DIR / "whatsapp-monitor.log", "a") as wa_log:
        proc = subprocess.Popen(
            [sys.executable, "-c", "from mihomes.cli import app; app()",
             "whatsapp", "monitor"],
            env=env,
            cwd=str(PROJECT_ROOT),
            stdout=wa_log, stderr=wa_log,
            **_hidden_popen_kwargs(),
        )
    _wa_monitor_proc = proc
    WA_PID_FILE.write_text(str(proc.pid))
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
    # Seed from the persisted marker so a restart on the same Monday is a no-op.
    last_inventory_digest_date = _read_digest_marker()
    wa_backoff = WA_BACKOFF_BASE   # current WhatsApp restart backoff (seconds)
    wa_retry_at = 0.0              # earliest monotonic time to retry WhatsApp

    while True:
        try:
            # --- Check Telegram monitor ---
            if not _monitor_alive():
                _log("Telegram monitor down — restarting...")
                new_pid = _start_monitor()
                _log(f"Telegram monitor started (PID {new_pid})")

            # --- Check WhatsApp monitor (only when opted in) ---
            if _whatsapp_autostart_enabled():
                wa_up = _wa_monitor_alive()

                if wa_up:
                    wa_backoff = WA_BACKOFF_BASE   # healthy — reset backoff
                elif time.monotonic() >= wa_retry_at:
                    if not _whatsapp_bridge_running():
                        # Bridge down: the monitor would exit instantly. Back off
                        # instead of hot-looping; the bridge is supervised elsewhere.
                        _log(f"WhatsApp bridge unreachable — deferring monitor restart {wa_backoff}s")
                        wa_retry_at = time.monotonic() + wa_backoff
                        wa_backoff = min(wa_backoff * 2, WA_BACKOFF_MAX)
                    else:
                        _log("WhatsApp monitor down — restarting...")
                        new_wa_pid = _start_whatsapp_monitor()
                        _log(f"WhatsApp monitor started (PID {new_wa_pid})")
                        # If it dies again before next tick, apply backoff.
                        wa_retry_at = time.monotonic() + wa_backoff
                        wa_backoff = min(wa_backoff * 2, WA_BACKOFF_MAX)

            # --- Google Calendar sync (every 15 min) ---
            now = time.time()
            if now - last_calendar_sync >= CALENDAR_SYNC_INTERVAL:
                try:
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
                    _write_digest_marker(today)
                except Exception as e:
                    _log(f"Inventory digest failed: {e}")
                    last_inventory_digest_date = today
                    _write_digest_marker(today)

        except Exception as e:
            _log(f"Watchdog error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
