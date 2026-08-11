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
