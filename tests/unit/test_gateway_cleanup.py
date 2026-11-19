"""G9 + G10 · §6 Steps 9 and 10 — the cutover gates and the coverage gap (A22, A23, A24).

**A22 and A23 cannot be honestly discharged by this run, and these tests say so in code.**

N10 is a halt instruction, not a preference: *"Do not delete the Baileys bridge before the
Cloud API is proven in production. Step 9 after Step 7. `bridge/` is the only working WhatsApp
transport today; deleting it early makes rollback impossible while O1 is still open."*

There is no production. U4 records that no Meta account exists, U5 that N10's precondition
therefore cannot be met, and U2 that O1 — whether the tier even supports the inventory *group*
the live product routes through — is unanswered. Deleting the only working transport under
those conditions would not be shipping Step 9; it would be removing the rollback path for a
migration that has not started.

**So these two ship as ratchets rather than as claims.** Each measures the current footprint,
asserts it has not *grown*, and states the number that must reach zero at cutover. They pass
today by construction — and that is the point: a gate that will fail the moment someone adds a
new Baileys dependency is worth more now than a gate that cannot run until the deletion
happens. The harness calls this G-baileys; it holds the line rather than proving the step.

A24 (G10) is different: it is fully dischargeable now, and it is discharged.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "mihomes"

#: The Python client for the Node bridge. **This is the module A22 eventually forbids** — not
#: `bridge/` itself, which is JavaScript and invisible to an import check.
BAILEYS_CLIENT = "mihomes.services.gateways.whatsapp.client"

#: Measured 2026-08-30, at G9. Every module importing the Baileys client today.
#:
#: Enumerated rather than counted: a count tells you something moved, a list tells you what.
#: When Step 7 is green in production this set must go to **empty**, and `test_no_baileys_imports`
#: becomes the assertion §8 actually declares instead of the ratchet it is now.
#: **Measured by running the check, not by grepping and trusting the result.** A first draft
#: listed three of these five; the test failed on the two it had missed, which is the ratchet
#: doing its job on the commit that introduced it.
EXPECTED_BAILEYS_IMPORTERS = {
    "cli/automation.py",
    "cli/whatsapp.py",
    "services/gateways/whatsapp/extractor.py",
    "services/gateways/whatsapp/responder.py",
    "services/staff_pto.py",
}

#: `scripts/watchdog.py`'s WhatsApp supervision (D15). **23 references, measured** — which is
#: exactly the figure F6 reports, so the survey's number and this tree agree. (A first draft
#: guessed 11 from a `grep -n` line count, which counts *lines* rather than occurrences; the
#: test caught it.)
#:
#: A23 shrinks this to health checks only, *after* the deletion — the shrink follows, it does
#: not lead, because a watchdog that stops supervising a process that still exists is an outage.
WATCHDOG = ROOT / "scripts" / "watchdog.py"
EXPECTED_WATCHDOG_WHATSAPP_REFS = 23


def _python_files():
    for path in SRC.rglob("*.py"):
        if "__pycache__" not in path.parts:
            yield path


def _importers_of(module: str) -> set[str]:
    """Files importing `module`, as posix paths relative to `src/mihomes`."""
    needle = module.rsplit(".", 1)[0] + "." + module.rsplit(".", 1)[1]
    found = set()
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        if f"from {needle} import" in text or f"import {needle}" in text:
            found.add(path.relative_to(SRC).as_posix())
    return found


# ------------------------------------------------------------------------------------- #
# A22 (G9.1) — BLOCKED by U5. Ships as a ratchet.
# ------------------------------------------------------------------------------------- #
def test_no_baileys_imports():
    """**A22 — cannot be discharged by this run (U5), so this holds the line instead.**

    §8's criterion is *"no import of the Baileys client survives the cutover"*. The cutover has
    not happened and N10 forbids it happening here, so asserting zero importers would be
    asserting something false — the honest test is that the set has not **grown**.

    Two directions, and both matter:

    * a **new** importer fails immediately — the ratchet, and the thing most likely to happen
      while the migration is open, since `whatsapp/client.py` is still the working transport;
    * a **removed** importer also fails — deliberately. That is the cutover happening, and it
      must be a visible act that updates this list and flips the criterion, not a quiet
      subtraction that leaves a stale expectation behind.
    """
    actual = _importers_of(BAILEYS_CLIENT)

    new = sorted(actual - EXPECTED_BAILEYS_IMPORTERS)
    assert not new, (
        f"new Baileys client importers: {new}. The Cloud API (Step 7) is the transport this "
        "phase migrates to — a new dependency on the Node bridge makes Step 9's deletion "
        "larger, and N10 already blocks that deletion until Step 7 is proven in production"
    )

    gone = sorted(EXPECTED_BAILEYS_IMPORTERS - actual)
    assert not gone, (
        f"these no longer import the Baileys client: {gone}. If the cutover has begun, update "
        "EXPECTED_BAILEYS_IMPORTERS in the same commit and say so — an unmaintained expectation "
        "silently stops constraining anything"
    )

    # And the ratchet's own precondition: the module must still exist, or every assertion above
    # is comparing two empty sets and reporting success.
    assert (SRC / "services" / "gateways" / "whatsapp" / "client.py").exists()


def test_the_bridge_is_still_present_because_n10_says_it_must_be():
    """The other half of U5, asserted rather than assumed.

    `bridge/` is JavaScript, so no import check sees it. If it were deleted while O1 is open and
    no production cutover has happened, the WhatsApp gateway would simply stop working — and
    nothing else in this suite would notice, because every WhatsApp test stubs the client.
    """
    bridge = ROOT / "bridge"
    assert bridge.is_dir(), (
        "bridge/ has been deleted, but N10 forbids that until the Cloud API is proven in "
        "production (U5) and O1's group question is answered (U2). This is the rollback path"
    )
    assert (bridge / "index.js").exists()


# ------------------------------------------------------------------------------------- #
# A23 (G9.2) — BLOCKED by U5, for the same reason. Also a ratchet.
# ------------------------------------------------------------------------------------- #
def test_watchdog_scope():
    """**A23 — blocked by U5.** The shrink *follows* the deletion; it cannot lead it.

    D15 says the watchdog should supervise nothing that no longer exists. Today `bridge/` and
    the WhatsApp monitor both exist and both need supervising, so removing that supervision now
    would be an outage rather than a cleanup — the failure mode is a bridge that dies at 3am and
    nobody restarts it.

    So this pins the current footprint. It fails if supervision **grows** (more surface to
    remove later) and if it **shrinks** without this expectation being updated (the cutover
    happening quietly).
    """
    text = WATCHDOG.read_text(encoding="utf-8")
    refs = len(re.findall(r"whatsapp", text, re.IGNORECASE))

    assert refs == EXPECTED_WATCHDOG_WHATSAPP_REFS, (
        f"the watchdog now has {refs} WhatsApp references, not "
        f"{EXPECTED_WATCHDOG_WHATSAPP_REFS}. If Step 9's cutover has begun, update this number "
        "in the same commit — A23's real assertion (that the watchdog supervises nothing that "
        "no longer exists) becomes checkable only once the deletion has happened"
    )

    # The supervision that must eventually go, named so the eventual diff is legible.
    for marker in ("_whatsapp_autostart_enabled", "_whatsapp_bridge_running", "_start_whatsapp_monitor"):
        assert marker in text, f"{marker} is gone — see the note above about updating this test"


# ------------------------------------------------------------------------------------- #
# A24 (G10.1) — G-coverage. **Fully dischargeable now, and discharged.**
# ------------------------------------------------------------------------------------- #
#: Modules that must be **measured** after Step 10. Pure logic, no network — F10's finding is
#: that the omit list is broader than its own justification ("require real network connections")
#: and swallows the tenancy code this whole phase added.
MUST_BE_MEASURED = (
    "services/gateways/identity.py",
    "services/gateways/linking.py",
    "services/gateways/webhook.py",
    "services/gateways/review_common.py",
    "services/gateways/dedup.py",
    "services/gateways/telegram/responder.py",
    "services/gateways/whatsapp/responder.py",
)

#: Modules that **stay** omitted, each with the reason. U8: `cloud_client.py` is network-bound
#: on exactly the reasoning that omits `stripe_provider.py` and the AI providers — testing that
#: Meta's API works is Meta's job, and the seam worth testing is the Protocol boundary, which
#: `FakeCloudClient` covers without credentials.
STAY_OMITTED = {
    "services/gateways/whatsapp/cloud_client.py": (
        "HTTP against Meta's Graph API. U8 — its error handling is exercised by nothing in CI, "
        "and that is an accepted gap, not an oversight"
    ),
    "services/gateways/whatsapp/client.py": (
        "HTTP against the Node bridge. Goes away entirely at Step 9's cutover (A22)"
    ),
    "services/gateways/telegram/client.py": (
        "HTTP against Telegram's Bot API — same network-bound reasoning"
    ),
}


def _omit_patterns() -> list[str]:
    """The `omit` list, read out of `pyproject.toml`."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("omit = [", 1)[1].split("]", 1)[0]
    return re.findall(r'"([^"]+)"', block)


def _is_omitted(rel_path: str, patterns: list[str]) -> bool:
    import fnmatch

    full = f"src/mihomes/{rel_path}"
    return any(fnmatch.fnmatch(full, p) for p in patterns)


@pytest.mark.parametrize("module", MUST_BE_MEASURED)
def test_coverage_not_omitted(module):
    """**A24** — every new gateway module reports coverage.

    Parametrized rather than looped so a failure names *which* module is unmeasured, and
    derived from the real `omit` list rather than from a transcription of it.

    F10's finding is that `*/services/gateways/whatsapp/*` and `.../telegram/*` omit whole
    directories on a justification — *"require real network connections"* — that is true of the
    HTTP clients and false of everything else in them. This phase's tenancy code lands squarely
    inside those globs, so the modules carrying D11/D12 would have shipped unmeasured.
    """
    assert (SRC / module).exists(), f"{module} does not exist — this list is stale"

    patterns = _omit_patterns()
    assert not _is_omitted(module, patterns), (
        f"{module} is excluded from coverage by one of {patterns}. It is pure logic with no "
        "network dependency, and it carries this phase's tenancy boundary — an unmeasured "
        "module is where a scoping bug hides"
    )


@pytest.mark.parametrize("module,reason", sorted(STAY_OMITTED.items()))
def test_the_still_omitted_modules_each_name_a_reason(module, reason):
    """The other side of A24: what stays omitted must say **why** (U8).

    Same construction as `ALLOWLIST_MECHANISMS` and `UNFILTERED_CLASSES`: an exemption that
    nothing checks is the cheapest way to make a gate pass — widen one glob and the whole
    directory disappears from measurement with no diff anybody reads. Naming each one means the
    narrowing above cannot be quietly undone.
    """
    assert (SRC / module).exists(), f"{module} no longer exists — remove its STAY_OMITTED entry"
    assert reason.strip(), f"{module} is omitted with no reason given"
    assert _is_omitted(module, _omit_patterns()), (
        f"{module} is now measured, which may well be an improvement — but its STAY_OMITTED "
        "entry says it should not be, so remove the entry deliberately rather than leaving a "
        "claim that no longer matches the config"
    )


def test_the_omit_list_no_longer_swallows_whole_gateway_directories():
    """The narrowing itself, asserted as a property rather than as a diff.

    Before Step 10 the list carried `*/services/gateways/whatsapp/*` and `.../telegram/*` —
    directory-wide globs. Those are what F10 named, and a future edit that restores one would
    re-hide every module `MUST_BE_MEASURED` names while all of the tests above still pass, since
    each checks only its own path.
    """
    offenders = [
        p
        for p in _omit_patterns()
        if p.rstrip("*").rstrip("/").endswith(("gateways/whatsapp", "gateways/telegram"))
    ]
    assert not offenders, (
        f"these omit patterns exclude entire gateway directories: {offenders}. Step 10 narrows "
        "the list to the genuinely network-bound modules — a directory glob re-hides the "
        "tenancy code this phase added"
    )
