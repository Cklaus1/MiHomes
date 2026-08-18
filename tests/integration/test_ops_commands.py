"""`mihomes backup` / `mihomes doctor` against Postgres + `StorageProvider` (G14 · §6 Step 14).

Replaces `test_backup.py`, whose entire premise — `sqlite3.connect(DB_PATH)`, WAL-checkpoint
snapshotting, `extractall()` over a live SQLite file — no longer exists in the code: `backup.py`
is media-only now (D13), and `doctor` no longer assumes a local DB file or a local media
directory (A18, launch gate S3).

A **private** Postgres database, not the five-module `cli_database` (conftest): those modules
share a session-scoped install with demo data loaded, and this file's assertions ("exactly one
document", "backups: none yet") would be false the moment they run in the same database.
"""

from __future__ import annotations

import ast
import inspect
import os
import tarfile
import tempfile
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from typer.testing import CliRunner

# Override MIHOMES_DIR before importing anything — mihomes.config binds its paths at import
# time (the same trap storage/__init__.py documents for MEDIA_DIR).
_test_dir = tempfile.mkdtemp()
os.environ["MIHOMES_DIR"] = _test_dir

from mihomes.cli import app  # noqa: E402
from mihomes.models.document import DocumentType  # noqa: E402
from mihomes.services import backup as backup_svc  # noqa: E402
from mihomes.services.document import create_document  # noqa: E402
from mihomes.storage import ObjectNotFound, build_key, get_storage, reset_storage  # noqa: E402
from mihomes.tenancy import account_context  # noqa: E402

runner = CliRunner()

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL unset — Postgres-only suite (conventions §0).",
)


@pytest.fixture(scope="module")
def ops_db():
    """A private Postgres database for this module, initialised via the real `init_db()`.

    Session-scoped `cli_database` is shared by five other modules with demo data already loaded
    — sharing it here would make "no backups yet"/"one document" assertions false depending on
    test order across the whole run.
    """
    import mihomes.db as db_mod

    url = make_url(TEST_DATABASE_URL)
    ops_database = f"{url.database}_ops"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as conn:
        conn.exec_driver_sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{ops_database}' AND pid <> pg_backend_pid()"
        )
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{ops_database}"')
        conn.exec_driver_sql(f'CREATE DATABASE "{ops_database}"')

    ops_url = str(url.set(database=ops_database))
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = ops_url
    db_mod.dispose_engine()

    from mihomes.db import init_db
    init_db()

    yield ops_url

    db_mod.dispose_engine()
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    with admin.connect() as conn:
        conn.exec_driver_sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{ops_database}' AND pid <> pg_backend_pid()"
        )
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{ops_database}"')
    admin.dispose()


@pytest.fixture
def account_id(ops_db):
    """The single account `init_db()` bootstrapped — every CLI command binds to it implicitly."""
    from mihomes.db import get_session
    from mihomes.models.account import Account

    with get_session() as session:
        return session.query(Account).one().id


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    """Point the filesystem storage backend at a throwaway root for this test."""
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
    reset_storage()
    root = tmp_path / "objects"
    get_storage(refresh=True, override_root=root)
    yield root
    reset_storage()


@pytest.fixture(autouse=True)
def _isolate_backups_dir(tmp_path, monkeypatch):
    """Point `backup.py` at a throwaway `BACKUPS_DIR`, one per test.

    `mihomes.config` is imported once per process — the first test **file** pytest collects wins
    `MIHOMES_DIR`, and every other file's own `os.environ["MIHOMES_DIR"] = tempfile.mkdtemp()`
    (this file's included) is then a no-op, because `config` was already bound. Sharing one real
    `BACKUPS_DIR` with `test_cli.py`/`test_demo_boot.py` made "no backups yet"/"newest backup is
    stale" order-dependent — this file passed alone and failed inside the full suite. Patching
    `config.BACKUPS_DIR` directly (backup.py reads it live via `import mihomes.config as config`,
    never `from mihomes.config import BACKUPS_DIR` — see backup.py's own comment on that trap)
    sidesteps the shared global instead of fighting collection order.
    """
    import mihomes.config as config

    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")


@pytest.fixture(autouse=True)
def _clean_documents_between_tests(ops_db):
    """Isolate the module-scoped database between tests.

    `run_doctor`/`create_backup` each open their own session via `mihomes.db.get_session()`, so
    there is no single connection this file could wrap in a rollback the way `conftest.py`'s
    `session` fixture does — the isolation has to be a real cleanup instead. Raw SQL, not the ORM:
    a plain `DELETE` needs no tenant context and no listener to fire correctly.
    """
    from sqlalchemy import text

    from mihomes.db import get_session

    yield
    with get_session() as session:
        session.execute(text("DELETE FROM documents"))


def _make_document(account, storage, *, with_object: bool = True) -> tuple:
    """Create a `Document` row and, unless told otherwise, the object it points at."""
    from mihomes.db import get_session

    key = build_key(account, "documents", "note.pdf")
    if with_object:
        storage.put(key, b"%PDF-1.4 test contents")
    # `account_context` must be the OUTER manager: `with A, B` exits B (innermost) before A, and
    # `get_session()`'s exit is where the commit/flush happens — the G8.3 `before_flush` listener
    # that stamps `account_id` needs the ContextVar still bound at that point, not already reset.
    with account_context(account), get_session() as session:
        doc = create_document(session, "Note", key, DocumentType.OTHER)
        doc_id = doc.id
    return doc_id, key


# --- A18: no filesystem assumptions -----------------------------------------------------

def test_backup_module_no_longer_imports_filesystem_paths():
    """Structural regression guard (not source-text matching — see lessons.md): `backup.py`
    dropped `DB_PATH`/`MIHOMES_DIR` entirely. Walks the AST rather than grepping so a docstring
    mentioning either name (as this module's own does, explaining why) cannot trip it — the same
    class of bug the `skip_tenant` guard hit twice before (G6.3, G10, G12)."""
    tree = ast.parse(inspect.getsource(backup_svc))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "mihomes.config":
            imported_names.update(alias.name for alias in node.names)
    assert "DB_PATH" not in imported_names
    assert "MIHOMES_DIR" not in imported_names


def test_doctor_no_filesystem_assumptions(ops_db, account_id, storage_root):
    """A18 — the named criterion. No local `mihomes.db` file exists (Postgres via
    `DATABASE_URL`) and no legacy `MEDIA_DIR` directory has been touched, yet `doctor` reports a
    clean, error-free run rather than the old false "Database not found"."""
    with account_context(account_id):
        findings = backup_svc.run_doctor()

    assert not [f for f in findings if f["level"] == "error"]
    messages = " | ".join(f["message"] for f in findings)
    assert "Database not found" not in messages
    assert "Database reachable" in messages


def test_doctor_scope_is_stated_as_per_account(ops_db, account_id, storage_root):
    """A clean run on one account must not read as 'the install is healthy' on a multi-tenant
    host — the false pass A18 exists to prevent, one level up. See backup.py's `run_doctor`."""
    with account_context(account_id):
        findings = backup_svc.run_doctor()
    assert str(account_id) in findings[0]["message"]
    assert "not a whole-install check" in findings[0]["message"]


# --- backup/restore round-trip through object storage ------------------------------------

def test_backup_restore_round_trips_through_storage(ops_db, account_id, storage_root):
    """G14.2's verify clause, literally: media is fetched through `StorageProvider.get`, archived,
    and put back through `StorageProvider.put` — not tarred/untarred off a filesystem root."""
    _doc_id, key = _make_document(account_id, get_storage())

    with account_context(account_id):
        archive = backup_svc.create_backup()
    assert archive.exists()

    # Simulate object loss (e.g. a bad deploy wiped the bucket's dev equivalent).
    get_storage().delete(key)
    with pytest.raises(ObjectNotFound):
        get_storage().get(key)

    with account_context(account_id):
        findings = backup_svc.run_doctor()
    assert any(f["level"] == "error" and "missing object" in f["message"] for f in findings)

    restored = backup_svc.restore_backup(archive)
    assert restored == 1
    assert get_storage().get(key) == b"%PDF-1.4 test contents"

    with account_context(account_id):
        findings = backup_svc.run_doctor()
    assert not [f for f in findings if f["level"] == "error"]


def test_backup_excludes_documents_without_a_real_storage_key(ops_db, account_id, storage_root):
    """A `file_path` that is not a storage key (the free-text "—" placeholder, or a pre-SPEC-002
    `/static/uploads/...` row) is not something any `StorageProvider` can `get()` — it must be
    skipped, not crash the backup."""
    from mihomes.db import get_session

    with account_context(account_id), get_session() as session:
        create_document(session, "Legacy", "/static/uploads/old.pdf", DocumentType.OTHER)

    with account_context(account_id):
        archive = backup_svc.create_backup()  # must not raise
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert names == ["manifest.json"]


# --- doctor: storage integrity -------------------------------------------------------------

def test_doctor_flags_a_document_with_no_object(ops_db, account_id, storage_root):
    _make_document(account_id, get_storage(), with_object=False)

    with account_context(account_id):
        findings = backup_svc.run_doctor()
    assert any(
        f["level"] == "error" and "1/1 document(s) point at a missing object" in f["message"]
        for f in findings
    )


def test_doctor_reports_no_documents_as_info_not_error(ops_db, account_id, storage_root):
    with account_context(account_id):
        findings = backup_svc.run_doctor()
    assert not [f for f in findings if f["level"] == "error"]
    assert any("no documents with stored files" in f["message"] for f in findings)


# --- doctor: backup freshness (D14 / RPO) ---------------------------------------------------

def test_doctor_flags_no_backups_as_warning(ops_db, account_id, storage_root):
    with account_context(account_id):
        findings = backup_svc.run_doctor()
    assert any(
        f["level"] == "warning" and "No media backups found" in f["message"] for f in findings
    )


def test_doctor_flags_a_stale_backup(ops_db, account_id, storage_root):
    import mihomes.config as config

    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stale = config.BACKUPS_DIR / "mihomes-media-backup-19990101-000000.tar.gz"
    with tarfile.open(stale, "w:gz"):
        pass
    old = time.time() - (backup_svc.RPO_HOURS + 1) * 3600
    os.utime(stale, (old, old))

    with account_context(account_id):
        findings = backup_svc.run_doctor()
    assert any(f["level"] == "error" and "stale" in f["message"] for f in findings)


def test_doctor_accepts_a_fresh_backup(ops_db, account_id, storage_root):
    with account_context(account_id):
        backup_svc.create_backup()
        findings = backup_svc.run_doctor()
    assert not [f for f in findings if f["level"] == "error"]
    assert any(f["level"] == "ok" and "Media backups:" in f["message"] for f in findings)


# --- restore: live-service guard (ported from the old test_backup.py) ----------------------

@pytest.fixture
def pid_dir(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(backup_svc, "_PID_DIR", home / ".mihomes")
    return home / ".mihomes"


def test_restore_refused_while_service_live(ops_db, account_id, storage_root, pid_dir):
    with account_context(account_id):
        archive = backup_svc.create_backup()
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "watchdog.pid").write_text(str(os.getpid()))

    with pytest.raises(RuntimeError, match="Refusing to restore"):
        backup_svc.restore_backup(archive)


def test_force_overrides_live_service(ops_db, account_id, storage_root, pid_dir):
    with account_context(account_id):
        archive = backup_svc.create_backup()
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "watchdog.pid").write_text(str(os.getpid()))

    backup_svc.restore_backup(archive, force=True)  # must not raise


def test_stale_pid_file_does_not_block_restore(ops_db, account_id, storage_root, pid_dir):
    with account_context(account_id):
        archive = backup_svc.create_backup()
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "monitor.pid").write_text("2147480000")  # essentially guaranteed not to exist

    backup_svc.restore_backup(archive)  # stale pid -> not live -> allowed


# --- CLI wiring smoke test -------------------------------------------------------------------

def test_doctor_and_backup_cli_commands(ops_db, account_id, storage_root):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "OK" in result.output

    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0
    assert "Backup created" in result.output
