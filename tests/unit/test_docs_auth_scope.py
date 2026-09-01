"""G0 · SPEC-010 §2 — the four doc repairs, as a regression gate (B1–B4).

**Two PRDs disagreed about whether email/password authentication exists.** `SAAS_PRD:105`
listed "Non-Google auth" as out of scope for GA; `ONBOARDING_AUTH_RBAC:11` says it *owns*
authentication and `:60` promised the layer was abstracted so email/password could be added.

Both sentences were written in good faith and neither was obviously wrong on its own. Together
they meant a reader's answer to "can we do this?" depended on which document they opened —
which is worse than either answer, because it is invisible until someone builds against the
wrong one.

These tests do not assert that the resolution is *correct* — that was a founder decision. They
assert the resolution stays **visible**, because the likely regression is a tidy-up edit that
shortens the amended lines back to something briefer and equally contradictory.

Same construction as `test_docs_ui_scope.py` and `test_docs_dns.py`: the thing being protected
is a distinction, not a fact.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAAS_PRD = ROOT / "docs" / "product" / "SAAS_PRD.md"
ONBOARDING = ROOT / "docs" / "product" / "ONBOARDING_AUTH_RBAC.md"


def _lines(path: pathlib.Path) -> list[tuple[int, str]]:
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1))


def test_non_google_exclusion_is_narrowed():
    """**B1** — the out-of-scope list must not read as a blanket ban on non-Google auth.

    The exclusion itself survives: additional third-party IdPs (Apple, Microsoft, SAML) are
    still deferred. What must not survive is the bare phrase, which contradicted the owning
    PRD.

    Asserted structurally rather than by exact string, so a reword that keeps the distinction
    passes and a tightening that drops it fails.
    """
    text = SAAS_PRD.read_text(encoding="utf-8")

    bare = [
        f"{n}: {line.strip()[:100]}"
        for n, line in _lines(SAAS_PRD)
        if re.search(r"non-google auth", line, re.IGNORECASE)
        and not re.search(r"amended|corrected|previously|read", line, re.IGNORECASE)
    ]
    assert not bare, (
        "SAAS_PRD still lists 'Non-Google auth' as out of scope without qualification. That "
        "contradicts ONBOARDING_AUTH_RBAC §3, which owns authentication and ships "
        f"email/password (SPEC-010 D1):\n  {bare}"
    )

    # And the positive half: the scope section must say email/password is in.
    scope = text.split("### 6.2")[1][:2000] if "### 6.2" in text else text
    assert re.search(r"email/password", scope, re.IGNORECASE), (
        "the out-of-scope section no longer mentions email/password at all. Removing the "
        "contradiction by deleting both sides leaves the next reader with no answer"
    )


def test_onboarding_prd_matches_the_build():
    """**B2/B3** — the owning PRD must describe what was actually built.

    Two claims it carried are now false, and both would mislead someone planning work:

    * *"No passwords"* — email/password ships.
    * *"without touching call sites"* — measured false. `IdentityProvider`'s three methods are
      all OAuth-shaped and a password login implements none of them.
    """
    text = ONBOARDING.read_text(encoding="utf-8")

    stale = [
        f"{n}: {line.strip()[:100]}"
        for n, line in _lines(ONBOARDING)
        if "without touching call sites" in line
        and not re.search(r"corrected|previously|false", line, re.IGNORECASE)
    ]
    assert not stale, (
        "ONBOARDING_AUTH_RBAC still promises email/password can be added 'without touching "
        "call sites'. Measured false: IdentityProvider is authorization_url / exchange_code / "
        f"verify, and a password login implements none of them (SPEC-010 §0.2):\n  {stale}"
    )

    assert "create_session" in text, (
        "the correction does not name the seam that *does* generalise. Saying the old promise "
        "was wrong without saying what replaces it leaves the reader worse off than before"
    )

    # B3 — the identity-key rule must distinguish the two identity types.
    assert re.search(r"password identity is keyed on", text, re.IGNORECASE), (
        "the PRD still states only that identity is keyed on Google `sub`. A password user has "
        "no sub, and how they are keyed is exactly the design question SPEC-010 D3 answers"
    )
    assert "password_hash IS NOT NULL" in text, (
        "the partial-index condition is not recorded. Without it a reader would reasonably "
        "make `users.email` unique table-wide, which breaks Google identity"
    )


def test_the_google_identity_rule_is_intact():
    """The line that must **not** change (B3's other half).

    *"Email is display/contact metadata and can change without breaking identity"* is correct
    and load-bearing for Google users — `test_auth.py:283` asserts it, and keying on email
    would orphan memberships when someone changes their Google address.

    Without this arm, an over-eager application of B3 could rewrite the rule to "identity is
    keyed on email" and silently break the property two other tests defend.
    """
    text = ONBOARDING.read_text(encoding="utf-8")
    assert re.search(r"keyed on Google `sub`", text), (
        "the Google identity rule is gone. SPEC-010 adds a second keying rule for password "
        "users; it does not replace this one"
    )
    assert re.search(r"can change.*without breaking identity", text, re.IGNORECASE), (
        "the rationale for email-is-not-identity was removed. It is why `users.email` is "
        "deliberately non-unique, and the next person to 'fix' that column needs to find it"
    )


def test_the_invitee_question_is_answered_not_deleted():
    """**B4** — `ONBOARDING` §11 Q3 asked how non-Google invitees accept. SPEC-010 answers it.

    Struck through rather than removed, and the answer names *why* it works — the invite is
    keyed on its own token, not on the identity method. That differs from what the question
    guessed (`IdentityProvider` carrying it), and recording the difference is the point: a
    question closed for the wrong reason reopens later.
    """
    text = ONBOARDING.read_text(encoding="utf-8")
    assert re.search(r"Non-Google invitees", text), (
        "Q3 was deleted rather than answered. An open question that vanishes leaves no trace "
        "that it was ever resolved, or how"
    )
    assert re.search(r"SPEC-010", text), "the answer does not cite the spec that provides it"
    assert re.search(r"invite token", text, re.IGNORECASE), (
        "the answer does not say why it works. 'Answered' without a mechanism is an assertion, "
        "and the mechanism here is not the one the question anticipated"
    )
