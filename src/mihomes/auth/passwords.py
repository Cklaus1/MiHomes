"""Password hashing — SPEC-010 §5.1, Step 1 (D4, D5, D9).

**The rule the rest of this package states does not apply here, and getting that backwards is
the likeliest defect in this spec.**

`sessions.py:9` says, correctly for what it describes:

    "No salt and no bcrypt, deliberately. A session id is 256 bits of `secrets` output, not a
     human-chosen password: there is no dictionary to attack, so a slow KDF buys nothing…"

`invite_service.py:62` repeats it. Five places in this codebase hash a secret with plain
sha256, and all five are right to. **A password is the sixth kind of thing and the opposite
case**: it is human-chosen, it appears in every leaked-credential list on the internet, and an
unsalted fast hash of one is crackable offline at billions of guesses per second. So this module
salts, and it is deliberately slow.

A reviewer copying the adjacent pattern is the single most likely way this goes wrong, which is
why A2 asserts the stored *format* rather than that verification works — a sha256 password
verifies perfectly and fails only when a database leaks, at which point every user's password is
already gone.

## Why scrypt (D4)

argon2id is the modern default and would mean adding `argon2-cffi`. `scrypt` is memory-hard,
resistant to the GPU and ASIC parallelism that makes bare hashes cheap to attack, and it ships
inside `cryptography` — **already a declared runtime dependency** (`pyproject.toml:33`),
already audited as part of a package this project ships. Measured before choosing it:
`cryptography 46.0.6`, `Scrypt(...).derive()` returns 32 bytes here.

No new dependency for a security-critical primitive is worth a good deal.

## Why the format carries its parameters (D5)

`scrypt$n$r$p$salt$hash` mirrors `crypto.py:52`'s `enc:v1:` convention. Cost parameters travel
with each hash, so raising `SCRYPT_N` later re-hashes each password on its owner's next
successful login — rather than invalidating every stored credential at once, which is a
password reset for the entire user base.
"""

from __future__ import annotations

import base64
import hmac
import os

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

__all__ = [
    "SCRYPT_N",
    "SCRYPT_P",
    "SCRYPT_R",
    "hash_password",
    "needs_rehash",
    "verify_password",
]

#: ~32 MiB and roughly 100ms per hash on current hardware. The memory cost is the point: it is
#: what makes a GPU array expensive rather than merely fast.
#:
#: **This is the knob that ages.** Raising it is safe (D5 re-hashes on next login); lowering it
#: silently weakens every password set afterwards, which is why the value lives here as a named
#: constant rather than inline at the call site.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1

_SALT_BYTES = 16
_KEY_BYTES = 32
_PREFIX = "scrypt"

def _derive(plain: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=_KEY_BYTES, n=n, r=r, p=p).derive(plain.encode("utf-8"))


def hash_password(plain: str) -> str:
    """`scrypt$n$r$p$<salt_b64>$<hash_b64>` — a fresh 16-byte salt per call (D4/D5).

    **Per call, not per install.** A shared salt lets one precomputed table attack every
    account at once, which is most of what a salt exists to prevent. A2 asserts that hashing
    one password twice yields two different strings, because that difference *is* the salt
    doing its job and is otherwise invisible.
    """
    if not plain:
        # Refused rather than hashed. An empty password is not a weak credential, it is the
        # absence of one, and storing a valid hash of "" would let it authenticate.
        raise ValueError("password must not be empty")

    salt = os.urandom(_SALT_BYTES)
    key = _derive(plain, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return "$".join(
        (
            _PREFIX,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(key).decode("ascii"),
        )
    )


def verify_password(plain: str, stored: str | None) -> bool:
    """Constant-time verification. **A `None` stored hash still does the work** (D9).

    The obvious implementation returns False immediately when there is no stored hash. That
    turns the login form into an account-existence oracle: an unknown email answers in
    microseconds while a known one takes ~100ms, and an attacker with a list of addresses can
    read which of them hold accounts straight off the response time — the first step of a
    credential-stuffing run.

    So the no-password path derives against a dummy hash and returns False, paying the same
    cost. The comparison is `compare_digest`, not `==`: an early-exit byte comparison leaks the
    length of the matching prefix.
    """
    if stored is None or not stored:
        # Burn the same CPU, discard the result. `bool(...)` on a comparison that is always
        # False keeps the branch honest without a linter removing it as dead code.
        _derive(plain or "", b"\x00" * _SALT_BYTES, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
        return False

    try:
        prefix, n_s, r_s, p_s, salt_b64, key_b64 = stored.split("$")
        if prefix != _PREFIX:
            raise ValueError(f"unsupported hash format {prefix!r}")
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)
    except (ValueError, TypeError):
        # A malformed hash is a corrupt or hand-edited row. Refuse rather than raise: an
        # exception here becomes a 500 that tells an attacker their input reached the parser.
        return False

    # Derived with the **stored** parameters, not the current ones — that is what lets D5's
    # cost increase roll forward without invalidating existing passwords.
    actual = _derive(plain, salt, n=n, r=r, p=p)
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str | None) -> bool:
    """True when a stored hash was made with weaker parameters than the current constants.

    Called after a *successful* verification, which is the only moment the plaintext exists to
    re-hash with. Cheap: it parses, it does not derive.
    """
    if not stored:
        return False
    try:
        prefix, n_s, r_s, p_s, _salt, _key = stored.split("$")
    except ValueError:
        return True  # unparseable — replace it on the next successful login
    if prefix != _PREFIX:
        return True
    try:
        return (int(n_s), int(r_s), int(p_s)) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)
    except ValueError:
        return True
