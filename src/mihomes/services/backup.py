"""Backup service — media backup/restore and integrity checks (G14 · §6 Step 14).

**Database backups are the managed Postgres vendor's responsibility (D13).** Writing our own
would be a second, unmonitored backup system competing with the vendor's PITR — worse than none,
because it invites the belief that "backups are handled" when nobody is watching ours.
`create_backup`/`restore_backup` therefore touch **media only** (`MULTITENANCY.md` §11.1).

**Objects are read and written through `StorageProvider.get`/`put`, keyed by `Document.file_path`
— never by walking a filesystem root.** `build_key()` has exactly one caller
(`web/forms.py:_store_bytes`), so every stored object is reachable from a `Document` row, and
`Document` is the only place a key is recorded (verified by grep, not assumed — see
`opportunities.md` if that ever stops being true). Enumerating `Document` and fetching each key
therefore backs up **every** object regardless of whether the configured backend is the
filesystem or S3, with no branch per backend and no need for `StorageProvider` to grow a `list()`
it was deliberately built without (G11). An earlier draft of this rewrite tarred the filesystem
backend's root directly and refused outright on S3 — that would have shipped hosted deployments,
the only ones D13 actually applies to, with **no media backup at all**, against §11.1's "required
regardless of this decision."

Objects that exist in storage with no `Document` row pointing at them are excluded on purpose:
G16's import ordering guarantees such an object is garbage, never a dangling reference, so it is
correctly not anyone's data to back up.

**Scoped to the bound account, like every other ops command (G13).** A backup taken as
`--account a` contains only `a`'s documents; there is no whole-install mode. `doctor`'s output
says so explicitly, for the same reason A18 exists: a per-tenant "OK" read as install-wide would
be a false pass.

**The archive itself still lands on local disk (`BACKUPS_DIR`)**, exactly as the SQLite-era tool
did. Moving it off-box — to the operator's machine, another bucket, cold storage — is an
operational step this tool does not take for you, and on a Fly machine with no persistent volume
that file does not survive a redeploy. Recorded as a gap, not solved here: building an
S3-to-second-bucket sync for a bucket nobody has provisioned would be exactly the "second
unmonitored backup system" D13 warns against, just moved from the database to the media half.
"""

from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime
from pathlib import Path

# `import mihomes.config as config`, never `from mihomes.config import BACKUPS_DIR` — the latter
# binds the path BY VALUE at import time, so a test (or anything) that repoints `MIHOMES_DIR`
# afterwards changes nothing here. lessons.md already records this exact trap for `DB_DIR`/
# `DB_URL`, and `storage/__init__.py` restates it for `MEDIA_DIR`; the original version of this
# module made the identical mistake for `BACKUPS_DIR`, invisible until two test files sharing one
# process (only the first to import `mihomes.config` controls the real path) collided over it.
import mihomes.config as config
from mihomes.storage import ObjectNotFound, StorageError, get_storage, is_storage_key

# Pid files whose presence means a separate process holds a live connection to storage
# (watchdog, monitors). They cannot collide on a *key* — keys are opaque and assigned once — but
# refusing while one is live keeps the same operational habit the SQLite-era guard established:
# stop background writers before an operator-initiated restore.
_PID_DIR = Path.home() / ".mihomes"
_SERVICE_PID_FILES = ("watchdog.pid", "monitor.pid", "whatsapp_monitor.pid")

RPO_HOURS = 24  # D14's operative commitment ("automated daily backups") until a provider SLA
# sets a real number — see MULTITENANCY.md §11.1.

MANIFEST_NAME = "manifest.json"
_OBJECTS_PREFIX = "objects/"


def _document_keys(session) -> list[tuple[str, str]]:
    """(document id, storage key) for every document backed by a real object.

    Filtered through `is_storage_key()`: a manually-entered `file_path` (the free-text "—"
    fallback in `web/routes/documents.py`, or a pre-SPEC-002 `/static/uploads/...` row) is not a
    key any `StorageProvider` can `get()`.
    """
    from mihomes.models.document import Document

    rows = session.query(Document.id, Document.file_path).all()
    return [(str(doc_id), key) for doc_id, key in rows if is_storage_key(key)]


def create_backup(output_path: Path | None = None) -> Path:
    """Archive this account's documents, fetched through `StorageProvider.get` (media-only; D13).

    A `manifest.json` (document id -> key) rides alongside the object bytes, so `restore_backup`
    knows what it is putting back without touching the database — the row and the object round-
    trip independently, through Postgres and this archive respectively.
    """
    from mihomes.db import get_session

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if output_path is None:
        config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = config.BACKUPS_DIR / f"mihomes-media-backup-{timestamp}.tar.gz"

    storage = get_storage()
    with get_session() as session:
        keys = _document_keys(session)

    manifest = []
    with tarfile.open(output_path, "w:gz") as tar:
        for doc_id, key in keys:
            try:
                data = storage.get(key)
            except ObjectNotFound:
                # A row survived without its object — a pre-existing integrity problem that
                # `run_doctor`'s storage check surfaces. Not this command's job to paper over.
                continue
            info = tarfile.TarInfo(name=_OBJECTS_PREFIX + key)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            manifest.append({"document_id": doc_id, "key": key})

        manifest_bytes = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo(name=MANIFEST_NAME)
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

    return output_path


def _live_service_pids() -> list[str]:
    """Return the names of service pid files that point at a running process.

    **`except PermissionError` before the broader `except OSError`, not `ProcessLookupError`
    alone.** POSIX raises `ProcessLookupError` (an `OSError` subclass) for a stale pid, and this
    file only ever ran there until this check was exercised on Windows: a pid with no matching
    process raises a plain `OSError` (`WinError 87`, "the parameter is incorrect"), not
    `ProcessLookupError`. `PermissionError` is also an `OSError` subclass, so it has to be caught
    first or "exists but access denied" would be swallowed by the broader clause below and
    misread as "gone."
    """
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
        except PermissionError:
            live.append(name)  # exists but owned by another user — treat as live
        except OSError:
            continue  # no such process — POSIX ESRCH, or Windows' invalid-parameter error
        else:
            live.append(name)
    return live


def restore_backup(backup_path: Path, *, force: bool = False) -> int:
    """Put every archived object back at its original key. Returns the count restored.

    Refuses to run while a watchdog/monitor process is live (unless `force`) — see the module-
    level pid comment. Reads the archive with `tarfile.extractfile()` member-by-member rather
    than `extractall()`, because the destination is object storage, not a directory this archive
    could traverse into: each member's name becomes a storage *key*, and a tampered key is
    rejected by the provider itself (`FilesystemStorage` refuses one that resolves outside its
    root; an S3 key has no such notion of "outside the bucket" to escape).
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

    storage = get_storage()
    restored = 0
    with tarfile.open(backup_path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.startswith(_OBJECTS_PREFIX):
                continue
            key = member.name[len(_OBJECTS_PREFIX):]
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            storage.put(key, extracted.read())
            restored += 1
    return restored


def list_backups() -> list[dict]:
    """List available local backup archives."""
    if not config.BACKUPS_DIR.exists():
        return []
    backups = []
    for f in sorted(config.BACKUPS_DIR.glob("mihomes-media-backup-*.tar.gz"), reverse=True):
        backups.append({
            "path": f,
            "name": f.name,
            "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()[:19],
        })
    return backups


def _check_database(session) -> list[dict]:
    from mihomes.models.audit_log import AuditLog
    from mihomes.models.issue import Issue
    from mihomes.models.property import Property
    from mihomes.models.task import Task

    findings = []
    prop_count = session.query(Property).count()
    task_count = session.query(Task).count()
    issue_count = session.query(Issue).count()
    audit_count = session.query(AuditLog).count()
    findings.append({
        "level": "ok",
        "message": f"Database reachable: {prop_count} properties, {task_count} tasks, "
                   f"{issue_count} issues",
    })
    findings.append({"level": "ok", "message": f"Audit log: {audit_count} entries"})

    # No `PRAGMA foreign_key_check` here — that was SQLite-only and always meaningless on
    # Postgres, whose FK constraints are enforced at write time, not checked after the fact.
    #
    # `Task.property_id` carries a real `ForeignKey("properties.id")` under `0001_pg_baseline`,
    # so the query below cannot find anything through any normal write path — the constraint
    # already refuses the delete that would create one. It stays as a cheap sanity net against
    # the one thing a foreign key cannot stop: a direct database edit that disables or bypasses
    # constraint checking. Cheap to run, and it costs nothing to be right twice.
    orphan_tasks = session.query(Task).filter(
        ~Task.property_id.in_(session.query(Property.id))
    ).count()
    if orphan_tasks > 0:
        findings.append({
            "level": "warning",
            "message": f"Orphaned tasks: {orphan_tasks} task(s) reference deleted properties",
        })

    no_slug = session.query(Task).filter(Task.slug == "").count()
    if no_slug > 0:
        findings.append({
            "level": "warning",
            "message": f"Empty slugs: {no_slug} task(s) have empty slugs",
        })

    return findings


def _check_storage(session) -> list[dict]:
    """Confirm every document's storage key actually resolves — not a directory listing.

    `StorageProvider` deliberately has no `list()` (G11), so "does storage have everything it
    should" is asked the other way around: for each `Document` row, does its key exist? This is
    backend-agnostic (same code for filesystem and S3) and catches the failure that matters — a
    row pointing at nothing — where a raw file count would say nothing about correctness.
    """
    keys = _document_keys(session)
    if not keys:
        return [{"level": "info", "message": "Media: no documents with stored files"}]

    storage = get_storage()
    missing = 0
    for _doc_id, key in keys:
        try:
            if not storage.exists(key):
                missing += 1
        except StorageError as e:
            return [{"level": "error", "message": f"Media storage unreachable: {e}"}]

    if missing:
        return [{
            "level": "error",
            "message": f"Media: {missing}/{len(keys)} document(s) point at a missing object",
        }]
    return [{"level": "ok", "message": f"Media: {len(keys)} document(s), all objects present"}]


def _check_backup_freshness() -> list[dict]:
    """Flag a media backup older than the RPO window (D14), or missing entirely.

    Checks **our own** media backups — the thing this codebase actually produces. Step 14 also
    asks for a check that the *managed Postgres provider's* last backup is within the RPO window;
    that would need that provider's API, and no vendor is pinned in code (D13 leaves the choice as
    "an implementation detail"). Not faked here — recorded in `opportunities.md` instead, the same
    treatment G12 gave A17's bootstrap tension.
    """
    backups = list_backups()
    if not backups:
        return [{"level": "warning", "message": "No media backups found. Run 'mihomes backup'"}]

    newest = max(b["path"].stat().st_mtime for b in backups)
    age_hours = (datetime.now().timestamp() - newest) / 3600
    if age_hours > RPO_HOURS:
        return [{
            "level": "error",
            "message": f"Media backups: stale — newest is {age_hours:.0f}h old "
                       f"(RPO {RPO_HOURS}h)",
        }]
    return [{
        "level": "ok",
        "message": f"Media backups: {len(backups)}, newest {age_hours:.1f}h old",
    }]


def run_doctor() -> list[dict]:
    """Run integrity checks for the bound account (G14 · A18).

    Rewritten against Postgres + `StorageProvider` (was written for a single local SQLite file
    and a local media directory, which produced a false "Database not found" the moment either
    moved off this filesystem — G14.3). Every check below asks the *service* it depends on, never
    a hardcoded path.

    **Scoped to one account.** An operator on a multi-tenant host reading a clean run as "the
    install is healthy" would be exactly the false pass A18 exists to prevent, so that scope is
    the first line of output rather than left implicit.
    """
    from mihomes.db import get_session
    from mihomes.tenancy import require_account

    findings = [{
        "level": "info",
        "message": f"Scope: account {require_account()} only — not a whole-install check",
    }]

    try:
        with get_session() as session:
            findings.extend(_check_database(session))
            findings.extend(_check_storage(session))
    except Exception as e:
        findings.append({"level": "error", "message": f"Database check failed: {e}"})
        return findings

    findings.extend(_check_backup_freshness())
    return findings
