"""MiHomes configuration — paths, directories, defaults."""

import os
from pathlib import Path

MIHOMES_DIR = Path(os.environ.get("MIHOMES_DIR", Path.home() / ".mihomes"))
DB_DIR = MIHOMES_DIR / "db"
MEDIA_DIR = MIHOMES_DIR / "media"
# Uploaded documents live under the user's data dir — NOT inside the installed
# package, so a `pip install --upgrade` can't wipe them and backups include
# them (spec H34). All web upload routes resolve their write path from here.
UPLOADS_DIR = MEDIA_DIR / "uploads"
BACKUPS_DIR = MIHOMES_DIR / "backups"
EXPORTS_DIR = MIHOMES_DIR / "exports"
LOGS_DIR = MIHOMES_DIR / "logs"
WHATSAPP_AUTH_DIR = MIHOMES_DIR / "whatsapp-auth"

DB_PATH = DB_DIR / "mihomes.db"
DB_URL = f"sqlite:///{DB_PATH}"

# Public URL prefix the uploads dir is served under (see web.app).
UPLOADS_URL_PREFIX = "/uploads"


def ensure_dirs() -> None:
    """Create all MiHomes directories if they don't exist."""
    for d in (DB_DIR, MEDIA_DIR, UPLOADS_DIR, BACKUPS_DIR, EXPORTS_DIR,
              LOGS_DIR, WHATSAPP_AUTH_DIR):
        d.mkdir(parents=True, exist_ok=True)


def is_initialized() -> bool:
    """Has MiHomes been initialized?

    **Was `DB_PATH.exists()`, which is a SQLite-only question.** Once `DATABASE_URL` points at
    Postgres (SPEC-002 Step 13) there is no local file to look for, so that check reported "not
    initialized" for a perfectly good database and every CLI command exited 1 with
    *"MiHomes is not initialized. Run: mihomes init"*. Measured while wiring G13 — the same class
    of filesystem assumption that G14.3 records for `doctor`, where a false *"Database not found"*
    also skips every later check.

    For Postgres the question becomes "does the schema exist", answered by looking for the
    `accounts` table — the one table every install must have, created by `0001_pg_baseline` and
    populated by `init_db()`. Kept cheap: a catalogue lookup, no ORM import, and any connection
    failure means "not initialized" rather than an exception, because callers use this to decide
    whether to *tell the user to run init*.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        return DB_PATH.exists()

    try:
        from sqlalchemy import create_engine, inspect

        engine = create_engine(url)
        try:
            return inspect(engine).has_table("accounts")
        finally:
            engine.dispose()
    except Exception:
        # Unreachable database, bad URL, missing driver — from the caller's point of view all of
        # these mean "not usable yet", and raising here would replace a helpful message with a
        # traceback.
        return False
