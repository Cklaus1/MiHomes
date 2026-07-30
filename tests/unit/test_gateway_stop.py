"""M31 · Stopping a gateway must reap ALL of its processes, not just some.

`telegram stop` killed only watchdog.pid + monitor.pid, orphaning the WhatsApp
monitor (whatsapp_monitor.pid) and the Node bridge (bridge.pid) that the shared
watchdog had spawned. And there was no `whatsapp stop` at all.

`stop_pid_file` is the reusable, side-effect-injected unit both commands loop
over: it reads a pid file, checks liveness, kills, and cleans up — returning a
status the CLI can report.
"""

from mihomes.services.gateways.pid import stop_pid_file


class _Recorder:
    def __init__(self, running_pids):
        self.running = set(running_pids)
        self.killed = []

    def is_running(self, pid):
        return pid in self.running

    def kill(self, pid):
        if pid not in self.running:
            raise ProcessLookupError(pid)
        self.killed.append(pid)
        self.running.discard(pid)


def _write_pid(tmp_path, name, pid):
    p = tmp_path / name
    p.write_text(str(pid))
    return p


def test_absent_pidfile(tmp_path):
    p = tmp_path / "missing.pid"
    status, pid = stop_pid_file(p, lambda x: True, lambda x: None)
    assert status == "absent" and pid is None


def test_stale_pidfile_is_removed(tmp_path):
    p = _write_pid(tmp_path, "stale.pid", 4242)
    rec = _Recorder(running_pids=set())  # 4242 not running
    status, pid = stop_pid_file(p, rec.is_running, rec.kill)
    assert status == "stale" and pid == 4242
    assert not p.exists(), "stale pid file must be cleaned up"
    assert rec.killed == []


def test_running_pid_is_killed_and_removed(tmp_path):
    p = _write_pid(tmp_path, "live.pid", 1001)
    rec = _Recorder(running_pids={1001})
    status, pid = stop_pid_file(p, rec.is_running, rec.kill)
    assert status == "stopped" and pid == 1001
    assert rec.killed == [1001]
    assert not p.exists()


def test_kill_failure_reports_error(tmp_path):
    p = _write_pid(tmp_path, "prot.pid", 2002)

    def boom(_):
        raise PermissionError("access denied")

    status, pid = stop_pid_file(p, lambda x: True, boom)
    assert status == "error" and pid == 2002
    assert p.exists(), "pid file kept so a follow-up (elevated) stop can retry"


def test_garbage_pidfile_is_removed(tmp_path):
    p = _write_pid(tmp_path, "junk.pid", 0)
    p.write_text("not-a-number")
    status, pid = stop_pid_file(p, lambda x: True, lambda x: None)
    assert status == "stale" and pid is None
    assert not p.exists()
