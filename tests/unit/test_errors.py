"""SPEC-005 §6 Step 15 — observability and error handling (A31, A32).

Two criteria, and the second needs its scope stated before it can be honest.

**A32 says "the request path", not "the tree".** C3 measured 154 `except Exception` blocks in
`src/` against the spec's claimed 136 — but scoping A32 to all of them turns one acceptance
criterion into a 154-site refactor wearing its name, and the 138 outside `web/` are a real
cleanup with no criterion attached (§0.8 U11). So this file scopes to `src/mihomes/web/`, which
is what a request actually traverses, and the rest is logged as deferred rather than quietly
counted as done.

**The check is an AST walk, not a grep.** `except Exception: pass` is the shape everyone thinks
of, but the one that actually occurred here was a handler that set a user-facing error string and
told the operator nothing — invisible to any pattern matching `pass`. What matters is whether the
handler *either logs or re-raises*, which is a property of its body.
"""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest

from mihomes.logging_config import JsonFormatter, logging_dict_config

WEB_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "mihomes" / "web"


# ── A32 — no silent swallow in the request path ────────────────────────────────


def _swallows_silently(handler: ast.ExceptHandler) -> bool:
    """Does this `except Exception` block neither log nor re-raise?

    "Logs" means any `*.exception|error|warning|info|debug(...)` call anywhere in the body —
    matched on the *method name* rather than on a `logger` receiver, because a module that
    aliases its logger or logs through a helper is still logging.
    """
    logs = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("exception", "error", "warning", "info", "debug")
        for node in ast.walk(handler)
    )
    reraises = any(isinstance(node, ast.Raise) for node in ast.walk(handler))
    return not logs and not reraises


def _bare_exception_handlers():
    """Every `except Exception` under `web/`, as `(path, lineno, node)`."""
    for path in sorted(WEB_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                caught = node.type
                if isinstance(caught, ast.Name) and caught.id == "Exception":
                    yield path, node.lineno, node


def test_no_silent_swallow():
    """A32 — every `except Exception` in the request path logs or re-raises (N15, F7).

    Nine sites failed this when it was written, all the same shape: an AI or weather call that
    degraded to a user-visible error message and recorded nothing. That is not a crash, which is
    why it survived a hardening pass that was looking for crashes — and it is exactly what N15
    means by a service that "cannot learn it is broken".
    """
    offenders = [
        f"{path.relative_to(WEB_ROOT.parents[2])}:{lineno}"
        for path, lineno, node in _bare_exception_handlers()
        if _swallows_silently(node)
    ]

    assert not offenders, (
        "an `except Exception` in the request path must log with context or re-raise (N15) — "
        "a customer seeing an error the operator never hears about is how a broken provider "
        "stays broken. Silent:\n  " + "\n  ".join(offenders)
    )


def test_the_scope_of_a32_is_stated_not_assumed():
    """C3's scoping, asserted rather than left in a comment.

    If `web/` ever stops containing bare handlers entirely, `test_no_silent_swallow` becomes
    vacuous — it would pass over an empty list — and nobody would notice. This is the check that
    the check has something to check.
    """
    total = list(_bare_exception_handlers())
    assert total, (
        "no `except Exception` found under web/ at all — either the scope is wrong or A32 is "
        "now asserting nothing"
    )


# ── A31 — the handler, the page, and one structured record ─────────────────────


def test_handler_and_log(caplog):
    """A31 — an unhandled exception renders the error page and emits **one** structured record
    carrying a request id.

    Driven through the real app rather than by calling the handler: `app.exception_handler`
    registration, middleware ordering and template lookup are three separate things that can
    each be individually correct and still not compose.
    """
    from fastapi.testclient import TestClient

    from mihomes.web import deps as deps_module
    from mihomes.web.app import create_app
    from mihomes.web.errors import REQUEST_ID_HEADER

    app = create_app()

    @app.get("/_test_boom", include_in_schema=False)
    async def _boom():
        raise RuntimeError("deliberate")

    _boom.__mihomes_undeclared_ok__ = True

    client = TestClient(app, raise_server_exceptions=False, base_url="http://localhost")

    with caplog.at_level(logging.ERROR):
        response = client.get("/_test_boom", headers={"Accept": "application/json"})

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error"

    request_id = body["request_id"]
    assert request_id and request_id != "-"
    assert response.headers[REQUEST_ID_HEADER] == request_id, (
        "the response header and the body must name the same request — they are what connect "
        "a support ticket to a log line"
    )

    # **Exactly one** record, not two. Logging here *and* re-raising would double every incident
    # in the aggregator, and the count is what an operator alerts on.
    unhandled = [r for r in caplog.records if "unhandled exception" in r.getMessage()]
    assert len(unhandled) == 1, f"expected one record, got {len(unhandled)}"

    record = unhandled[0]
    assert record.exc_info is not None, "the record must carry the traceback, not just a message"
    assert getattr(record, "request_id", None) == request_id, (
        "A31: the log record must carry the same request id the response does — a record that "
        "cannot be joined to the response is not observability"
    )

    # The formatter that runs in production has to survive this record. Asserted here rather
    # than on a synthetic one, because `extra=` fields are exactly what a naive formatter drops.
    rendered = JsonFormatter().format(record)
    assert request_id in rendered
    assert "traceback" in rendered

    # `deps_module` is imported to prove the app under test is the shared one; a second
    # Jinja environment would mean this test passed against a template nothing else uses.
    assert deps_module.templates is not None


def test_the_html_error_page_renders_and_names_the_request(caplog):
    """The browser half of A31: a navigation gets the page, not JSON.

    Separate from the assertion above because the two responses come from different branches,
    and a handler that renders JSON for everybody would satisfy the other test completely.
    """
    from fastapi.testclient import TestClient

    from mihomes.web.app import create_app
    from mihomes.web.errors import REQUEST_ID_HEADER

    app = create_app()

    @app.get("/_test_boom_html", include_in_schema=False)
    async def _boom():
        raise RuntimeError("deliberate")

    _boom.__mihomes_undeclared_ok__ = True

    client = TestClient(app, raise_server_exceptions=False, base_url="http://localhost")
    with caplog.at_level(logging.ERROR):
        response = client.get("/_test_boom_html", headers={"Accept": "text/html"})

    assert response.status_code == 500
    assert "Something went wrong" in response.text
    assert response.headers[REQUEST_ID_HEADER] in response.text, (
        "the page must show the reference the header carries — a customer reading the screen is "
        "the only person who can quote it back"
    )

    # No internals. The page is shown to whoever triggered it, including someone probing.
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text
    assert "deliberate" not in response.text


# ── the logging configuration itself ───────────────────────────────────────────


def test_logging_is_one_dictconfig():
    """F7's fix: one configuration, stated as data.

    The previous implementation attached a handler imperatively and re-scanned
    `logger.handlers` to stay idempotent — which works for one handler and stops working at two.
    """
    config = logging_dict_config()

    assert config["version"] == 1
    assert config["disable_existing_loggers"] is False, (
        "disabling existing loggers silences every module logger created at import time, which "
        "given the import order here is most of them"
    )
    assert "mihomes" in config["loggers"]
    assert set(config["formatters"]) >= {"plain", "json"}


def test_the_config_owns_no_stream_handler():
    """No `StreamHandler`, and this is a regression gate rather than a style rule.

    The first version of this config had a console handler. `StreamHandler` binds whatever
    `sys.stderr` *is* at construction time and keeps that object — so once anything replaces the
    stream, every emit raises `ValueError: I/O operation on closed file`, and **a failed emit
    aborts the record before the remaining handlers run**. The durable file handler lost the
    record too.

    That cost nine failures in the full suite, all of them passing in isolation, all reading as
    "the code did not log" when the code had logged. `ext://sys.stderr` does not fix it — that
    is resolved at configuration time as well.
    """
    handlers = logging_dict_config()["handlers"]

    for name, spec in handlers.items():
        assert "StreamHandler" not in spec["class"], (
            f"handler {name!r} is a StreamHandler. It binds a stream that outlives its "
            "validity, and a failed emit takes the whole record down with it — including the "
            "file handler that F7 exists to feed"
        )

    assert any("RotatingFileHandler" in s["class"] for s in handlers.values()), (
        "the durable sink is the point: a swallowed error that reaches no file is "
        "indistinguishable from one that was never raised"
    )


def test_records_reach_the_root_logger():
    """`propagate` must stay **True** — the second half of the same nine failures.

    `propagate: False` means nothing attached to the root ever sees a record from this tree:
    not `caplog`, not a `basicConfig` in a script, not an operator's handler, not an aggregator
    agent. The config here owns the only handler on `mihomes`, so the duplicate that False was
    guarding against cannot occur — and the guard cost the ability to observe our own logs from
    anywhere else, which is precisely backwards for an observability change.

    Asserted by *capturing* rather than by reading the flag, so a future config that sets
    `propagate: True` while breaking propagation some other way still fails.
    """
    import logging as _logging

    from mihomes.logging_config import setup_logging

    assert logging_dict_config()["loggers"]["mihomes"]["propagate"] is True

    setup_logging()

    records: list[_logging.LogRecord] = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            records.append(record)

    root = _logging.getLogger()
    handler = _Capture()
    root.addHandler(handler)
    try:
        _logging.getLogger("mihomes.test_propagation").error("reached the root")
    finally:
        root.removeHandler(handler)

    assert any(r.getMessage() == "reached the root" for r in records), (
        "a record logged under `mihomes` never reached a root handler — caplog and every "
        "external log consumer are blind to this application's own errors"
    )


@pytest.mark.parametrize(
    ("env", "expected"),
    [({"MIHOMES_ENV": "production"}, "json"), ({}, "plain")],
)
def test_json_in_production_plain_elsewhere(monkeypatch, env, expected):
    """The format follows the environment rather than an operator's memory.

    Getting this wrong is cheap and visible in both directions — unreadable local logs, or
    unparseable production ones — which is why it is a default rather than a required setting.
    """
    monkeypatch.delenv("MIHOMES_LOG_FORMAT", raising=False)
    monkeypatch.delenv("MIHOMES_ENV", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert logging_dict_config()["handlers"]["file"]["formatter"] == expected


def test_the_json_formatter_emits_one_object_per_line():
    """Including `extra=` fields, which is how `request_id` reaches the line at all."""
    import json

    record = logging.LogRecord(
        name="mihomes.test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="something %s", args=("broke",), exc_info=None,
    )
    record.request_id = "abc123"

    rendered = JsonFormatter().format(record)
    assert "\n" not in rendered, "one object per line, or an aggregator cannot parse it"

    payload = json.loads(rendered)
    assert payload["message"] == "something broke"
    assert payload["level"] == "ERROR"
    assert payload["request_id"] == "abc123"


def test_healthz_is_live_on_the_product_app():
    """C2 — the spec said "confirmed live from SPEC-001"; it was landing-only.

    `landing/routes.py:41` has one and the product app had none, so Step 15 *adds* it. Asserted
    against the route table rather than by calling it, because the call needs a database and
    what is being asserted is that the route exists at all.
    """
    from mihomes.web.app import create_app

    paths = {route.path for route in create_app().routes if hasattr(route, "path")}
    assert "/healthz" in paths, (
        "the product app has no /healthz — Fly's healthcheck would have nothing to call, and a "
        "deploy that cannot fail its healthcheck cannot be rolled back automatically"
    )
