"""
MiHomes Watchdog — keeps the WhatsApp bridge and monitor alive.
Runs as a background process, checks every 60s, restarts anything that died.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
BRIDGE_DIR = PROJECT_ROOT / "bridge"
LOG_DIR = Path(os.path.expanduser("~/.mihomes"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
PID_FILE = LOG_DIR / "monitor.pid"
WATCHDOG_PID_FILE = LOG_DIR / "watchdog.pid"
CHECK_INTERVAL = 60       # seconds between bridge/monitor health checks
CALENDAR_SYNC_INTERVAL = 900  # 15 minutes between Google Calendar syncs


def _pid_running(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
            capture_output=True, text=True,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def _bridge_running() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:7867/status", timeout=2)
        return True
    except Exception:
        return False


def _start_bridge():
    log = open(LOG_DIR / "bridge.log", "a")
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    subprocess.Popen(
        [npm_cmd, "start"],
        cwd=str(BRIDGE_DIR),
        stdout=log, stderr=log,
        creationflags=subprocess.DETACHED_PROCESS | 0x08000000,
        close_fds=True,
    )
    # Wait up to 30s for bridge to come up
    for _ in range(15):
        time.sleep(2)
        if _bridge_running():
            return True
    return False


def _start_monitor():
    # Resolve NVIDIA key from config DB
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
    if not nvidia_key:
        try:
            result = subprocess.run(
                [sys.executable, "-c",
                 "from mihomes.db import get_session; from mihomes.services.config_service import get_config\n"
                 "with get_session() as s: print(get_config(s, 'ai.nim_api_key') or '', end='')"],
                capture_output=True, text=True, cwd=str(PROJECT_ROOT),
            )
            nvidia_key = result.stdout.strip()
        except Exception:
            pass

    monitor_property = "belle-estate"
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "from mihomes.db import get_session; from mihomes.services.config_service import get_config\n"
             "with get_session() as s: print(get_config(s, 'whatsapp.monitor_property') or 'belle-estate', end='')"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        monitor_property = result.stdout.strip() or "belle-estate"
    except Exception:
        pass

    env = os.environ.copy()
    if nvidia_key:
        env["NVIDIA_API_KEY"] = nvidia_key
    env["MIHOMES_MONITOR"] = "1"

    monitor_log = open(LOG_DIR / "monitor.log", "a")
    proc = subprocess.Popen(
        [sys.executable, "-c", "from mihomes.cli import app; app()",
         "whatsapp", "monitor", "--property", monitor_property],
        env=env,
        cwd=str(PROJECT_ROOT),
        stdout=monitor_log, stderr=monitor_log,
        creationflags=subprocess.DETACHED_PROCESS | 0x08000000,
        close_fds=True,
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

    while True:
        try:
            # --- Check bridge ---
            if not _bridge_running():
                _log("Bridge down — restarting...")
                if _start_bridge():
                    _log("Bridge restarted")
                else:
                    _log("Bridge failed to start — will retry next cycle")

            # --- Check monitor ---
            monitor_up = False
            if PID_FILE.exists():
                try:
                    pid = int(PID_FILE.read_text().strip())
                    monitor_up = _pid_running(pid)
                except (ValueError, OSError):
                    pass

            if not monitor_up:
                if PID_FILE.exists():
                    _log("Monitor down — restarting...")
                else:
                    _log("Monitor not started — starting...")
                new_pid = _start_monitor()
                _log(f"Monitor started (PID {new_pid})")

            # --- Google Calendar sync (every 15 min) ---
            now = time.time()
            if now - last_calendar_sync >= CALENDAR_SYNC_INTERVAL:
                try:
                    sys.path.insert(0, str(PROJECT_ROOT / "src"))
                    from mihomes.db import get_session
                    from mihomes.services.calendar_sync import auto_sync
                    with get_session() as session:
                        result = auto_sync(session)
                    if result["pushed"] or result["pulled"]:
                        _log(f"Calendar sync — pushed: {result['pushed']}, pulled: {result['pulled']}")
                    for err in result.get("errors", []):
                        _log(f"Calendar sync error: {err}")
                    last_calendar_sync = now
                except Exception as e:
                    _log(f"Calendar sync failed: {e}")
                    last_calendar_sync = now  # Still update to avoid tight retry loop

        except Exception as e:
            _log(f"Watchdog error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
