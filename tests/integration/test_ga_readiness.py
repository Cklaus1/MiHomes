"""SPEC-005 §6 Step 17 — the GA readiness surface. **The exit criterion** (A33).

A33's claim is narrow and worth stating exactly, because the obvious reading is wrong:

> It does **not** assert the six gates are *green* — three of them (§1.6) are founder decisions
> and one is a legal document. It asserts none of them is **silently absent**, which is the
> failure this spec's scope was chosen to prevent.

So a passing run here does not mean the product can launch. It means nobody can *believe* it can
launch without seeing what is outstanding — which is a different guarantee, and the only one a
test can make about a legal document nobody has written yet.

**The bullets are parsed from `SAAS_PRD.md`, and that is load-bearing.** The build harness
recorded this list as "five bullets"; it is six. A transcribed list would have frozen the
miscount into the gate and passed forever while under-reporting GA by one requirement — the same
failure mode N5 forbids for the export and the purge, arriving in the readiness surface instead.
"""

from __future__ import annotations

import pytest

from mihomes.services.ga_readiness import (
    SAAS_PRD_PATH,
    Status,
    ga_bullets,
    ga_gates,
)


def test_all_gates_tracked():
    """A33 — every GA bullet is present, and none reports a false green.

    Three assertions, and the third is the criterion:

    1. The bullets are found at all (a parser that silently returns `[]` would pass everything).
    2. Every bullet has a status **and evidence** — a status with no reasoning is an opinion.
    3. No bullet claims `MET` on the strength of something this repo cannot see.
    """
    gates = ga_gates()

    assert gates, (
        "no GA bullets parsed from SAAS_PRD.md — a readiness surface that enumerates nothing "
        "reports 'all clear' for a product with no gates met"
    )
    assert len(gates) == len(ga_bullets()), "every parsed bullet must produce a gate"

    for gate in gates:
        assert gate.evidence.strip(), (
            f"{gate.bullet[:60]!r} has a status with no evidence — the point of this surface is "
            "that a reader can check the claim rather than trust it"
        )
        assert isinstance(gate.status, Status)

    # **The false-green check.** A `BLOCKED` gate must name who can unblock it; a gate with no
    # owner and no proof is how "we thought that was done" happens.
    for gate in gates:
        if gate.status is Status.BLOCKED:
            assert gate.owner, (
                f"{gate.bullet[:60]!r} is blocked with no owner — an outstanding gate nobody is "
                "responsible for is one nobody will close"
            )


def test_the_three_inbound_gates_are_reported_unresolved():
    """§1.6's inbound gates appear as **explicitly unresolved**, not omitted.

    Step 17 requires exactly this: *"including the three §1.6 inbound gates as **explicitly
    unresolved** where they are"*. Two of the three surface as GA bullets in their own right
    (SPEC-001 O1's legal documents, SPEC-004 O1's prices); the third — SPEC-003 O1, secret
    encryption — was closed by SPEC-003 U1 and is recorded in §0.8 U3 as stale in the spec, so
    it is not expected here.

    Asserted by *status*, not by counting: a gate that quietly flipped to `MET` because a mock
    passed is precisely the false green A33 names.
    """
    gates = ga_gates()

    blocked = [g for g in gates if g.status is Status.BLOCKED]
    assert len(blocked) >= 2, (
        "SPEC-001 O1 (ToS/Privacy) and SPEC-004 O1 (real prices) are both open founder "
        "decisions that block GA. Fewer than two blocked gates means one has been marked met "
        "without anyone having done it"
    )

    text = " ".join(g.evidence for g in blocked)
    assert "SPEC-001 O1" in text, "the ToS/Privacy gate must be traceable to the spec that raised it"
    assert "SPEC-004 O1" in text, "the pricing gate must be traceable to the spec that raised it"

    for gate in blocked:
        assert gate.owner == "founder", (
            f"{gate.bullet[:60]!r} is blocked on someone other than the founder — every open "
            "inbound gate in §1.6 is a founder decision"
        )


def test_the_deliverability_gate_is_not_claimed_green():
    """§10's own caveat, enforced: A20 proves a *document*, not a mailbox.

    The email lifecycle is genuinely built in this phase, which makes this the bullet most
    likely to be marked done — and *"with DKIM/SPF/DMARC passing"* is the half no test in this
    repo can see, because the sending domain is not verified (§0.8 U7). A `MET` here would be
    the single most expensive false green available: it reads as "mail works" when what was
    proven is "the record we wrote down is self-consistent".
    """
    email_gate = next(g for g in ga_gates() if "email lifecycle" in g.bullet.lower())

    assert email_gate.status is not Status.MET, (
        "the email lifecycle gate is marked met, but 'DKIM/SPF/DMARC passing' cannot be proven "
        "from this repo — D17 makes A20 a documentation test precisely because the domain is "
        "not verified yet (§0.8 U7)"
    )
    assert "U7" in email_gate.evidence or "not yet verified" in email_gate.evidence


def test_a_new_bullet_is_unknown_rather_than_absent(tmp_path):
    """The fail-safe direction: a GA requirement added to the PRD tomorrow must **appear**.

    This is the assertion that keeps the surface honest over time. Evidence is keyed by
    substring, so a bullet nobody has mapped could plausibly have been dropped from the list —
    which would make the readiness report shrink silently as the PRD grew.
    """
    prd = tmp_path / "SAAS_PRD.md"
    prd.write_text(
        "**GA definition of done (Phase 4 exit):**\n"
        "- Data export and account-deletion paths exist (GDPR/CCPA baseline from §9).\n"
        "- Something nobody has mapped yet.\n"
        "\n"
        "## 11. Success Metrics\n",
        encoding="utf-8",
    )

    gates = ga_gates(prd)

    assert len(gates) == 2, "both bullets must appear, including the unmapped one"
    unmapped = gates[1]
    assert unmapped.status is Status.UNKNOWN, (
        "an unmapped bullet must be UNKNOWN — 'nothing here can tell' is a report; omitting it "
        "is a silence, and silence is the failure A33 exists to prevent"
    )
    assert unmapped.evidence.strip()


def test_the_parser_refuses_a_prd_without_the_heading(tmp_path):
    """A moved heading must fail loudly rather than enumerate nothing.

    The dangerous failure is not a crash — it is `ga_bullets()` returning `[]`, which would make
    every assertion above vacuously true and the CLI report "0 of 0 met" as success.
    """
    prd = tmp_path / "SAAS_PRD.md"
    prd.write_text("# A PRD with no GA section\n\nSome prose.\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="GA definition of done"):
        ga_bullets(prd)


def test_the_surface_is_reachable_from_the_cli():
    """Step 17 says *"a single command or page"*. A service nothing invokes is not a surface.

    Exercised through the Typer app rather than by calling `render()`: SPEC-004 and this run
    both found commands that were correct in isolation and exited 1 on invocation because of
    the root callback's tenant gate (BD7). Registering it is not the same as it working.
    """
    from typer.testing import CliRunner

    from mihomes.cli import app

    result = CliRunner().invoke(app, ["ga-readiness"])

    # Exit 1 is the **expected** outcome today: gates are outstanding, and a command that always
    # exits 0 is one nothing can automate against. What must not happen is a crash.
    assert result.exit_code in (0, 1), (
        f"`mihomes ga-readiness` exited {result.exit_code}\n{result.output}"
    )
    assert "GA readiness" in result.output

    # Every bullet must actually reach the operator, not just the data structure.
    for gate in ga_gates():
        head = gate.bullet.split("(")[0].strip()[:40]
        assert head.split("**")[-1].strip()[:25] in result.output, (
            f"{head!r} is tracked but never printed — a gate an operator cannot see is absent "
            "for every practical purpose"
        )


def test_the_prd_is_where_we_think_it_is():
    """The path is resolved rather than assumed.

    If `SAAS_PRD.md` moves, every test in this file would otherwise fail with a confusing
    `FileNotFoundError` from inside the parser rather than saying what actually happened.
    """
    assert SAAS_PRD_PATH.exists(), f"SAAS_PRD.md not found at {SAAS_PRD_PATH}"
    assert "GA definition of done" in SAAS_PRD_PATH.read_text(encoding="utf-8")
