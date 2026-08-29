"""Predictive maintenance service.

Scans assets and generates task + alert recommendations based on:
  - Time since last service (>1 year → due)
  - End-of-life proximity (>80% through expected lifespan → warning, >100% → critical)
  - Asset condition (poor → immediate attention)
  - Warranty expiry (within 90 days → alert)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.entitlements import check_entitlement
from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
from mihomes.models.asset import Asset, AssetCondition, AssetType
from mihomes.models.task import Task, TaskPriority, TaskStatus

# ── Thresholds ────────────────────────────────────────────────────────────────

SERVICE_OVERDUE_DAYS = 365          # flag if not serviced in >1 year
EOL_WARNING_PCT = 0.80              # warn when 80% through expected lifespan
WARRANTY_ALERT_DAYS = 90            # alert if warranty expires within 90 days

# Asset types we track for service intervals (exclude consumables/valuables)
_SERVICEABLE = {AssetType.APPLIANCE, AssetType.EQUIPMENT, AssetType.VEHICLE}


# ── Output types ──────────────────────────────────────────────────────────────

@dataclass
class MaintenanceFlag:
    asset: Asset
    reason: str
    risk_level: str      # "critical" | "high" | "medium" | "low"
    action: str          # "service" | "replace" | "inspect" | "warranty"
    days_overdue: int | None = None
    eol_pct: float | None = None


@dataclass
class MaintenanceScanResult:
    flags: list[MaintenanceFlag]
    alerts_created: int
    tasks_created: int


# ── Core scanner ──────────────────────────────────────────────────────────────

def scan_assets(session: Session) -> list[MaintenanceFlag]:
    """Scan all active assets and return maintenance flags (no DB writes)."""
    today = date.today()
    assets = session.query(Asset).filter(Asset.active.is_(True)).all()
    flags: list[MaintenanceFlag] = []

    for asset in assets:
        # ── End of life ──────────────────────────────────────────────────────
        ref_date = asset.install_date or asset.purchase_date
        if ref_date and asset.expected_lifespan_years and asset.expected_lifespan_years > 0:
            age_days = (today - ref_date).days
            lifespan_days = asset.expected_lifespan_years * 365
            eol_pct = age_days / lifespan_days

            if eol_pct >= 1.0:
                flags.append(MaintenanceFlag(
                    asset=asset,
                    reason=(
                        f"Past expected lifespan of {asset.expected_lifespan_years:.0f} yrs "
                        f"({_age_str(ref_date, today)} old)"
                    ),
                    risk_level="critical",
                    action="replace",
                    eol_pct=round(eol_pct * 100, 1),
                ))
                continue  # most severe — skip further checks for this asset
            elif eol_pct >= EOL_WARNING_PCT:
                flags.append(MaintenanceFlag(
                    asset=asset,
                    reason=(
                        f"{int(eol_pct * 100)}% through expected lifespan "
                        f"({_age_str(ref_date, today)} of {asset.expected_lifespan_years:.0f} yr life)"
                    ),
                    risk_level="high",
                    action="inspect",
                    eol_pct=round(eol_pct * 100, 1),
                ))

        # ── Poor condition ───────────────────────────────────────────────────
        if asset.condition == AssetCondition.POOR:
            flags.append(MaintenanceFlag(
                asset=asset,
                reason="Condition marked as POOR",
                risk_level="high",
                action="inspect",
            ))

        # ── Overdue service ──────────────────────────────────────────────────
        if asset.asset_type in _SERVICEABLE:
            service_ref = asset.last_serviced or asset.install_date or asset.purchase_date
            if service_ref:
                days_since = (today - service_ref).days
                if days_since > SERVICE_OVERDUE_DAYS:
                    flags.append(MaintenanceFlag(
                        asset=asset,
                        reason=f"No service in {days_since} days (last: {service_ref})",
                        risk_level="medium",
                        action="service",
                        days_overdue=days_since - SERVICE_OVERDUE_DAYS,
                    ))

        # ── Warranty expiring ────────────────────────────────────────────────
        if asset.warranty_expires:
            days_left = (asset.warranty_expires - today).days
            if 0 < days_left <= WARRANTY_ALERT_DAYS:
                flags.append(MaintenanceFlag(
                    asset=asset,
                    reason=f"Warranty expires {asset.warranty_expires} ({days_left} days)",
                    risk_level="low",
                    action="warranty",
                ))
            elif days_left <= 0:
                flags.append(MaintenanceFlag(
                    asset=asset,
                    reason=f"Warranty expired {asset.warranty_expires}",
                    risk_level="low",
                    action="warranty",
                ))

    # De-duplicate: keep highest-risk flag per asset
    flags = _dedupe(flags)
    return sorted(flags, key=lambda f: _risk_order(f.risk_level))


def run_predictive_maintenance(
        session: Session,
        *,
        account,
) -> MaintenanceScanResult:
    """Scan assets and create alerts + tasks for new findings. Returns summary.

    Plan gate (SPEC-005 D10/A12): ``maintenance.predict`` must be Allowed — Estate only.
    Enforced at function entry, before any reads or writes.

    **The argument is an ACTION, not an entitlement key.** `can()` keys `_BOOLEAN_ACTIONS` on
    actions; `"predictive_maintenance"` is the *key* that action maps to, matches neither dict,
    and falls through to `can()`'s final `return Allowed()` — so this gate allowed every plan
    on every call. Measured at G12, not read: `can(free, "predictive_maintenance")` returned
    Allowed. A12 written against the old string would have passed vacuously.

    F6: this function still has zero callers, so the gate has no live surface — placed exactly
    as SPEC-004 D14 placed the dead vendor_rating gates, so whoever wires it up inherits the
    gate rather than reopening the hole.
    """
    check_entitlement(account, "maintenance.predict")

    flags = scan_assets(session)
    alerts_created = 0
    tasks_created = 0

    for flag in flags:
        # Skip if we already have a pending alert for this asset+action
        existing_alert = session.query(Alert).filter(
            Alert.source_entity_type == "asset",
            Alert.source_entity_id == flag.asset.id,
            Alert.alert_type == f"predictive_{flag.action}",
            Alert.status.notin_([AlertStatus.RESOLVED]),
        ).first()

        if not existing_alert:
            severity_map = {
                "critical": AlertSeverity.CRITICAL,
                "high": AlertSeverity.HIGH,
                "medium": AlertSeverity.MEDIUM,
                "low": AlertSeverity.LOW,
            }
            session.add(Alert(
                alert_type=f"predictive_{flag.action}",
                source_entity_type="asset",
                source_entity_id=flag.asset.id,
                severity=severity_map[flag.risk_level],
                message=(
                    f"{flag.asset.name} ({flag.asset.asset_type.value}) — "
                    f"{flag.reason}. Action: {flag.action.upper()}."
                ),
            ))
            alerts_created += 1

        # For critical/high + service/replace: also create a task if none pending
        if flag.risk_level in ("critical", "high") and flag.action in ("replace", "inspect", "service"):
            prop_id = flag.asset.property_id
            task_title = f"{flag.action.title()}: {flag.asset.name}"

            existing_task = session.query(Task).filter(
                Task.title == task_title,
                Task.property_id == prop_id,
                Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            ).first()

            if not existing_task:
                from mihomes.services.task import create_task
                due = date.today() + timedelta(days=30 if flag.risk_level == "high" else 14)
                priority = TaskPriority.HIGH if flag.risk_level == "critical" else TaskPriority.MEDIUM
                create_task(
                    session,
                    task_title,
                    str(prop_id),
                    priority=priority,
                    due_date=due,
                    description=(
                        f"Predictive maintenance: {flag.reason}\n"
                        f"Asset: {flag.asset.name} "
                        f"({flag.asset.make or ''} {flag.asset.model_name or ''}).".strip()
                    ),
                )
                tasks_created += 1

    session.flush()
    return MaintenanceScanResult(
        flags=flags,
        alerts_created=alerts_created,
        tasks_created=tasks_created,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _age_str(start: date, end: date) -> str:
    days = (end - start).days
    years = days // 365
    months = (days % 365) // 30
    if years and months:
        return f"{years}y {months}m"
    if years:
        return f"{years}y"
    return f"{months}m"


def _risk_order(level: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(level, 9)


def _dedupe(flags: list[MaintenanceFlag]) -> list[MaintenanceFlag]:
    """Keep only the highest-risk flag per asset."""
    best: dict[int, MaintenanceFlag] = {}
    for f in flags:
        existing = best.get(f.asset.id)
        if existing is None or _risk_order(f.risk_level) < _risk_order(existing.risk_level):
            best[f.asset.id] = f
    return list(best.values())
