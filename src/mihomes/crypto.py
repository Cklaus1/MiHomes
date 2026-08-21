"""At-rest encryption for stored credentials — closes SPEC-003 O1 / U1.

**What this protects against, stated first, because a crypto module that oversells itself is worse
than none.** Until now provider API keys and bot tokens sat in `configurations.value` as readable
text. SPEC-003 Step 15 masked them on *display* and refused to write new ones (N11), and said
plainly that masking "does nothing about a database disclosure." This module is the part that does:
a backup file, a stolen disk, a `pg_dump` in a support ticket, or a `SELECT * FROM configurations`
by anyone with read access now yields ciphertext.

It does **not** protect against someone who can read the process environment or the file holding
`MIHOMES_SECRET_KEY` — that is the same person who can read the decrypted value out of the running
process anyway. Encryption at rest moves the secret from "in the database" to "in the environment";
it does not make it disappear. Founder decision, 2026-08-20: env var, chosen over an OS keychain
because the deployment target is a headless VM with no logged-in session to unlock one, and a
keychain that silently falls back to an env var is two mechanisms with one of them unaudited.

**The `enc:v1:` prefix is the load-bearing design choice.** A stored value is self-describing, so:

- legacy plaintext is recognisable and can be read without a flag, a schema change, or a guess;
- rotation to `v2` (a new algorithm, a rewrapped key) can read old values while writing new ones;
- and a value that *looks* encrypted but cannot be decrypted fails loudly rather than being handed
  to an API as if it were a key.

That last point is why `decrypt` raises. The tempting failure mode — return the ciphertext, or
`None`, when no key is configured — turns a missing environment variable into an authentication
error three layers away, at the Anthropic client, with a message about an invalid key. Loud and
local beats quiet and distant.
"""

from __future__ import annotations

import os

__all__ = [
    "ENCRYPTED_PREFIX",
    "EncryptionUnavailable",
    "SECRET_KEY_ENV",
    "UndecryptableValue",
    "decrypt",
    "encrypt",
    "generate_key",
    "is_encrypted",
    "secret_key",
]

#: The environment variable holding the Fernet key. Documented in `.env.example`, `fly.toml`,
#: `scripts/watchdog.py` and `scripts/Start-MiHomes.ps1` — every process that reads a secret needs
#: it, and two of those are separate processes that would otherwise fail in isolation.
SECRET_KEY_ENV = "MIHOMES_SECRET_KEY"

#: Version marker. `v1` is Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`).
ENCRYPTED_PREFIX = "enc:v1:"


class EncryptionUnavailable(RuntimeError):
    """Raised when a value must be encrypted but no key is configured.

    Separate from `UndecryptableValue` because the two have different fixes: this one means "set
    the variable", the other means "you set the *wrong* variable, or the row predates it."
    """


class UndecryptableValue(RuntimeError):
    """Raised when a value carries the encrypted prefix but cannot be decrypted.

    Either no key is configured, or the configured key is not the one that encrypted it. Both are
    operator errors that must surface *here* rather than as a downstream auth failure.
    """


def secret_key() -> str | None:
    """The configured Fernet key, or `None` if unset.

    Returns `None` rather than raising: absence is fatal for *some* callers (decrypting an
    encrypted value) and merely a constraint for others (`set_config` refusing a secret write, the
    CLI printing a helpful message). The caller knows which; this function does not.

    An empty or whitespace-only variable is treated as unset — an exported-but-blank variable is a
    deployment mistake, not a request to use the empty string as a key.
    """
    raw = os.environ.get(SECRET_KEY_ENV, "")
    return raw.strip() or None


def _fernet():
    """A `Fernet` for the configured key. Raises `EncryptionUnavailable` if there is none.

    The import is deferred so that importing this module — which `config_service` does
    unconditionally — never requires `cryptography` to be installed unless a secret is actually
    handled. That keeps the CLI's non-secret paths working on a partial install.
    """
    key = secret_key()
    if key is None:
        raise EncryptionUnavailable(
            f"{SECRET_KEY_ENV} is not set, so credentials cannot be encrypted or decrypted. "
            f"Generate one with `mihomes config generate-key` and put it in the environment."
        )

    from cryptography.fernet import Fernet

    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:  # ValueError, binascii.Error — both mean "not a Fernet key"
        raise EncryptionUnavailable(
            f"{SECRET_KEY_ENV} is set but is not a valid Fernet key (expected 44 url-safe base64 "
            f"characters — `mihomes config generate-key` prints one): {exc}"
        ) from exc


def generate_key() -> str:
    """A fresh Fernet key, for the operator to place in the environment.

    Lives here rather than in the CLI so that the key format is decided in exactly one place — the
    same reason `ENCRYPTED_PREFIX` is a constant.
    """
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("utf-8")


def is_encrypted(value: str | None) -> bool:
    """Whether this stored value is one of ours.

    A prefix check, not a decryption attempt: callers use this to *decide* whether to decrypt, so
    it must not itself require a key.
    """
    return isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)


def encrypt(plaintext: str) -> str:
    """`plaintext` → `enc:v1:<token>`. Raises `EncryptionUnavailable` with no key.

    Refusing is the point. The alternative — return the plaintext and let the caller store it —
    reintroduces exactly the hole this closes, at the one moment nobody is looking.
    """
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt(value: str | None) -> str | None:
    """The plaintext behind a stored value.

    Three cases, and the middle one is the compatibility path that makes a phased rollout possible:

    - `None` → `None`. An absent value is not an error.
    - **no prefix → returned unchanged.** Legacy plaintext, written before encryption existed or
      imported from a pre-Postgres SQLite database (`services/importer.py` Core-inserts source rows
      directly). Reading it is *how the system keeps working* while `mihomes config encrypt-secrets`
      has not been run yet.
    - prefixed → decrypted, or **raise**. Never the ciphertext, never `None`.
    """
    if value is None or not is_encrypted(value):
        return value

    from cryptography.fernet import InvalidToken

    token = value[len(ENCRYPTED_PREFIX):]
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except EncryptionUnavailable as exc:
        raise UndecryptableValue(
            f"a stored credential is encrypted but {SECRET_KEY_ENV} is not set, so it cannot be "
            f"read. Set it to the key that encrypted this database."
        ) from exc
    except InvalidToken as exc:
        raise UndecryptableValue(
            f"a stored credential could not be decrypted with the current {SECRET_KEY_ENV}. The "
            f"key has changed, or this value came from a different install. Re-set the credential "
            f"with `mihomes config set`."
        ) from exc
