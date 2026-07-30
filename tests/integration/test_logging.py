"""R1.3 / L1 — rotating file-log handler wired into the root CLI callback.

The R1 census replaced ~42 `except Exception: pass` swallows with
`logger.exception(...)`. That is only useful if those records actually land
somewhere durable. Before this change nothing configured the root logger, so
every `logger.exception` went to the default last-resort stderr handler and was
lost the moment the process exited. L1 installs a RotatingFileHandler under the
MiHomes data dir so suppressed-but-logged failures survive for post-mortem.

These tests assert:
  * a rotating file handler is installed under <data_dir>/logs/,
  * the level honours MIHOMES_LOG_LEVEL,
  * an emitted record is actually written to the file,
  * setup is idempotent (invoking the CLI repeatedly must not stack handlers).
"""

import importlib
import logging
import logging.handlers

import pytest


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    """Point MIHOMES_DIR at a temp dir and reset root logger handlers."""
    monkeypatch.setenv("MIHOMES_DIR", str(tmp_path))
    monkeypatch.delenv("MIHOMES_LOG_LEVEL", raising=False)

    # config caches MIHOMES_DIR at import time — reload so LOGS_DIR follows tmp.
    import mihomes.config as config_mod
    importlib.reload(config_mod)
    import mihomes.logging_config as logging_config_mod
    importlib.reload(logging_config_mod)

    root = logging.getLogger("mihomes")
    saved = root.handlers[:]
    root.handlers = []
    yield tmp_path, logging_config_mod
    # restore
    for h in root.handlers:
        h.close()
    root.handlers = saved
    # Reloading config above re-pinned mihomes.config.DB_DIR to this temp dir,
    # which is about to be deleted; other modules (e.g. web.server) captured the
    # *original* DB_DIR at their import and would then diverge from config. Undo
    # the env patch first (LIFO — monkeypatch would otherwise fire after us) and
    # reload config back to the true environment so the globals realign.
    monkeypatch.undo()
    importlib.reload(config_mod)
    importlib.reload(logging_config_mod)


def test_setup_installs_rotating_file_handler(isolated_logging):
    tmp_path, logging_config = isolated_logging
    logging_config.setup_logging()

    root = logging.getLogger("mihomes")
    rotating = [h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert rotating, "no RotatingFileHandler installed on the mihomes logger"

    log_dir = tmp_path / "logs"
    assert log_dir.is_dir(), "logs directory not created under data dir"


def test_level_honours_env_var(isolated_logging, monkeypatch):
    tmp_path, logging_config = isolated_logging
    monkeypatch.setenv("MIHOMES_LOG_LEVEL", "DEBUG")
    logging_config.setup_logging()

    assert logging.getLogger("mihomes").level == logging.DEBUG


def test_default_level_is_info(isolated_logging):
    tmp_path, logging_config = isolated_logging
    logging_config.setup_logging()

    assert logging.getLogger("mihomes").level == logging.INFO


def test_logged_exception_is_written_to_file(isolated_logging):
    tmp_path, logging_config = isolated_logging
    logging_config.setup_logging()

    logger = logging.getLogger("mihomes.test")
    try:
        raise ValueError("boom-from-r1")
    except ValueError:
        logger.exception("suppressed exception under test")

    for h in logging.getLogger("mihomes").handlers:
        h.flush()

    log_files = list((tmp_path / "logs").glob("*.log"))
    assert log_files, "no .log file produced"
    contents = log_files[0].read_text()
    assert "boom-from-r1" in contents
    assert "suppressed exception under test" in contents


def test_setup_is_idempotent(isolated_logging):
    tmp_path, logging_config = isolated_logging
    logging_config.setup_logging()
    logging_config.setup_logging()
    logging_config.setup_logging()

    root = logging.getLogger("mihomes")
    rotating = [h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(rotating) == 1, f"handler stacked {len(rotating)}x — not idempotent"
