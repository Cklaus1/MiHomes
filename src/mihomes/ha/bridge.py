"""HA bridge — main event processing loop.

Consumes state_changed events and routes them to MiHomes issues/alerts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mihomes.db import get_session
from mihomes.ha.config import get_ha_token, get_ha_ws_url, get_default_property
from mihomes.ha.client import connect_and_subscribe, StateChangedEvent
from mihomes.ha.rules import find_matching_rules, HARule
from mihomes.models.issue import IssueSeverity

log = logging.getLogger(__name__)

# Dedupe: (entity_id, rule_pattern) → last_triggered timestamp
_seen: dict[tuple[str, str], datetime] = {}
_COOLDOWN_SECONDS = 300  # 5 min — don't re-fire same rule for same entity within this window


def _should_fire(entity_id: str, rule: HARule) -> bool:
    key = (entity_id, rule.pattern)
    last = _seen.get(key)
    now = datetime.now(timezone.utc)
    if last and (now - last).total_seconds() < _COOLDOWN_SECONDS:
        return False
    _seen[key] = now
    return True


def _handle_event(session: Session, event: StateChangedEvent, default_property: str | None) -> int:
    """Process one state_changed event. Returns number of actions taken."""
    rules = find_matching_rules(event.entity_id, event.new_state)
    if not rules:
        return 0

    actions = 0
    for rule in rules:
        if not _should_fire(event.entity_id, rule):
            log.debug("Cooldown active for %s / %s — skipping", event.entity_id, rule.pattern)
            continue

        title = rule.title_fn(event.entity_id, event.new_state, event.attributes)
        description = rule.description_fn(event.entity_id, event.new_state, event.attributes)

        if rule.alert_only:
            _create_alert(session, title, description, rule.severity)
        else:
            _create_issue(session, title, description, rule.severity, default_property)

        actions += 1

    return actions


def _create_issue(
    session: Session,
    title: str,
    description: str | None,
    severity: IssueSeverity,
    property_slug: str | None,
) -> None:
    if not property_slug:
        log.warning("HA bridge: no default property set — skipping issue '%s'", title)
        return

    from mihomes.services.issue import create_issue
    try:
        issue = create_issue(
            session,
            title=title,
            property_id_or_slug=property_slug,
            severity=severity,
            description=description,
        )
        session.commit()
        log.info("Created issue #%s: %s", issue.id, title)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.error("Failed to create issue '%s': %s", title, exc)


def _create_alert(
    session: Session,
    title: str,
    description: str | None,
    severity: IssueSeverity,
) -> None:
    from mihomes.models.alert import Alert, AlertSeverity

    sev_map = {
        IssueSeverity.LOW: AlertSeverity.LOW,
        IssueSeverity.MEDIUM: AlertSeverity.MEDIUM,
        IssueSeverity.HIGH: AlertSeverity.HIGH,
        IssueSeverity.CRITICAL: AlertSeverity.CRITICAL,
    }

    try:
        alert = Alert(
            alert_type="ha_sensor",
            source_entity_type="ha_entity",
            message=f"{title}\n\n{description}" if description else title,
            severity=sev_map.get(severity, AlertSeverity.MEDIUM),
        )
        session.add(alert)
        session.commit()
        log.info("Created alert #%s: %s", alert.id, title)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.error("Failed to create alert '%s': %s", title, exc)


async def run_bridge(*, log_level: str = "INFO") -> None:
    """Main entry point — runs forever until cancelled."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [ha_bridge] %(message)s",
    )

    with get_session() as session:
        ws_url = get_ha_ws_url(session)
        token = get_ha_token(session)
        default_property = get_default_property(session)

    if not ws_url or not token:
        raise RuntimeError(
            "Home Assistant is not configured. Run: mihomes ha setup"
        )

    log.info("Starting HA bridge → %s (default property: %s)", ws_url, default_property or "none")

    total_events = 0
    total_actions = 0

    async for event in connect_and_subscribe(ws_url, token):
        total_events += 1
        log.debug(
            "Event: %s → %s (was: %s)",
            event.entity_id,
            event.new_state,
            event.old_state,
        )

        with get_session() as session:
            dp = get_default_property(session) or default_property
            n = _handle_event(session, event, dp)
            total_actions += n

        if total_events % 100 == 0:
            log.info(
                "HA bridge stats: %d events processed, %d actions taken",
                total_events,
                total_actions,
            )
