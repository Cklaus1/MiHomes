"""G-CLI · C.2/M41 — import-csv must exit non-zero when every row fails.

Previously the command printed per-row errors as warnings and returned normally,
so a CSV in which no row imported still exited 0 — a scripted caller couldn't
tell import had wholly failed. When ``errors and not created`` the command must
exit 1.
"""

import os
import tempfile

_test_dir = tempfile.mkdtemp()
os.environ["MIHOMES_DIR"] = _test_dir

import pytest
from typer.testing import CliRunner

from mihomes.cli import app
from mihomes.db import get_session, init_db

runner = CliRunner()


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()
    yield


def _write_csv(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".csv", dir=_test_dir)
    os.close(fd)
    with open(path, "w") as f:
        f.write(text)
    return path


def test_import_all_rows_fail_exits_1():
    # Every row has an invalid property_type enum → each row errors, none created.
    csv_text = "name,property_type\nA House,not-a-real-type\nB House,also-bogus\n"
    path = _write_csv(csv_text)
    result = runner.invoke(app, ["import-csv", "csv", "property", path])
    assert result.exit_code == 1, f"expected exit 1, output={result.output!r}"
    assert "error" in result.output.lower()


def test_import_partial_success_exits_0():
    # One good row, one bad row → partial success still exits 0.
    csv_text = "name,property_type\nGood House,primary\nBad House,bogus-type\n"
    path = _write_csv(csv_text)
    result = runner.invoke(app, ["import-csv", "csv", "property", path])
    assert result.exit_code == 0, f"expected exit 0, output={result.output!r}"
    assert "imported" in result.output.lower()


def test_import_all_success_exits_0():
    csv_text = "name,property_type\nAlpha House,primary\nBeta House,secondary\n"
    path = _write_csv(csv_text)
    result = runner.invoke(app, ["import-csv", "csv", "property", path])
    assert result.exit_code == 0, f"expected exit 0, output={result.output!r}"
