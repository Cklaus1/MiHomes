"""Configuration service — key-value settings."""

from sqlalchemy.orm import Session

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


# ── Secret handling — SPEC-003 §6 Step 15, F7, O1 ─────────────────────────────
#
# **Masking is a DISPLAY concern, and that distinction is the whole design.** `get_config` must
# keep returning the real value: it is how the AI provider gets its API key. Masking there would
# not be safer, it would break the feature. So the mask is applied at the two places a human
# reads a value — the web settings page and `mihomes config list` — and nowhere else.
#
# **This does not make the secrets safe** (§10). O1 is open: values remain plaintext in
# `configurations.value`. Masking stops shoulder-surfing, screenshots, and pasted terminal output;
# it does nothing about a database disclosure. Saying so plainly matters more than the mask does.

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


def get_config(session: Session, key: str, default: str | None = None) -> str | None:
    config = _lookup(session, key)
    if config is not None:
        return config.value
    return DEFAULTS.get(key, default)


def set_config(session: Session, key: str, value: str) -> Configuration:
    config = _lookup(session, key)
    if config:
        config.value = value
    else:
        # account_id is stamped by the before_flush listener (G8.3).
        config = Configuration(key=key, value=value)
        session.add(config)
    session.flush()
    return config


def list_config(session: Session) -> list[dict[str, str]]:
    stored = {c.key: c.value for c in session.query(Configuration).all()}
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
