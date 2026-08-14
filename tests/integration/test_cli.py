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


# Imports are deliberately below the MIHOMES_DIR assignment: mihomes.config binds its
# paths at import time, so importing earlier would freeze the real home directory.
from mihomes.cli import app  # noqa: E402
from mihomes.db import get_engine, get_session, init_db  # noqa: E402
from mihomes.tenancy import account_context  # noqa: E402

runner = CliRunner()


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
    from mihomes.tenancy.bootstrap import ensure_default_account
    account_id = ensure_default_account(get_engine())

    with account_context(account_id):
        with get_session() as session:
            from mihomes.models.property import Property
            from mihomes.services.demo import load_demo_data
            if not session.query(Property).filter(Property.slug == "beach-house").first():
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
        # M39: bad dates now raise typer.BadParameter → Click usage error (exit 2)
        # with a friendly "Invalid value for --due" message, not a raw traceback.
        result = runner.invoke(app, ["task", "add", "Bad Date", "--property", "beach-house", "--due", "not-a-date"])
        assert result.exit_code == 2
        assert "Invalid value" in result.output
        assert "Traceback" not in result.output

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


    def test_ratings_render_with_unrated_dimensions(self):
        # L9: a rating that omits cost/communication is stored NULL; the ratings
        # view must render it (dash, "not rated") instead of crashing on None.
        runner.invoke(app, ["vendor", "add", "L9 Vendor"])
        r = runner.invoke(app, ["vendor", "rate", "l9-vendor", "--quality", "5", "--reliability", "4"])
        assert r.exit_code == 0
        r = runner.invoke(app, ["vendor", "ratings", "l9-vendor"])
        assert r.exit_code == 0
        assert "Traceback" not in r.output


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


class TestFormatEnumValidation:
    """L4 — `--format` must be a validated choice, not a free string that
    silently falls back to the default on a typo."""

    def test_report_weekly_valid_format(self):
        result = runner.invoke(app, ["report", "weekly", "--format", "markdown"])
        assert result.exit_code == 0

    def test_report_weekly_rejects_bad_format(self):
        result = runner.invoke(app, ["report", "weekly", "--format", "bogus"])
        assert result.exit_code == 2
        assert "bogus" in result.output or "Invalid value" in result.output

    def test_ai_review_rejects_bad_format(self):
        result = runner.invoke(app, ["ai", "review", "--format", "bogus"])
        assert result.exit_code == 2
        assert "bogus" in result.output or "Invalid value" in result.output


class TestWeatherAcceptGuard:
    """L6 — `weather suggest --accept N` with no property is nonsensical
    (numbering is per-property) and must be rejected before any AI/network
    call, not silently mis-applied across every property."""

    def test_accept_without_property_rejected(self):
        result = runner.invoke(app, ["weather", "suggest", "--accept", "1,2"])
        assert result.exit_code == 1
        assert "property" in result.output.lower()
