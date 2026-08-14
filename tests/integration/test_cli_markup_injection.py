"""L5 — Rich markup injection hardening.

Rich interprets ``[...]`` in a string as a style tag. Any command that renders
user-controlled text (note bodies, entity names, free-form fields) straight into
a table cell / panel / ``console.print`` f-string will raise ``MarkupError`` and
crash when that text contains an unbalanced tag such as ``[/]``. These tests feed
such payloads through real commands and assert the command survives (exit 0) and
renders the literal text rather than blowing up.

Uses the same shared-on-disk DB convention as test_cli.py (MIHOMES_DIR frozen at
first config import; idempotent demo load).
"""

import os
import tempfile

import pytest
from typer.testing import CliRunner

os.environ.setdefault("MIHOMES_DIR", tempfile.mkdtemp())


# Imports are deliberately below the MIHOMES_DIR assignment: mihomes.config binds its
# paths at import time, so importing earlier would freeze the real home directory.
from mihomes.cli import app  # noqa: E402
from mihomes.db import get_session, init_db  # noqa: E402
from mihomes.tenancy import account_context  # noqa: E402

runner = CliRunner()

# A payload that crashes an un-escaped Rich renderer: an unbalanced closing tag.
PAYLOAD = "danger [/] [bold]unclosed"


@pytest.fixture(scope="module", autouse=True)
def setup_db(cli_database):
    """Initialize the CLI database and load demo data once per module.

    `cli_database` (root conftest) owns the dedicated Postgres database and sets `DATABASE_URL`,
    which `mihomes.db._active_url()` honours as of G13. The SQLite file this used to use no
    longer works at all: `0001_pg_baseline` is Postgres-native, so its `DEFAULT now()` on
    `created_at` makes the first INSERT into `accounts` fail with `unknown function: now()`.

    The `account_context` is required, not incidental — `load_demo_data` writes tenant-owned
    rows, and G8.3 stamps `account_id` from this ContextVar and raises `LookupError` without it.
    Demo data is loaded idempotently so collection order cannot trip the "already loaded" guard
    when several of these modules run in one session.
    """
    init_db()
    # init_db() runs the migrations AND bootstraps the account; this returns the existing one.
    from mihomes.db import get_engine
    from mihomes.tenancy.bootstrap import ensure_default_account
    account_id = ensure_default_account(get_engine())

    with account_context(account_id):
        with get_session() as session:
            from mihomes.models.property import Property
            from mihomes.services.demo import load_demo_data
            if not session.query(Property).filter(Property.slug == "beach-house").first():
                load_demo_data(session)
        yield


def test_note_list_survives_markup_in_body():
    # A note body with unbalanced markup must not crash `note list`.
    add = runner.invoke(app, ["note", "add", PAYLOAD, "--to", "property:beach-house"])
    assert add.exit_code == 0, add.output

    result = runner.invoke(app, ["note", "list", "--to", "property:beach-house"])
    assert result.exit_code == 0, f"note list crashed on markup body:\n{result.output}"
    assert result.exception is None
    # literal text present, not swallowed
    assert "danger" in result.output


def test_guest_list_survives_markup_in_name():
    # Guest name + dietary are rendered raw into a table via add_row.
    add = runner.invoke(
        app,
        ["guest", "add", PAYLOAD, "--dietary", "[red]allergic[/]"],
    )
    assert add.exit_code == 0, add.output

    result = runner.invoke(app, ["guest", "list"])
    assert result.exit_code == 0, f"guest list crashed on markup name:\n{result.output}"
    assert result.exception is None
    assert "danger" in result.output
