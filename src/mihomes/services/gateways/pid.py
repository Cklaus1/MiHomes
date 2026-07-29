"""Cross-platform PID liveness helpers, shared by the gateway CLIs + watchdog.

Spec M28. The Windows check used ``str(pid) in tasklist_stdout`` — a substring
match, so PID 123 matched a row for 1234 (or digits in the image name / memory
column), leaving a dead monitor looking alive. `tasklist_has_pid` parses the
``/FO CSV`` output and compares the PID column as an exact integer.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def tasklist_has_pid(stdout: str, pid: int) -> bool:
    """Return True iff a `tasklist /FO CSV` payload has a row whose PID == pid.

    Pure/text-only so it is unit-testable off-Windows. The PID is the second
    CSV column; header and informational lines (e.g. "No tasks are running")
    have no valid integer there and are skipped.
    """
    if not stdout:
        return False
    for row in csv.reader(io.StringIO(stdout)):
        if len(row) < 2:
            continue
        try:
            if int(row[1].strip()) == pid:
                return True
        except (ValueError, IndexError):
            continue  # header row ("PID") or malformed line
    return False


def stop_pid_file(pid_file, is_running, kill):
    """Read a pid file and stop the process it names (spec M31).

    Side effects are injected (``is_running(pid) -> bool``, ``kill(pid)``) so the
    logic is unit-testable off-platform. Returns ``(status, pid)`` where status is
    one of ``"absent"`` (no file), ``"stale"`` (file present but pid dead/garbage
    — file removed), ``"stopped"`` (killed and file removed), or ``"error"``
    (kill raised — file kept so an elevated retry can reap it).
    """
    from pathlib import Path

    pid_file = Path(pid_file)
    if not pid_file.exists():
        return "absent", None
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return "stale", None
    if not is_running(pid):
        pid_file.unlink(missing_ok=True)
        return "stale", pid
    try:
        kill(pid)
    except (ProcessLookupError, PermissionError, OSError):
        return "error", pid
    pid_file.unlink(missing_ok=True)
    return "stopped", pid


def pid_running(pid: int) -> bool:
    """Cross-platform liveness check for a previously recorded PID."""
    if sys.platform == "win32":
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                capture_output=True,
                text=True,
                creationflags=0x08000000,
                startupinfo=si,
            )
            return tasklist_has_pid(r.stdout, pid)
        except Exception:
            logger.exception("pid_running: tasklist check failed")
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
