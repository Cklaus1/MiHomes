"""Central logging configuration (L1).

The R1 hardening pass replaced silent `except Exception: pass` swallows with
`logger.exception(...)`. Those records are only useful if they land somewhere
durable — otherwise they hit Python's last-resort stderr handler and vanish
when the process exits. This module installs a single RotatingFileHandler on
the top-level ``mihomes`` logger so suppressed-but-logged failures persist under
``<data_dir>/logs/mihomes.log`` for post-mortem.

Called once from the root CLI callback. Level is read from ``MIHOMES_LOG_LEVEL``
(default ``INFO``). Setup is idempotent — repeated calls never stack handlers.
"""

import logging
import logging.handlers
import os

from mihomes.config import LOGS_DIR

_HANDLER_TAG = "mihomes-rotating-file"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 5


def setup_logging() -> None:
    """Install the rotating file handler on the ``mihomes`` logger (idempotent)."""
    level_name = os.environ.get("MIHOMES_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("mihomes")
    logger.setLevel(level)

    # Idempotent: if our tagged handler is already attached, just refresh level.
    for handler in logger.handlers:
        if getattr(handler, "_mihomes_tag", None) == _HANDLER_TAG:
            handler.setLevel(level)
            return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "mihomes.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler._mihomes_tag = _HANDLER_TAG
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    )
    logger.addHandler(handler)
