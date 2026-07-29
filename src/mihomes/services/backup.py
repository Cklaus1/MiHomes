"""Backup service — backup, restore, and integrity checks."""

import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from mihomes.config import BACKUPS_DIR, DB_PATH, MEDIA_DIR, MIHOMES_DIR

# Pid files whose presence means a separate process holds a live SQLite
# connection (watchdog, monitors). Restoring while any of these is alive would
# let that process replay a stale WAL against the restored DB (spec D1/Q7).
_PID_DIR = Path.home() / ".mihomes"
_SERVICE_PID_FILES = ("watchdog.pid", "monitor.pid", "whatsapp_monitor.pid")


def _consistent_db_snapshot(dest: Path) -> None:
    """Copy the live DB to ``dest`` via SQLite's backup API (WAL-safe).

    ``VACUUM INTO`` / ``Connection.backup()`` checkpoint the WAL and produce a
    single consistent file even while the DB is being written — unlike ``cp``,
    which can capture a torn page and omit un-checkpointed WAL contents (D1).
    """
    src = sqlite3.connect(str(DB_PATH))
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


def create_backup(output_path: Path | None = None) -> Path:
    """Create a backup of the database and media directory.

    The DB is snapshotted through SQLite's backup API (checkpointing the WAL)
    before being added to the archive, so recent committed data is never lost
    and a mid-write copy can't produce a torn file (spec D1).
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if output_path is None:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = BACKUPS_DIR / f"mihomes-backup-{timestamp}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(output_path, "w:gz") as tar:
            if DB_PATH.exists():
                snapshot = Path(tmp) / "mihomes.db"
                _consistent_db_snapshot(snapshot)
                tar.add(snapshot, arcname="db/mihomes.db")
            if MEDIA_DIR.exists():
                tar.add(MEDIA_DIR, arcname="media")

    return output_path


def _live_service_pids() -> list[str]:
    """Return the names of service pid files that point at a running process."""
    import os

    live = []
    for name in _SERVICE_PID_FILES:
        pid_file = _PID_DIR / name
        if not pid_file.exists():
            continue
        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            continue
        try:
            os.kill(pid, 0)  # signal 0 = liveness probe, no signal sent
        except ProcessLookupError:
            continue  # stale pid file, process already gone
        except PermissionError:
            live.append(name)  # exists but owned by another user — treat as live
        else:
            live.append(name)
    return live


def restore_backup(backup_path: Path, *, force: bool = False) -> None:
    """Restore from a backup file, safely against WAL-mode SQLite (spec D1/Q7).

    Refuses to run while a watchdog/monitor process is live (unless ``force``),
    because those separate processes each hold a SQLite connection whose stale
    WAL would corrupt the restored DB. Before extracting, the current DB is
    snapshotted so a bad restore is reversible, this process's engine is
    disposed, and the stale ``mihomes.db{,-wal,-shm}`` files are deleted. The
    archive is extracted with ``filter="data"`` to block path traversal from a
    tampered backup.
    """
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    if not force:
        live = _live_service_pids()
        if live:
            services = ", ".join(sorted(live))
            raise RuntimeError(
                f"Refusing to restore while background services are running "
                f"({services}). Stop them first (mihomes telegram stop / "
                f"whatsapp stop) or pass --force."
            )

    from mihomes.db import dispose_engine

    # Snapshot the current DB first so a bad restore is reversible.
    if DB_PATH.exists():
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        pre = BACKUPS_DIR / f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "mihomes.db"
            _consistent_db_snapshot(snapshot)
            with tarfile.open(pre, "w:gz") as tar:
                tar.add(snapshot, arcname="db/mihomes.db")
                if MEDIA_DIR.exists():
                    tar.add(MEDIA_DIR, arcname="media")

    # Release this process's handle and wipe the stale WAL/SHM before extract.
    dispose_engine()
    for suffix in ("", "-wal", "-shm"):
        Path(str(DB_PATH) + suffix).unlink(missing_ok=True)

    with tarfile.open(backup_path, "r:gz") as tar:
        tar.extractall(path=MIHOMES_DIR, filter="data")


def list_backups() -> list[dict]:
    """List available backups."""
    if not BACKUPS_DIR.exists():
        return []
    backups = []
    for f in sorted(BACKUPS_DIR.glob("mihomes-backup-*.tar.gz"), reverse=True):
        backups.append({
            "path": f,
            "name": f.name,
            "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()[:19],
        })
    return backups


def run_doctor() -> list[dict]:
    """Run integrity checks. Returns list of findings."""
    findings = []

    # Check DB exists
    if not DB_PATH.exists():
        findings.append({"level": "error", "message": "Database not found"})
        return findings

    findings.append({"level": "ok", "message": f"Database exists ({DB_PATH.stat().st_size / 1024:.0f} KB)"})

    # Check media dir
    if MEDIA_DIR.exists():
        media_count = sum(1 for _ in MEDIA_DIR.rglob("*") if _.is_file())
        findings.append({"level": "ok", "message": f"Media directory: {media_count} files"})
    else:
        findings.append({"level": "info", "message": "Media directory does not exist (no photos stored)"})

    # Check backup dir
    if BACKUPS_DIR.exists():
        backup_count = len(list(BACKUPS_DIR.glob("*.tar.gz")))
        findings.append({"level": "ok", "message": f"Backups: {backup_count} backup(s) found"})
    else:
        findings.append({"level": "warning", "message": "No backups found. Run 'mihomes backup'"})

    # DB integrity checks
    try:
        from mihomes.db import get_session
        from mihomes.models.property import Property
        from mihomes.models.task import Task, TaskStatus
        from mihomes.models.issue import Issue, IssueStatus
        from mihomes.models.audit_log import AuditLog

        with get_session() as session:
            prop_count = session.query(Property).count()
            task_count = session.query(Task).count()
            issue_count = session.query(Issue).count()
            audit_count = session.query(AuditLog).count()
            findings.append({"level": "ok", "message": f"Entities: {prop_count} properties, {task_count} tasks, {issue_count} issues"})
            findings.append({"level": "ok", "message": f"Audit log: {audit_count} entries"})

            # Check for orphaned tasks (property deleted but task remains)
            from sqlalchemy import and_, not_
            orphan_tasks = session.query(Task).filter(
                ~Task.property_id.in_(session.query(Property.id))
            ).count()
            if orphan_tasks > 0:
                findings.append({"level": "warning", "message": f"Orphaned tasks: {orphan_tasks} task(s) reference deleted properties"})

            # Check for tasks with no slug
            no_slug = session.query(Task).filter(Task.slug == "").count()
            if no_slug > 0:
                findings.append({"level": "warning", "message": f"Empty slugs: {no_slug} task(s) have empty slugs"})

            # FK integrity via PRAGMA
            cursor = session.execute(text("PRAGMA foreign_key_check"))
            fk_violations = cursor.fetchall()
            if fk_violations:
                findings.append({"level": "error", "message": f"FK violations: {len(fk_violations)} foreign key constraint failures"})
            else:
                findings.append({"level": "ok", "message": "Foreign key integrity: OK"})

    except Exception as e:
        findings.append({"level": "error", "message": f"DB check failed: {e}"})

    return findings
