"""Configuration service — key-value settings."""

from sqlalchemy.orm import Session

from mihomes import crypto
from mihomes.models.configuration import Configuration

DEFAULTS = {
    "currency.default": "USD",
    "calendar.provider": "manual",
    "ai.provider": "claude",
    # ai.model intentionally omitted — get_ai_model() picks the provider-specific default
    "ai.max_context_tokens": "50000",
    "notifications.format": "rich",
    "retention.audit_years": "2",
    "retention.whatsapp_years": "1",
    "retention.ai_years": "1",
    # weather.default_location is used for properties that have no address set
    "weather.default_location": "Atlanta, GA",
}


# ── Secret handling — SPEC-003 §6 Step 15, F7; O1 CLOSED 2026-08-20 ───────────
#
# **Masking is a DISPLAY concern, and that distinction is the whole design.** `get_config` must
# keep returning the real value: it is how the AI provider gets its API key. Masking there would
# not be safer, it would break the feature. So the mask is applied at the two places a human
# reads a value — the web settings page and `mihomes config list` — and nowhere else.
#
# **Encryption is a STORAGE concern, and it is now separate from masking.** O1 is answered (U1):
# secret values are Fernet-encrypted in the column, keyed from `MIHOMES_SECRET_KEY`. Masking still
# does what it always did — stops shoulder-surfing, screenshots, and pasted terminal output — and
# encryption does the thing masking never could: a `pg_dump`, a backup file or a stolen disk now
# yields ciphertext. Two layers, two threat models, neither substituting for the other.
#
# The three functions that touch stored values all have to participate, and **`list_config` is the
# one that is easy to miss**: it runs its own `session.query(Configuration).all()` rather than
# going through `_lookup`, so a decrypt shim placed only in `get_config` would leave both
# human-facing surfaces rendering base64.
#
# **Only `is_secret` keys are encrypted.** The same table holds high-frequency operational state —
# `telegram.last_update_id` written once per poll, the dedup id list, `poll_lease`'s timestamp —
# and encrypting those would add a Fernet round-trip to the bot's hot path to protect values that
# are not secrets. The marker list is the boundary, and it errs toward over-masking (see
# `is_secret`).

_SECRET_MARKERS = ("api_key", "token", "secret", "password", "credential")


def is_secret(key: str) -> bool:
    """Whether this config key holds a credential.

    Substring matching on a deny-list of markers rather than an explicit key list: the keys are
    user-extensible (`configurations` is a free-form KV store), so an allow-list of known secret
    names would silently fail to mask `ai.anthropic_api_key_backup` the day someone adds it.
    Over-masking a harmless key is a cosmetic annoyance; under-masking a credential is the bug.
    """
    return any(marker in key.lower() for marker in _SECRET_MARKERS)


def mask_value(key: str, value: str | None) -> str | None:
    """The value as a human should see it. Non-secrets pass through unchanged.

    Shows the last four characters, which is enough to answer *"is this the key I think it is?"*
    without revealing anything usable — the convention every payment form uses, and the reason
    this is more useful than a fixed row of dots.
    """
    if value is None or not is_secret(key):
        return value
    if len(value) <= 4:
        # Too short to reveal any of: a 4-character "key" is either a placeholder or already
        # compromised, and showing half of it helps nobody.
        return "••••"
    return f"••••{value[-4:]}"


def list_config_for_display(session: Session) -> list[dict[str, str]]:
    """`list_config` with secrets masked — for the settings page and the CLI.

    A separate function rather than a flag on `list_config`, so a caller that needs real values
    has to say so by calling the other one. A boolean parameter defaulting to "unmasked" is how
    the CLI ended up printing raw API keys in the first place.
    """
    return [
        {**row, "value": mask_value(row["key"], row.get("value")), "secret": is_secret(row["key"])}
        for row in list_config(session)
    ]


def _lookup(session: Session, key: str) -> Configuration | None:
    """Find a config row by key within the current account.

    Not `session.get(Configuration, key)`: SPEC-002 made the primary key the
    composite `(account_id, key)`, so `get()` now demands a 2-tuple and raised
    "Incorrect number of values in identifier to formulate primary key" for every
    caller.

    Filtering on `key` alone is deliberate rather than passing the tuple. The
    account comes from the scoped session (G8), so callers should not have to know
    their own tenant to read their own settings — and a caller that *did* pass an
    account_id could pass the wrong one.
    """
    return (
        session.query(Configuration).filter(Configuration.key == key).one_or_none()
    )


def _for_storage(key: str, value: str | None) -> str | None:
    """The value as it should sit in the column.

    Empty string and `None` pass through untouched, and that is not laziness:
    `services/gateways/dedup.py:176` *releases* its poll lease by writing `""` and relies on the
    read-back being falsy. Encrypting `""` would produce a non-empty ciphertext, and any path that
    checked truthiness before decrypting would conclude the lease was still held — a bot that never
    polls again, from a change that looks like tightening.
    """
    if not value or not is_secret(key):
        return value
    if crypto.is_encrypted(value):
        # Already ciphertext: `encrypt_existing_secrets` re-running, or a caller passing a value it
        # read back. Double-wrapping would still decrypt, once, and leave a value nothing can read.
        return value
    return crypto.encrypt(value)


def get_config(session: Session, key: str, default: str | None = None) -> str | None:
    config = _lookup(session, key)
    if config is not None:
        return crypto.decrypt(config.value)
    return DEFAULTS.get(key, default)


def set_config(session: Session, key: str, value: str) -> Configuration:
    """Store a setting, encrypting it if the key names a credential.

    Raises `crypto.EncryptionUnavailable` when the value is a secret and no key is configured. That
    refusal is deliberate and it is the same shape as the N11 refusal it replaces: writing the
    credential in plaintext "just this once" is how the hole this closes was dug. The caller gets an
    error naming the environment variable, which is a fixable problem; a silently-plaintext
    credential is not, because nobody learns about it.
    """
    stored = _for_storage(key, value)
    config = _lookup(session, key)
    if config:
        config.value = stored
    else:
        # account_id is stamped by the before_flush listener (G8.3).
        config = Configuration(key=key, value=stored)
        session.add(config)
    session.flush()
    return config


def encrypt_existing_secrets(session: Session) -> list[str]:
    """Re-encrypt every legacy-plaintext secret in this account. Returns the keys converted.

    **Deliberately a command and not a migration.** A migration that reads `MIHOMES_SECRET_KEY`
    would be a migration whose result depends on the environment it runs in — and this phase hit
    that trap three times (`0001_pg_baseline`, `0002_rls`, `0004_onboarding_state` all read mutable
    application state and each broke a later revision). A migration is a fixed point in history;
    key material is not. So conversion is an explicit operator action, run when the key is in place.

    Idempotent: an already-encrypted value is skipped, so running it twice converts nothing the
    second time and is safe to put in a deploy script.
    """
    converted = []
    for row in session.query(Configuration).all():
        if not is_secret(row.key) or not row.value:
            continue
        if crypto.is_encrypted(row.value):
            continue
        row.value = crypto.encrypt(row.value)
        converted.append(row.key)
    if converted:
        session.flush()
    return sorted(converted)


def list_config(session: Session) -> list[dict[str, str]]:
    # Decrypting here, not only in `get_config`, is what keeps the settings page and
    # `mihomes config list` from rendering base64 — see the note at the top of this file.
    stored = {c.key: crypto.decrypt(c.value) for c in session.query(Configuration).all()}
    result = []
    all_keys = set(DEFAULTS.keys()) | set(stored.keys())
    for key in sorted(all_keys):
        value = stored.get(key, DEFAULTS.get(key))
        source = "custom" if key in stored else "default"
        result.append({"key": key, "value": value, "source": source})
    return result


def reset_config(session: Session, key: str) -> None:
    config = _lookup(session, key)
    if config:
        session.delete(config)
        session.flush()
