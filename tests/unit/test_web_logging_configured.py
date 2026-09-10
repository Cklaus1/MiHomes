"""The web entry points must install the file log, or every 500 is undiagnosable.

**The bug.** `setup_logging()` was called only from the CLI root callback
(`cli/__init__.py`). `mihomes-web` and `mihomes-dev` are separate console scripts that run
uvicorn directly, so neither ever installed a handler — and `logging_config` deliberately has
no console handler, so the records went nowhere at all.

What that cost, measured on a real 500: `errors.py`'s catch-all renders *"quote this reference
and we can find exactly what failed"* beside a request id, and `logger.exception` wrote the
traceback to a logger with no handlers. `grep <request-id> ~/.mihomes/logs/mihomes.log`
returned nothing. The page promised a lookup that could not succeed — worse than showing no
reference, because it sends the reader looking for a file that was never written.
"""

from __future__ import annotations

import logging


def test_dev_and_main_both_configure_logging():
    """Both entry points call it. A new one that forgets is the regression."""
    import inspect

    from mihomes.web import server

    for entry in ("main", "dev"):
        src = inspect.getsource(getattr(server, entry))
        assert "_configure_logging()" in src, (
            f"`mihomes.web.server.{entry}` does not install the file log — a 500 served by it "
            f"writes its traceback nowhere, and the error page's request id leads to nothing"
        )


def test_configure_logging_attaches_a_file_handler():
    """The mechanism: after the call, a `mihomes.*` record reaches a file handler."""
    from mihomes.config import LOGS_DIR
    from mihomes.web.server import _configure_logging

    _configure_logging()

    handlers = logging.getLogger("mihomes").handlers or logging.getLogger().handlers
    paths = [
        getattr(h, "baseFilename", "") for h in handlers if hasattr(h, "baseFilename")
    ]
    assert paths, (
        "no file handler is attached after _configure_logging(), so tracebacks are discarded"
    )
    assert any(str(LOGS_DIR) in p for p in paths), (
        f"a file handler exists but not under {LOGS_DIR}; the error page tells the reader to "
        f"look there"
    )


def test_a_traceback_actually_lands_in_the_file():
    """End-to-end, because the handler existing is not the claim — the write is.

    `logger.exception` is what `errors.py` calls; this asserts the bytes arrive.
    """
    from mihomes.config import LOGS_DIR
    from mihomes.web.server import _configure_logging

    _configure_logging()
    marker = "test-traceback-marker-9f3c1"

    logger = logging.getLogger("mihomes.test_web_logging")
    try:
        raise RuntimeError(marker)
    except RuntimeError:
        logger.exception("unhandled exception probe")

    for h in logging.getLogger("mihomes").handlers + logging.getLogger().handlers:
        h.flush()

    text = (LOGS_DIR / "mihomes.log").read_text(encoding="utf-8", errors="replace")
    assert marker in text, "logger.exception did not reach the log file"
    assert "Traceback" in text, "the record landed without its traceback"
