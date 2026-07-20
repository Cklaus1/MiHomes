"""HA bridge configuration — stored in MiHomes config_service.

When running as a Home Assistant Add-on, the Supervisor automatically injects
SUPERVISOR_TOKEN into the environment. No manual token configuration is needed.

Priority order:
  1. SUPERVISOR_TOKEN env var (add-on mode — zero config)
  2. config_service ha.token (standalone mode — manual setup)
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

from mihomes.services.config_service import get_config, set_config

HA_URL_KEY = "ha.url"
HA_TOKEN_KEY = "ha.token"
HA_DEFAULT_PROPERTY_KEY = "ha.default_property"
HA_ENABLED_KEY = "ha.enabled"

# ── Add-on auto-detection ─────────────────────────────────────────────────────
# When running as an HA Add-on, Supervisor injects these automatically.
_SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
_ADDON_MODE = bool(_SUPERVISOR_TOKEN)

# When in add-on mode, HA is reachable via the supervisor proxy:
_SUPERVISOR_WS_URL = "ws://supervisor/core/websocket"
_SUPERVISOR_API_URL = "http://supervisor/core/api"


def is_addon_mode() -> bool:
    """True when running as an HA add-on (SUPERVISOR_TOKEN is present)."""
    return _ADDON_MODE


def get_supervisor_token() -> str | None:
    """Return the auto-injected supervisor token (add-on mode only)."""
    return _SUPERVISOR_TOKEN


def get_ha_token(session: Session) -> str | None:
    """Return HA token: supervisor token in add-on mode, stored token otherwise."""
    if _ADDON_MODE:
        return _SUPERVISOR_TOKEN
    return get_config(session, HA_TOKEN_KEY)


def get_ha_ws_url(session: Session) -> str | None:
    """Return WebSocket URL: supervisor URL in add-on mode, configured URL otherwise."""
    if _ADDON_MODE:
        return _SUPERVISOR_WS_URL
    url = get_config(session, HA_URL_KEY)
    if not url:
        return None
    if url.startswith("https://"):
        return url.replace("https://", "wss://", 1) + "/api/websocket"
    if url.startswith("http://"):
        return url.replace("http://", "ws://", 1) + "/api/websocket"
    return url + "/api/websocket"


def get_ha_api_url() -> str:
    """Return HTTP API URL for making REST calls to HA."""
    if _ADDON_MODE:
        return _SUPERVISOR_API_URL
    return ""  # standalone: user-configured URL + /api


def get_ha_url(session: Session) -> str | None:
    if _ADDON_MODE:
        return "http://supervisor/core"
    return get_config(session, HA_URL_KEY)


def get_default_property(session: Session) -> str | None:
    return get_config(session, HA_DEFAULT_PROPERTY_KEY)


def is_ha_enabled(session: Session) -> bool:
    if _ADDON_MODE:
        return True  # always enabled in add-on mode
    return get_config(session, HA_ENABLED_KEY, "false") == "true"


def save_ha_config(
    session: Session,
    *,
    url: str,
    token: str,
    default_property: str | None = None,
    enabled: bool = True,
) -> None:
    """Save HA config for standalone (non-add-on) mode."""
    set_config(session, HA_URL_KEY, url.rstrip("/"))
    set_config(session, HA_TOKEN_KEY, token)
    set_config(session, HA_ENABLED_KEY, "true" if enabled else "false")
    if default_property:
        set_config(session, HA_DEFAULT_PROPERTY_KEY, default_property)
