"""The GA readiness surface — SPEC-005 §6 Step 17, the exit criterion (A33).

**A33 does not assert the gates are green.** It asserts none is *silently absent*, which is a
different and much more useful claim: three of them are founder decisions and one is a legal
document, so a test that required green could only ever be red. What this catches is the failure
this spec's scope was chosen to prevent — shipping while believing a gate was met because nobody
wrote down that it was not.

## Every status here is one of three things, and `UNKNOWN` is the important one

- `MET` — something in this repo proves it, and the proof is named.
- `BLOCKED` — a person must do something. The owner is named.
- `UNKNOWN` — **nothing here can tell**. Not a synonym for blocked: `BLOCKED` means we know what
  is missing, `UNKNOWN` means the repo has no way to see. A gate that says `UNKNOWN` is honest;
  a gate that says `MET` because a test passed against a mock is the false green A33 exists for.

## The bullets are parsed from `SAAS_PRD.md`, never transcribed

A hand-copied list is correct the day it is written and silently wrong the first time the PRD is
edited — the same reasoning as D14/D15's `Base.metadata` enumeration, and as A15's derivation of
workloads from the Typer app. The harness recorded this list as "five bullets"; it is **six**.
That miscount is exactly what a transcription would have frozen into the gate.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from enum import Enum

__all__ = ["Gate", "Status", "ga_gates", "ga_bullets", "SAAS_PRD_PATH"]

SAAS_PRD_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "docs" / "product" / "SAAS_PRD.md"
)

_HEADING = "**GA definition of done (Phase 4 exit):**"


class Status(str, Enum):
    MET = "met"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Gate:
    """One GA bullet, its status, and **why we believe that**.

    `evidence` is required rather than optional. A status with no evidence is an opinion, and
    the whole point of this surface is that a reader can check the claim rather than trust it.
    """

    bullet: str
    status: Status
    evidence: str
    owner: str | None = None

    @property
    def is_regression_check(self) -> bool:
        """B2: two bullets are re-verification of shipped work, not Phase 4 deliverables.

        `SAAS_PRD` marks them itself. Kept as a property rather than filtered out, because
        "already built" is a status a reader needs to see — dropping them would make the list
        disagree with the PRD it is derived from.
        """
        return "regression check" in self.bullet.lower()


def ga_bullets(path: pathlib.Path | None = None) -> list[str]:
    """The GA bullets, parsed from `SAAS_PRD.md`.

    Stops at the first line that is neither a bullet nor blank, so a later section cannot leak
    in. Returns them verbatim, markdown and all: normalising here would mean the gate list and
    the PRD could differ while looking identical.
    """
    source = (path or SAAS_PRD_PATH).read_text(encoding="utf-8").splitlines()

    try:
        start = next(i for i, line in enumerate(source) if line.strip() == _HEADING)
    except StopIteration:
        raise AssertionError(
            f"{_HEADING!r} not found in {path or SAAS_PRD_PATH} — the GA definition of done is "
            "the exit criterion for this whole phase; if it moved, this surface must follow it "
            "rather than silently enumerate nothing"
        ) from None

    bullets: list[str] = []
    for line in source[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
        elif stripped:
            break
    return bullets


#: What this repo can say about each bullet, keyed by a stable substring of it.
#:
#: Keyed by substring rather than by index: a bullet reordered in the PRD must not silently
#: acquire another's status. A bullet whose key matches nothing comes back `UNKNOWN`, which is
#: the fail-safe direction — a new GA requirement appears as "nothing here can tell" rather than
#: as absent.
_EVIDENCE: dict[str, tuple[Status, str, str | None]] = {
    "Phase 1–3 exit criteria": (
        Status.MET,
        "Regression check (B2). The Phase 1-3 suites run in this repo: tenant isolation, the "
        "RBAC matrix, and Stripe reconciliation each have their own acceptance tests.",
        None,
    ),
    "email lifecycle live": (
        Status.BLOCKED,
        "The lifecycle is built — welcome, invite, receipt, the four-rung dunning ladder, "
        "cancellation, drips, unsubscribe and the outbox all ship in this phase. "
        "**DKIM/SPF/DMARC 'passing' is not provable here**: A20 asserts the documented record "
        "is internally consistent (D17), and no test can verify a domain that is not yet "
        "verified (§0.8 U7).",
        "founder",
    ),
    "grace policy": (
        Status.MET,
        "Regression check (B2), built and tested in Phase 3 — SPEC-004 Step 14, A20. Listing "
        "it as a Phase 4 deliverable invites rebuilding a shipped feature.",
        None,
    ),
    "Data export and account-deletion": (
        Status.MET,
        "SPEC-005 Steps 7 and 8. Export enumerates every TenantOwned table from Base.metadata "
        "(A27) and is tenant-scoped (A6/A26); the purge applies exactly one of three "
        "dispositions to every table (A28) and its deliberate survivors are asserted (A29).",
        None,
    ),
    "Terms of Service": (
        Status.BLOCKED,
        "SPEC-001 O1, the oldest unresolved item in the set (§0.8 U1). The footer links exist "
        "in the template and 404 today. No test can write a legal document, and this one "
        "legally blocks Phase 0's first email capture.",
        "founder",
    ),
    "public signup open": (
        Status.BLOCKED,
        "SPEC-004 O1 — roughly twenty PLACEHOLDER prices and limits (§0.8 U2). The billing "
        "mechanism is proven; **that the prices are right is not something any test asserts**.",
        "founder",
    ),
}


def ga_gates(path: pathlib.Path | None = None) -> list[Gate]:
    """Every GA bullet with its status. **Derived from the PRD, not transcribed.**

    A bullet matching no evidence key is `UNKNOWN` with a reason saying so, rather than omitted:
    silence is the one outcome A33 exists to make impossible.
    """
    gates: list[Gate] = []
    for bullet in ga_bullets(path):
        for key, (status, evidence, owner) in _EVIDENCE.items():
            if key.lower() in bullet.lower():
                gates.append(Gate(bullet, status, evidence, owner))
                break
        else:
            gates.append(
                Gate(
                    bullet,
                    Status.UNKNOWN,
                    "No evidence is recorded for this bullet. It was added to the PRD after "
                    "this surface was written, and nothing here can say whether it is met.",
                    None,
                )
            )
    return gates


def render() -> str:
    """The surface as text, for `mihomes ga-readiness`."""
    lines = ["GA readiness — SAAS_PRD.md §10 'GA definition of done'", ""]
    for gate in ga_gates():
        marker = {Status.MET: "[x]", Status.BLOCKED: "[ ]", Status.UNKNOWN: "[?]"}[gate.status]
        owner = f"  (owner: {gate.owner})" if gate.owner else ""
        lines.append(f"{marker} {gate.bullet}{owner}")
        lines.append(f"      {gate.evidence}")
        lines.append("")

    blocked = [g for g in ga_gates() if g.status is not Status.MET]
    lines.append(
        f"{len(ga_gates()) - len(blocked)} of {len(ga_gates())} met; "
        f"{len(blocked)} outstanding."
    )
    return "\n".join(lines)
