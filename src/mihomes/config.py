"""MiHomes configuration — paths, directories, defaults."""

import os
from pathlib import Path

MIHOMES_DIR = Path(os.environ.get("MIHOMES_DIR", Path.home() / ".mihomes"))
DB_DIR = MIHOMES_DIR / "db"
MEDIA_DIR = MIHOMES_DIR / "media"
BACKUPS_DIR = MIHOMES_DIR / "backups"
EXPORTS_DIR = MIHOMES_DIR / "exports"
WHATSAPP_AUTH_DIR = MIHOMES_DIR / "whatsapp-auth"

DB_PATH = DB_DIR / "mihomes.db"
DB_URL = f"sqlite:///{DB_PATH}"


def ensure_dirs() -> None:
    """Create all MiHomes directories if they don't exist."""
    for d in (DB_DIR, MEDIA_DIR, BACKUPS_DIR, EXPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def is_initialized() -> bool:
    """Check if MiHomes has been initialized (DB exists)."""
    return DB_PATH.exists()
