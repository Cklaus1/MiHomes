"""Central logging configuration — one `dictConfig`, JSON in production (SPEC-005 Step 15, F7).

The R1 hardening pass replaced silent `except Exception: pass` swallows with
`logger.exception(...)`. Those records are only useful if they land somewhere durable — otherwise
they hit Python's last-resort stderr handler and vanish when the process exits. This module is
where that lands.

## Why `dictConfig` rather than the imperative version this replaces

The previous implementation attached one `RotatingFileHandler` by hand and re-scanned
`logger.handlers` on every call to stay idempotent. That works for one handler and stops working
at two: the ordering, the propagation flags and the per-logger levels all become implicit, and
"why is this record missing" turns into reading code rather than reading config. `dictConfig`
states the whole tree in one literal, which is also what makes the JSON/plain split below a
one-line decision instead of a branch in three places.

**Idempotence is preserved and still matters.** `setup_logging()` is called from the CLI root
callback *and* from `create_app()`, so a `mihomes serve` process runs both. `dictConfig` with
`disable_existing_loggers: False` is naturally re-entrant — it replaces the configuration rather
than appending to it, which is strictly better than the old tag-scanning guard.

## JSON in production, human-readable everywhere else

`SAAS_PRD:168` wants observability, and a log aggregator wants one object per line. A developer
reading a terminal wants neither. The switch is `MIHOMES_LOG_FORMAT` (`json` | `plain`), and it
**defaults by environment** rather than requiring an operator to remember: `MIHOMES_ENV=production`
gets JSON, everything else gets the readable formatter. Getting this wrong in either direction is
cheap and visible, which is why it is a default rather than a decision.

## What this module does NOT do

**Nobody is paged.** §10 records it plainly: this makes the system legible, not monitored.
`sentry-sdk` is config-gated and unconfigured, and at GA someone still has to be watching.
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
from typing import Any

from mihomes.config import LOGS_DIR

__all__ = ["JsonFormatter", "logging_dict_config", "setup_logging"]

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 5

#: Attributes `logging.LogRecord` always carries. Anything outside this set was attached by a
#: caller via `extra=` and is merged into the JSON object — which is how `request_id` reaches the
#: log line without every call site formatting it into the message.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"asctime", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Deliberately hand-rolled rather than a dependency. The entire requirement is "emit the record
    as an object", `structlog`/`python-json-logger` both bring a configuration model of their own,
    and F7's finding was that this repo had *no* observability — adding a library to that is a
    larger change than adding thirty lines, with more to get wrong at the boundary.

    `exc_info` is rendered into a `traceback` field rather than appended to `message`, because a
    multi-line traceback inside a JSON string is what makes aggregated logs unsearchable.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)

        # Anything a caller passed via `extra=` — `request_id` above all. Merged rather than
        # nested so a log aggregator can index on it directly.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value if _is_jsonable(value) else repr(value)

        return json.dumps(payload, default=str)


def _is_jsonable(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, type(None), list, dict))


def _format_name() -> str:
    """`json` in production, `plain` elsewhere — overridable by `MIHOMES_LOG_FORMAT`."""
    explicit = os.environ.get("MIHOMES_LOG_FORMAT", "").strip().lower()
    if explicit in ("json", "plain"):
        return explicit
    return "json" if os.environ.get("MIHOMES_ENV", "").lower() == "production" else "plain"


def logging_dict_config() -> dict[str, Any]:
    """The whole configuration as data, so a test can assert on it without installing it.

    Returned rather than applied for the same reason `SCHEDULE` in `cli/jobs.py` is a constant:
    a configuration you can only observe by its side effects is one you cannot gate.
    """
    level = os.environ.get("MIHOMES_LOG_LEVEL", "INFO").upper()
    if not hasattr(logging, level):
        level = "INFO"

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    return {
        "version": 1,
        # **False, not the default True.** Disabling existing loggers would silence every module
        # logger created at import time — which, given the import order here, is most of them.
        "disable_existing_loggers": False,
        "formatters": {
            "plain": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
            },
            "json": {
                "()": "mihomes.logging_config.JsonFormatter",
            },
        },
        "handlers": {
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(LOGS_DIR / "mihomes.log"),
                "maxBytes": _MAX_BYTES,
                "backupCount": _BACKUP_COUNT,
                "encoding": "utf-8",
                "formatter": _format_name(),
                "level": level,
            },
            # **There is deliberately no console handler.**
            #
            # The first version had one, and it cost nine test failures that took a full-suite
            # run to see. `logging.StreamHandler` binds whatever `sys.stderr` *is* when the
            # handler is constructed and holds that object forever — so once anything replaces
            # the stream (pytest's per-test capture being the obvious case, but `nohup`,
            # daemonisation and a rotated systemd journal do it too), every emit fails with
            # `ValueError: I/O operation on closed file`.
            #
            # The consequence is worse than a lost console line: **a failed emit aborts the
            # record before the remaining handlers run**, so the file handler lost it as well.
            # Nine tests asserting "this failure was logged" read as *the code did not log*
            # when the code had logged perfectly — and every one of them passed in isolation.
            # `ext://sys.stderr` does not fix it; that is resolved at configuration time too.
            #
            # A library's logging config has no business owning the terminal in any case. The
            # durable sink is the rotating file — which is what F7 was about, since a swallowed
            # error that reaches no file is indistinguishable from one never raised. A CLI that
            # wants console output can add its own handler, where the process that owns the
            # stream also owns its lifetime.
        },
        "loggers": {
            "mihomes": {
                "handlers": ["file"],
                "level": level,
                # **True**, and the first version had it False.
                #
                # The reasoning for False was "the root has no handler of ours, so propagating
                # would duplicate records if anything else configures one". That trades a
                # hypothetical duplicate for a real loss: `propagate: False` means **nothing
                # attached to the root logger ever sees a record from this tree** — pytest's
                # `caplog`, a `logging.basicConfig` in a script, an operator's own handler, or
                # an aggregator agent that hooks the root.
                #
                # Measured: four tests asserting "this failure was logged" failed with an empty
                # `caplog`, because the record reached the file handler and stopped there. The
                # duplicate this guarded against never materialised — this config owns the only
                # handler here — and the guard cost the ability to observe our own logs from
                # anywhere else, which is the opposite of what F7 asked for.
                "propagate": True,
            },
        },
    }


def setup_logging() -> None:
    """Install the configuration. Idempotent — safe from both the CLI and the app factory."""
    logging.config.dictConfig(logging_dict_config())
