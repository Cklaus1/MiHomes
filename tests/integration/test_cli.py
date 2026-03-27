"""CLI integration tests — exercise commands via Typer CliRunner.

Finds bugs in argument parsing, output formatting, and error handling
that service-level tests miss.
"""

import os
import tempfile
import pytest
from typer.testing import CliRunner

# Override MIHOMES_DIR before importing anything
_test_dir = tempfile.mkdtemp()
os.environ["MIHOMES_DIR"] = _test_dir

from mihomes.cli import app
from mihomes.db import init_db, get_engine, get_session

runner = CliRunner()


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    """Initialize a real DB for CLI tests."""
    init_db()
    # Load demo data
    with get_session() as session:
        from mihomes.services.demo import load_demo_data
        load_demo_data(session)
    yield


class TestVersionAndHelp:
    def test_version(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "MiHomes" in result.output

    def test_help(self):
        result = runner.invoke(app, ["help"])
        assert result.exit_code == 0
        assert "Quick Reference" in result.output

    def test_stats(self):
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "Properties" in result.output
        assert "3" in result.output  # 3 demo properties

    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        # Typer returns exit code 2 for no_args_is_help
        assert result.exit_code in (0, 2)
        assert "Commands" in result.output or "Usage" in result.output


class TestPropertyCLI:
    def test_list(self):
        result = runner.invoke(app, ["property", "list"])
        assert result.exit_code == 0
        # Rich wraps long names across lines, so check for partial match
        assert "Beach" in result.output
        assert "beach-hou" in result.output

    def test_show(self):
        result = runner.invoke(app, ["property", "show", "beach-house"])
        assert result.exit_code == 0
        assert "Beach House" in result.output
        assert "Open Tasks" in result.output  # Enriched show

    def test_show_not_found(self):
        result = runner.invoke(app, ["property", "show", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_add_and_delete(self):
        result = runner.invoke(app, ["property", "add", "CLI Test House", "--type", "primary"])
        assert result.exit_code == 0
        assert "created" in result.output
        result = runner.invoke(app, ["property", "delete", "cli-test-house", "--force"])
        assert result.exit_code == 0
        assert "deleted" in result.output

    def test_add_empty_name(self):
        result = runner.invoke(app, ["property", "add", "", "--type", "primary"])
        assert result.exit_code == 1
        assert "cannot be empty" in result.output

    def test_edit(self):
        result = runner.invoke(app, ["property", "edit", "city-apartment", "--sqft", "2000"])
        assert result.exit_code == 0
        assert "updated" in result.output

    def test_occupy_vacate(self):
        result = runner.invoke(app, ["property", "occupy", "beach-house", "--from", "2026-06-01"])
        assert result.exit_code == 0
        assert "occupied" in result.output
        result = runner.invoke(app, ["property", "vacate", "beach-house"])
        assert result.exit_code == 0

    def test_status(self):
        result = runner.invoke(app, ["property", "status"])
        assert result.exit_code == 0
        assert "Beach House" in result.output


class TestTaskCLI:
    def test_list(self):
        result = runner.invoke(app, ["task", "list"])
        assert result.exit_code == 0

    def test_list_overdue(self):
        result = runner.invoke(app, ["task", "list", "--overdue"])
        assert result.exit_code == 0

    def test_list_recent(self):
        result = runner.invoke(app, ["task", "list", "--recent"])
        assert result.exit_code == 0

    def test_add_and_complete(self):
        result = runner.invoke(app, ["task", "add", "CLI Task", "--property", "beach-house", "--due", "2026-12-01"])
        assert result.exit_code == 0
        assert "created" in result.output
        # Extract slug for completion
        result = runner.invoke(app, ["task", "complete", "cli-task", "--notes", "Done via CLI test"])
        assert result.exit_code == 0
        assert "completed" in result.output

    def test_add_bad_date(self):
        result = runner.invoke(app, ["task", "add", "Bad Date", "--property", "beach-house", "--due", "not-a-date"])
        assert result.exit_code == 1
        assert "Invalid" in result.output

    def test_add_seasonal_no_season(self):
        result = runner.invoke(app, ["task", "add", "Bad Season", "--property", "beach-house", "--recurrence", "seasonal"])
        assert result.exit_code == 1
        assert "Season spec required" in result.output

    def test_upcoming(self):
        result = runner.invoke(app, ["task", "upcoming", "--days", "90"])
        assert result.exit_code == 0


class TestIssueCLI:
    def test_list_open(self):
        result = runner.invoke(app, ["issue", "list", "--open"])
        assert result.exit_code == 0
        assert "Roof leak" in result.output

    def test_add_and_resolve(self):
        result = runner.invoke(app, ["issue", "add", "CLI Issue", "--property", "beach-house", "--severity", "low"])
        assert result.exit_code == 0
        result = runner.invoke(app, ["issue", "resolve", "cli-issue", "--notes", "Fixed"])
        assert result.exit_code == 0
        assert "resolved" in result.output


class TestStaffCLI:
    def test_list(self):
        result = runner.invoke(app, ["staff", "list"])
        assert result.exit_code == 0
        assert "Sarah Chen" in result.output

    def test_workload(self):
        result = runner.invoke(app, ["staff", "workload"])
        assert result.exit_code == 0
        assert "Pending" in result.output  # Real task counts now


class TestVendorCLI:
    def test_list(self):
        result = runner.invoke(app, ["vendor", "list"])
        assert result.exit_code == 0
        # Rich wraps long names across lines
        assert "Coastal" in result.output


class TestBudgetCLI:
    def test_report(self):
        result = runner.invoke(app, ["budget", "report", "--property", "beach-house"])
        assert result.exit_code == 0

    def test_negative_rejected(self):
        result = runner.invoke(app, ["budget", "set", "--property", "beach-house", "--category", "test", "--amount", "-100"])
        assert result.exit_code == 1
        assert "negative" in result.output


class TestSearchCLI:
    def test_search(self):
        result = runner.invoke(app, ["search", "Beach"])
        assert result.exit_code == 0
        assert "property" in result.output


class TestAlertsCLI:
    def test_alerts(self):
        result = runner.invoke(app, ["alerts"])
        assert result.exit_code == 0

    def test_alerts_brief(self):
        result = runner.invoke(app, ["alerts", "--format", "brief"])
        assert result.exit_code == 0

    def test_alerts_json(self):
        result = runner.invoke(app, ["alerts", "--format", "json"])
        assert result.exit_code == 0


class TestConfigCLI:
    def test_list(self):
        result = runner.invoke(app, ["config", "list"])
        assert result.exit_code == 0
        assert "ai.provider" in result.output

    def test_set_and_get(self):
        result = runner.invoke(app, ["config", "set", "test.key", "test_val"])
        assert result.exit_code == 0
        result = runner.invoke(app, ["config", "get", "test.key"])
        assert result.exit_code == 0
        assert "test_val" in result.output


class TestDashboardCLI:
    def test_dashboard(self):
        result = runner.invoke(app, ["dashboard"])
        assert result.exit_code == 0
        assert "Beach House" in result.output
        # Should include today's date
        assert "2026" in result.output


class TestNoteCLI:
    def test_add_and_list(self):
        result = runner.invoke(app, ["note", "add", "CLI test note", "--to", "property:beach-house"])
        assert result.exit_code == 0
        result = runner.invoke(app, ["note", "list", "--to", "property:beach-house"])
        assert result.exit_code == 0
        assert "CLI test note" in result.output


class TestTemplateCLI:
    def test_seed_and_list(self):
        result = runner.invoke(app, ["template", "seed"])
        assert result.exit_code == 0
        result = runner.invoke(app, ["template", "list"])
        assert result.exit_code == 0
        assert "Spring" in result.output

    def test_run(self):
        result = runner.invoke(app, ["template", "run", "spring-opening", "--property", "beach-house", "--due", "2026-06-01"])
        assert result.exit_code == 0
        assert "tasks created" in result.output


class TestAssetCLI:
    def test_list(self):
        result = runner.invoke(app, ["asset", "list"])
        assert result.exit_code == 0
        assert "Sub-Zero" in result.output


class TestExportCLI:
    def test_export_csv(self):
        result = runner.invoke(app, ["export", "csv", "property"])
        assert result.exit_code == 0
        assert "beach-house" in result.output

    def test_export_template(self):
        result = runner.invoke(app, ["export", "csv", "property", "--template"])
        assert result.exit_code == 0
        assert "id,slug,name" in result.output


class TestDoctorCLI:
    def test_doctor(self):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_backup(self):
        result = runner.invoke(app, ["backup"])
        assert result.exit_code == 0
        assert "Backup created" in result.output


class TestAutomationCLI:
    def test_digest(self):
        result = runner.invoke(app, ["auto", "digest", "--format", "brief"])
        assert result.exit_code == 0
        assert "Daily Digest" in result.output

    def test_run_all(self):
        result = runner.invoke(app, ["auto", "run-all"])
        assert result.exit_code == 0
        assert "Automation run complete" in result.output
