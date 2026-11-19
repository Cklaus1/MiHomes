"""Deploy-doc guards (SPEC-001 A18, D9, D11).

A18 is a docs test **on purpose**. `PRD_REVIEW` A6 records a copy-pasteable *wrong*
DMARC value published in `GTM_LAUNCH_PLAN.md:273`, contradicting `BILLING:224`. A
grep-level test is the cheapest way to stop the wrong value coming back — and the
consequence of the wrong value is that legitimately-signed Resend mail fails DMARC,
which means confirmation emails silently stop arriving and the Phase 0 funnel reads
as zero demand.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DOC = REPO_ROOT / "docs" / "deploy" / "PHASE0-DEPLOY.md"
FLY_TOML = REPO_ROOT / "fly.toml"

# The landing image is its own Dockerfile. The repo's existing `Dockerfile` builds
# the single-user app for the Home Assistant demo compose stack and is still in
# use, so Phase 0 adds a file rather than replacing one (minimal impact).
DOCKERFILE = REPO_ROOT / "Dockerfile.landing"
LEGACY_DOCKERFILE = REPO_ROOT / "Dockerfile"


@pytest.fixture
def doc() -> str:
    assert DEPLOY_DOC.exists(), f"missing {DEPLOY_DOC.relative_to(REPO_ROOT)}"
    return DEPLOY_DOC.read_text(encoding="utf-8")


def test_dmarc_relaxed_alignment(doc):
    """A18 — the DMARC record must NOT carry strict alignment (D11).

    BILLING:224: strict alignment breaks legitimately-signed Resend mail because
    the return-path sits on its own sub-label.
    """
    assert "v=DMARC1" in doc, "the deploy doc must carry the DMARC record"

    # Find the DMARC line(s) and check them specifically, so an unrelated mention
    # of adkim elsewhere in prose cannot pass or fail the test by accident.
    dmarc_lines = [line for line in doc.splitlines() if "v=DMARC1" in line]
    assert dmarc_lines, "no DMARC record line found"

    for line in dmarc_lines:
        assert "adkim=s" not in line, (
            f"strict DKIM alignment in the DMARC record (D11): {line.strip()}"
        )
        assert "aspf=s" not in line, (
            f"strict SPF alignment in the DMARC record (D11): {line.strip()}"
        )
        assert "p=none" in line, "Phase 0 starts at p=none, per BILLING §3"


def test_dmarc_has_a_reporting_address(doc):
    """`rua=` is how you find out mail is failing before the funnel looks dead."""
    dmarc_lines = [line for line in doc.splitlines() if "v=DMARC1" in line]
    assert any("rua=mailto:" in line for line in dmarc_lines)


def test_sending_domain_is_the_subdomain(doc):
    """D12 — send.mihomes.ai, not the apex.

    Isolates transactional reputation from the apex and from future bulk sends.
    """
    assert "send.mihomes.ai" in doc


def test_dns_table_covers_spf_dkim_and_mx(doc):
    """The pre-launch checklist asserts SPF/DKIM/MX pass, so the records must be listed."""
    lowered = doc.lower()
    for record in ("spf", "dkim", "mx", "dmarc"):
        assert record in lowered, f"the DNS table is missing {record.upper()}"


def test_migrations_run_as_a_release_step_not_on_boot(doc):
    """D9/N4 — concurrent `alembic upgrade` on boot is a race across Fly machines."""
    lowered = doc.lower()
    assert "release_command" in lowered or "release command" in lowered

    if FLY_TOML.exists():
        fly = FLY_TOML.read_text(encoding="utf-8")
        assert "release_command" in fly, (
            "fly.toml must run migrations as a release command (D9)"
        )
        assert "alembic" in fly


def test_migration_release_command_targets_the_landing_tree():
    """The landing app owns `alembic_landing/`, not `alembic/`.

    Running the main tree here would try to replay 40 SQLite-era revisions against
    the landing database — which fails, and would create 37 unrelated tables if it
    did not.
    """
    if not FLY_TOML.exists():
        pytest.skip("fly.toml not present")

    fly = FLY_TOML.read_text(encoding="utf-8")
    release = [line for line in fly.splitlines() if "release_command" in line]
    assert release, "no release_command in fly.toml"
    assert any("-n landing" in line or "landing" in line for line in release), (
        f"release_command must target the landing tree: {release}"
    )


def test_no_secrets_committed(doc):
    """Secrets come from `fly secrets`, never the repo (§10)."""
    # Real-shaped credentials, not the literal placeholder names.
    forbidden = [
        r"re_[A-Za-z0-9]{20,}",              # Resend live key
        r"GOCSPX-[A-Za-z0-9_\-]{10,}",       # Google client secret
        r"postgres://[^\s]*:[^\s@]+@",       # DSN with an inline password
    ]
    for pattern in forbidden:
        assert not re.search(pattern, doc), f"possible secret in the deploy doc: {pattern}"


def test_pre_launch_checklist_is_present_and_unchecked(doc):
    """§6 Step 9 — the seven-row checklist, carrying the open decisions.

    O1/O2/O3 are founder decisions that block *launch*, not the build. They belong
    here as unchecked boxes so they stay visible rather than being quietly assumed.
    """
    assert "- [ ]" in doc, "the checklist must ship unchecked — nothing is done yet"

    lowered = doc.lower()
    for item in ("resend", "spam", "https", "app.mihomes.ai"):
        assert item in lowered, f"checklist is missing {item!r}"

    for open_decision in ("O1", "O2", "O3"):
        assert open_decision in doc, f"{open_decision} must be tracked in the checklist"


def test_fly_toml_builds_the_landing_dockerfile():
    """fly.toml must point at Dockerfile.landing, not the default `Dockerfile`.

    Fly picks up `Dockerfile` implicitly when no build config says otherwise, which
    would silently deploy the single-user app's image to a public host.
    """
    if not FLY_TOML.exists():
        pytest.skip("fly.toml not present")

    fly = FLY_TOML.read_text(encoding="utf-8")
    assert "Dockerfile.landing" in fly, (
        "fly.toml must name Dockerfile.landing explicitly — otherwise Fly defaults "
        "to `Dockerfile`, which builds the unauthenticated single-user app (§7-N1)"
    )


def test_the_existing_dockerfile_is_untouched():
    """Phase 0 must not repurpose the single-user app's image.

    Regression guard: the landing Dockerfile was first written *over* the existing
    one, which docker-compose.yml still builds for the Home Assistant demo. Adding
    a file is minimal impact; replacing one breaks a working stack.
    """
    if not LEGACY_DOCKERFILE.exists():
        pytest.skip("no legacy Dockerfile in this tree")

    content = LEGACY_DOCKERFILE.read_text(encoding="utf-8")
    assert "mihomes-landing" not in content, (
        "the existing Dockerfile must not have been converted into the landing image"
    )
    assert "COPY alembic/" in content, (
        "the single-user image still needs its own migration tree"
    )


def test_dockerfile_does_not_run_migrations_or_the_single_user_app():
    """The image serves the landing app only (D1, D9)."""
    if not DOCKERFILE.exists():
        pytest.skip("Dockerfile not present")

    content = DOCKERFILE.read_text(encoding="utf-8")

    # Ignore comments: the file explains *why* mihomes-web is excluded, so a naive
    # substring search over the whole text flags its own rationale.
    directives = [
        line for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    code = "\n".join(directives)

    assert "mihomes-landing" in code, "the image must run the landing entry point"
    assert "mihomes-web" not in code, (
        "§7-N1: the single-user app must not be served from this image"
    )
    # The landing image must not carry the single-user migration tree at all.
    assert "COPY alembic/" not in code, (
        "D1/D3: alembic/ must never reach the landing image — it must not run "
        "against the landing database"
    )

    # Migrations are a release step, so the CMD must not invoke alembic.
    for line in directives:
        if line.strip().upper().startswith(("CMD", "ENTRYPOINT")):
            assert "alembic" not in line.lower(), f"D9: no migrations on boot — {line}"
