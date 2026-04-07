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
}


def get_config(session: Session, key: str, default: str | None = None) -> str | None:
    config = session.get(Configuration, key)
    if config is not None:
        return config.value
    return DEFAULTS.get(key, default)


def set_config(session: Session, key: str, value: str) -> Configuration:
    config = session.get(Configuration, key)
    if config:
        config.value = value
    else:
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
    config = session.get(Configuration, key)
    if config:
        session.delete(config)
        session.flush()
