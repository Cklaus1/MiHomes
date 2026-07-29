"""Backup/restore hardening regression tests (spec D1 / Q7).

The old backup tarred only ``mihomes.db`` with no WAL checkpoint, so committed
data still sitting in ``mihomes.db-wal`` was silently omitted; and restore did a
raw ``extractall`` over the live DB without disposing the engine, deleting the
stale WAL, or guarding against path traversal or live service processes.

These tests pin the fixed behaviour:

* WAL-resident committed data survives a backup→restore round-trip,
* restore is refused while a service pid file points at a live process,
* ``--force`` overrides that refusal,
* a tampered archive with an absolute/traversal member can't escape the data dir.
"""

import os
import sqlite3
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def estate(tmp_path, monkeypatch):
    """Isolate MIHOMES_DIR at a temp dir and give a fresh WAL-mode DB + module set."""
    home = tmp_path / "home"
    home.mkdir()
    mihomes_dir = tmp_path / "estate"
    monkeypatch.setenv("MIHOMES_DIR", str(mihomes_dir))
    # Pin pid-file discovery + snapshot dirs at the isolated tree.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    # Reload config + db + backup so the patched env/home takes effect.
    import importlib

    import mihomes.config as config
    importlib.reload(config)
    import mihomes.db as db
    importlib.reload(db)
    import mihomes.services.backup as backup
    importlib.reload(backup)

    config.ensure_dirs()
    # Build a real WAL-mode DB with one table + row, leaving data in the WAL.
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO note (body) VALUES ('wal-resident-secret')")
    conn.commit()
    conn.close()

    yield config, db, backup, home

    # Reload back to the real environment for other tests. monkeypatch's env/attr
    # undo is LIFO and would otherwise run *after* this teardown, so the reloads
    # below would re-pin the module globals to the (about-to-be-deleted) temp dir.
    # Undo the patches first so the modules reload against the true environment.
    monkeypatch.undo()
    importlib.reload(config)
    importlib.reload(db)
    importlib.reload(backup)


def _read_note(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return [r[0] for r in conn.execute("SELECT body FROM note")]
    finally:
        conn.close()


def test_wal_data_survives_backup_restore(estate):
    config, db, backup, _home = estate
    archive = backup.create_backup()

    # Clobber the live DB to prove the restore actually rewrites it.
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.execute("DELETE FROM note")
    conn.commit()
    conn.close()
    assert _read_note(config.DB_PATH) == []

    backup.restore_backup(archive)
    assert _read_note(config.DB_PATH) == ["wal-resident-secret"]


def test_restore_refused_while_service_live(estate):
    config, db, backup, home = estate
    archive = backup.create_backup()

    # Write a watchdog pid file pointing at *this* (definitely live) process.
    pid_file = home / ".mihomes" / "watchdog.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    with pytest.raises(RuntimeError, match="Refusing to restore"):
        backup.restore_backup(archive)


def test_force_overrides_live_service(estate):
    config, db, backup, home = estate
    archive = backup.create_backup()
    pid_file = home / ".mihomes" / "watchdog.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    # Should not raise with force=True.
    backup.restore_backup(archive, force=True)
    assert _read_note(config.DB_PATH) == ["wal-resident-secret"]


def test_stale_pid_file_does_not_block_restore(estate):
    config, db, backup, home = estate
    archive = backup.create_backup()
    pid_file = home / ".mihomes" / "monitor.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    # A pid that is essentially guaranteed not to exist.
    pid_file.write_text("2147480000")

    backup.restore_backup(archive)  # stale pid → not live → allowed
    assert _read_note(config.DB_PATH) == ["wal-resident-secret"]


def test_traversal_archive_rejected(estate):
    config, db, backup, _home = estate
    # Craft a malicious archive with an absolute-path member.
    evil = config.BACKUPS_DIR / "evil.tar.gz"
    payload = config.BACKUPS_DIR / "payload.txt"
    payload.write_text("pwned")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../../../../tmp/mihomes-escape.txt")

    with pytest.raises(Exception):  # tarfile raises OutsideDestinationError under filter="data"
        backup.restore_backup(evil)
    assert not Path("/tmp/mihomes-escape.txt").exists()
