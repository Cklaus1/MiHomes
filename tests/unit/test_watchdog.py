"""Regression tests for the watchdog supervisor (spec D7, D8).

D7: `mihomes whatsapp watchdog` crashed on POSIX because `_start_watchdog_now`
    called Windows-only `subprocess.STARTUPINFO()` unconditionally.
D8: the watchdog reports a dead monitor it spawned as still alive, because it
    discards the `Popen` handle and probes liveness with `os.kill(pid, 0)` —
    which succeeds for a zombie (un-`wait()`ed) child forever. It must reap via
    the retained handle (`proc.poll()`), and must not leak the log file handle.
"""

import subprocess
import sys
import time

import pytest


# --- D7: no unconditional STARTUPINFO on POSIX ------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only crash")
def test_whatsapp_start_watchdog_now_no_startupinfo_on_posix(monkeypatch, tmp_path):
    """_start_watchdog_now must not touch STARTUPINFO on non-Windows."""
    from mihomes.cli import whatsapp as wa

    # Point the pid/log dir at a tmp dir and stub the actual spawn so nothing
    # real is launched — we only care that no AttributeError is raised.
    monkeypatch.setattr(wa, "_LOG_DIR", tmp_path, raising=False)

    launched = {}

    class _FakeProc:
        pid = 4321

    def _fake_popen(*args, **kwargs):
        launched["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    # get_session is called for the NIM key; make it a harmless no-op context.
    import contextlib

    @contextlib.contextmanager
    def _fake_session():
        yield object()

    monkeypatch.setattr(wa, "get_session", _fake_session)
    monkeypatch.setattr(
        "mihomes.services.config_service.get_config", lambda *a, **k: None
    )

    # Pre-fix this raised AttributeError: module 'subprocess' has no attribute
    # 'STARTUPINFO' on Linux/macOS.
    wa._start_watchdog_now(tmp_path / "watchdog.py", "belle-estate")

    assert "startupinfo" not in launched["kwargs"]
    assert "creationflags" not in launched["kwargs"]


# --- D8: a dead spawned monitor must be detected as down --------------------

def test_watchdog_detects_dead_spawned_monitor(monkeypatch, tmp_path):
    """After the monitor it started exits, the watchdog must see it as down.

    We drive the two private helpers directly: start a short-lived child, let
    it exit, and assert the watchdog no longer considers that pid alive. With
    the zombie bug, `os.kill(pid, 0)` keeps returning True because the child is
    never reaped, so this fails pre-fix.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mh_watchdog",
        str(__import__("pathlib").Path(__file__).parents[2] / "scripts" / "watchdog.py"),
    )
    wd = importlib.util.module_from_spec(spec)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path, raising=False)
    spec.loader.exec_module(wd)
    monkeypatch.setattr(wd, "PID_FILE", tmp_path / "monitor.pid")
    monkeypatch.setattr(wd, "LOG_DIR", tmp_path)

    # Spawn a monitor that exits almost immediately (bypass the real command).
    _real_popen = subprocess.Popen
    monkeypatch.setattr(
        wd.subprocess, "Popen",
        lambda *a, **k: _real_popen([sys.executable, "-c", "pass"]),
    )
    pid = wd._start_monitor()

    # Wait for it to actually terminate.
    for _ in range(50):
        if not _os_alive(pid):
            break
        time.sleep(0.05)

    # Give the watchdog a chance to reap; the fix reaps inside liveness checking.
    assert wd._monitor_alive() is False, "dead spawned monitor reported as alive (zombie bug)"


def _os_alive(pid: int) -> bool:
    """True while the process exists in ANY state (including zombie)."""
    import os
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _load_watchdog(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "mh_watchdog_marker",
        str(Path(__file__).parents[2] / "scripts" / "watchdog.py"),
    )
    wd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wd)
    monkeypatch.setattr(wd, "DIGEST_MARKER_FILE", tmp_path / "last_inventory_digest")
    return wd


def test_digest_marker_persists_across_restart(monkeypatch, tmp_path):
    """L10: the weekly inventory digest date must survive a watchdog restart so
    a restart on the same Monday doesn't re-send it. Previously it lived only in
    memory (last_inventory_digest_date = None on every start)."""
    from datetime import date
    wd = _load_watchdog(monkeypatch, tmp_path)

    assert wd._read_digest_marker() is None  # nothing sent yet
    monday = date(2026, 7, 27)
    wd._write_digest_marker(monday)
    # A "restart" re-reads from disk:
    assert wd._read_digest_marker() == monday


def test_digest_marker_absent_file_is_none(monkeypatch, tmp_path):
    wd = _load_watchdog(monkeypatch, tmp_path)
    assert wd._read_digest_marker() is None
