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
WHATSAPP_AUTH_DIR = MIHOMES_DIR / "whatsapp-auth"

DB_PATH = DB_DIR / "mihomes.db"
DB_URL = f"sqlite:///{DB_PATH}"

# Public URL prefix the uploads dir is served under (see web.app).
UPLOADS_URL_PREFIX = "/uploads"


def ensure_dirs() -> None:
    """Create all MiHomes directories if they don't exist."""
    for d in (DB_DIR, MEDIA_DIR, UPLOADS_DIR, BACKUPS_DIR, EXPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def is_initialized() -> bool:
    """Check if MiHomes has been initialized (DB exists)."""
    return DB_PATH.exists()
