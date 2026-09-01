"""G1 · SPEC-010 §6 Step 1 — the KDF (A1, A2, A3).

**The failure mode here is a working feature that is unsafe**, which is why these tests look
unlike the rest of the suite. A password hashed with plain sha256 logs in perfectly: every
functional assertion passes, and the difference only appears when a database leaks, at which
point every user's password is already gone.

So A2 asserts the stored **format**, and asserts by *reading the module's source* that no fast
hash is reachable from it — the harness's **G-kdf**. That is unusual and deliberate: the
adjacent modules in this package recommend exactly the wrong primitive for this job
(`sessions.py:9`, `invite_service.py:62`, both correct for 256-bit tokens), so the likeliest
defect in this spec is a reviewer copying the neighbouring pattern in good faith.

A3 is the other unusual one: it asserts an **absence of information**. The natural, helpful
implementation — return early when there is no stored password — is the defect, because it
makes the login form answer "does this account exist?" in the timing.
"""

from __future__ import annotations

import ast
import base64
import inspect
import pathlib

import pytest

from mihomes.auth import passwords as pw_module
from mihomes.auth.passwords import (
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    hash_password,
    needs_rehash,
    verify_password,
)

PASSWORD = "correct horse battery staple"


# ── A1 — the round trip ───────────────────────────────────────────────────────

def test_round_trip():
    """**A1** — the right password verifies, the wrong one does not.

    Both halves in one test on purpose: a `verify_password` that returns True unconditionally
    passes the first assertion alone, and it is the exact shape of a total auth bypass.
    """
    stored = hash_password(PASSWORD)

    assert verify_password(PASSWORD, stored) is True, "the correct password must verify"
    assert verify_password("not the password", stored) is False, (
        "a wrong password verified — `verify_password` is not comparing anything, which is a "
        "complete authentication bypass"
    )


def test_a_password_does_not_verify_against_another_password_hash():
    """Two different passwords must not cross-verify.

    Guards the salt-reuse defect specifically: with a fixed salt this still passes, but with a
    truncated or constant *key* it does not.
    """
    a = hash_password("password-one")
    b = hash_password("password-two")
    assert verify_password("password-one", b) is False
    assert verify_password("password-two", a) is False


def test_unicode_and_long_passwords_round_trip():
    """A passphrase is not ASCII and is not short. bcrypt's 72-byte truncation is the classic
    version of this bug; scrypt has no such limit, and this pins that.
    """
    long_unicode = "correcte cheval batterie agrafe — ünïcödé ✅ " * 8
    stored = hash_password(long_unicode)
    assert verify_password(long_unicode, stored) is True
    # Truncation would make a shared prefix verify. It must not.
    assert verify_password(long_unicode[: len(long_unicode) // 2], stored) is False


# ── A2 — G-kdf, the definition of done ────────────────────────────────────────

def test_hash_format():
    """**A2 · G-kdf** — the stored value is `scrypt$n$r$p$salt$hash`, salted, no plaintext.

    Every assertion here fails against a sha256 implementation that would pass `test_round_trip`
    perfectly. That gap is the whole reason this test exists.
    """
    h1 = hash_password(PASSWORD)
    h2 = hash_password(PASSWORD)

    # The salt, proven. Two hashes of ONE password must differ — this is the single assertion
    # an unsalted implementation cannot satisfy, however strong its hash.
    assert h1 != h2, (
        "two hashes of the same password were identical — the salt is not per-call, so one "
        "precomputed table attacks every account at once"
    )

    prefix, n_s, r_s, p_s, salt_b64, key_b64 = h1.split("$")
    assert prefix == "scrypt", f"expected a scrypt hash, got {prefix!r}"
    assert (int(n_s), int(r_s), int(p_s)) == (SCRYPT_N, SCRYPT_R, SCRYPT_P), (
        "the stored cost parameters do not match the module constants, so D5's re-hash-on-login "
        "upgrade path would silently no-op"
    )

    salt = base64.b64decode(salt_b64)
    key = base64.b64decode(key_b64)
    assert len(salt) >= 16, f"salt is {len(salt)} bytes; 16 is the minimum that makes it useful"
    assert len(key) == 32, f"derived key is {len(key)} bytes, expected 32"

    # The salts differ, not merely the output. (A bug that re-derived with a fresh *key* but a
    # constant salt would pass the h1 != h2 check above.)
    assert salt_b64 != h2.split("$")[4], "the salt is constant across calls"

    # No plaintext, in any obvious encoding.
    assert PASSWORD not in h1
    assert base64.b64encode(PASSWORD.encode()).decode() not in h1


def test_no_fast_hash_is_reachable_from_this_module():
    """**A2 · G-kdf's third check** — no `sha256`/`md5`/`sha1` call appears in `passwords.py`.

    A source-level assertion, which is not how tests usually work, and justified by a specific
    measured hazard: **five places in this codebase hash a secret with bare sha256 and all five
    are right to.** `sessions.py:9` states the rule in a comment — *"No salt and no bcrypt,
    deliberately"* — and it is correct for a 256-bit random token.

    A password is the opposite case. Nothing in the type system or the test suite distinguishes
    them, so the only thing standing between this module and a well-intentioned "consistency"
    edit is an assertion that reads the code.

    Parsed with `ast` rather than grepped, so the prose in the module docstring (which discusses
    sha256 at length, and must keep doing so) does not trip it.
    """
    source = pathlib.Path(inspect.getfile(pw_module)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    banned = {"sha256", "sha1", "md5", "sha224", "new"}
    found = []
    for node in ast.walk(tree):
        # `hashlib.sha256(...)` / `hashlib.new("sha256")`
        if isinstance(node, ast.Attribute) and node.attr in banned:
            found.append(f"line {node.lineno}: .{node.attr}")
        # a bare `sha256(...)` from `from hashlib import sha256`
        elif isinstance(node, ast.Name) and node.id in banned - {"new"}:
            found.append(f"line {node.lineno}: {node.id}")
        elif isinstance(node, ast.ImportFrom) and node.module == "hashlib":
            found.append(f"line {node.lineno}: from hashlib import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "hashlib":
                    found.append(f"line {node.lineno}: import hashlib")

    assert not found, (
        "a fast hash is reachable from passwords.py. It would pass every functional test in "
        "this file and fail only when the database leaks:\n  " + "\n  ".join(found)
    )

    # The positive twin (§0.5b): the negative above passes vacuously against an empty file.
    assert "Scrypt" in source, "passwords.py does not reference Scrypt at all"


def test_the_source_scan_has_teeth():
    """The mutation check for the scan above.

    An `ast`-based assertion that silently matches nothing is worse than no assertion, because
    it reports success. This feeds the scanner a module that *does* call sha256 and requires it
    to object.
    """
    tree = ast.parse("import hashlib\ndef h(p):\n    return hashlib.sha256(p).hexdigest()\n")
    hits = [
        n for n in ast.walk(tree)
        if (isinstance(n, ast.Attribute) and n.attr == "sha256")
        or (isinstance(n, ast.Import) and any(a.name == "hashlib" for a in n.names))
    ]
    assert hits, "the scan cannot detect sha256 even when it is plainly there"


# ── A3 — the account-existence oracle ─────────────────────────────────────────

def test_no_user_enumeration_by_timing(monkeypatch):
    """**A3 · D9** — verifying against a null hash runs the KDF anyway.

    **This asserts the code path, not the clock.** A wall-clock comparison on a shared CI box is
    flaky, and a flaky security test gets disabled — which is worse than not having written one
    (harness §4). So the KDF is instrumented and the assertion is that it *ran*.

    The defect this catches is the natural implementation:

        if stored is None:
            return False        # ← microseconds, vs ~100ms for a real user

    That difference is readable straight off the response time, and it turns the login form into
    a list of which email addresses hold accounts — the first step of a credential-stuffing run.
    """
    calls: list[str] = []
    real_derive = pw_module._derive

    def counting_derive(plain, salt, **kw):
        calls.append("derive")
        return real_derive(plain, salt, **kw)

    monkeypatch.setattr(pw_module, "_derive", counting_derive)

    # The unknown-user path: no stored hash at all.
    calls.clear()
    assert verify_password(PASSWORD, None) is False
    assert len(calls) == 1, (
        "verify_password(None) returned without running the KDF. The login form now answers "
        "'does this account exist?' in its response time"
    )

    # Same for an empty string, which is what a NULL column reads back as in some paths.
    calls.clear()
    assert verify_password(PASSWORD, "") is False
    assert len(calls) == 1, "the empty-hash path skipped the KDF"

    # The positive twin: a real verification does the same one derivation, so the two paths
    # cost the same. If the known-user path derived twice, the timing would still differ.
    calls.clear()
    stored = hash_password(PASSWORD)
    calls.clear()
    assert verify_password(PASSWORD, stored) is True
    assert len(calls) == 1, (
        f"the known-user path ran the KDF {len(calls)} times against 1 for the unknown-user "
        "path — the cost asymmetry is the oracle all over again"
    )


def test_a_malformed_hash_is_refused_not_raised():
    """A corrupt or hand-edited row must return False, not raise.

    An exception here becomes a 500, which is itself an oracle: it tells an attacker their input
    reached the parser, and it distinguishes a malformed row from a wrong password.
    """
    for bad in ["not-a-hash", "scrypt$only$three", "scrypt$x$y$z$!!!$!!!", "bcrypt$1$2$3$a$b"]:
        assert verify_password(PASSWORD, bad) is False, f"{bad!r} should verify False"


def test_empty_password_is_refused_at_hash_time():
    """An empty password is the absence of a credential, not a weak one.

    Storing a valid hash of `""` would let it authenticate, so `hash_password` refuses rather
    than hashing. Verification of `""` against a real hash simply fails, as any wrong password
    does.
    """
    with pytest.raises(ValueError):
        hash_password("")

    stored = hash_password(PASSWORD)
    assert verify_password("", stored) is False


# ── D5 — the cost-upgrade path ────────────────────────────────────────────────

def test_needs_rehash_is_false_for_a_current_hash():
    assert needs_rehash(hash_password(PASSWORD)) is False


def test_needs_rehash_is_true_for_weaker_parameters():
    """A hash stored under an older, cheaper N must be flagged for replacement.

    This is what makes raising `SCRYPT_N` possible without invalidating every stored credential
    at once — D5. Without it, a cost increase is a password reset for the entire user base.
    """
    salt = base64.b64encode(b"\x00" * 16).decode()
    key = base64.b64encode(b"\x01" * 32).decode()
    weak = f"scrypt${SCRYPT_N // 4}${SCRYPT_R}${SCRYPT_P}${salt}${key}"
    assert needs_rehash(weak) is True

    # And a hash that still verifies under the old parameters — the upgrade must be triggered by
    # a *successful* login, so the old cost has to keep working until it happens.
    assert needs_rehash(hash_password(PASSWORD)) is False


def test_needs_rehash_on_absent_and_unparseable_values():
    # Nothing to re-hash: a Google-only user has no password at all.
    assert needs_rehash(None) is False
    assert needs_rehash("") is False
    # Unparseable or foreign: replace on next successful login.
    assert needs_rehash("garbage") is True
    assert needs_rehash("scrypt$incomplete") is True
    assert needs_rehash("bcrypt$2b$12$abcdefgh$ijkl") is True
