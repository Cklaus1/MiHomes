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

from mihomes.cli import app
from mihomes.db import get_session, init_db

runner = CliRunner()

# A payload that crashes an un-escaped Rich renderer: an unbalanced closing tag.
PAYLOAD = "danger [/] [bold]unclosed"


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()
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
