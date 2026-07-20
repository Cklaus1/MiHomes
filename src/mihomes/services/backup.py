"""Backup service — backup, restore, and integrity checks."""

import shutil
import tarfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from mihomes.config import BACKUPS_DIR, DB_PATH, MEDIA_DIR, MIHOMES_DIR


def create_backup(output_path: Path | None = None) -> Path:
    """Create a backup of the database and media directory."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if output_path is None:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = BACKUPS_DIR / f"mihomes-backup-{timestamp}.tar.gz"

    with tarfile.open(output_path, "w:gz") as tar:
        if DB_PATH.exists():
            tar.add(DB_PATH, arcname="db/mihomes.db")
        if MEDIA_DIR.exists():
            tar.add(MEDIA_DIR, arcname="media")

    return output_path


def restore_backup(backup_path: Path) -> None:
    """Restore from a backup file."""
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    with tarfile.open(backup_path, "r:gz") as tar:
        tar.extractall(path=MIHOMES_DIR)


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
