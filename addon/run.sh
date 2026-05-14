#!/usr/bin/with-contenv bashio

# ── Read add-on options ───────────────────────────────────────────────────────
LOG_LEVEL=$(bashio::config 'log_level')
RUN_BRIDGE=$(bashio::config 'run_ha_bridge')
RUN_AUTOMATION=$(bashio::config 'run_automation')
AUTO_INTERVAL=$(bashio::config 'automation_interval_minutes')

# ── Environment: SUPERVISOR_TOKEN is injected automatically by HA Supervisor ──
# We pass it through so MiHomes' HA bridge can use it without any user config.
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN}"

# ── MiHomes data lives in /data (persisted across restarts/updates) ───────────
export MIHOMES_DIR="/data/mihomes"
mkdir -p "${MIHOMES_DIR}/db" "${MIHOMES_DIR}/media" "${MIHOMES_DIR}/backups" "${MIHOMES_DIR}/exports"

# ── Initialize DB if first run ────────────────────────────────────────────────
if [ ! -f "${MIHOMES_DIR}/db/mihomes.db" ]; then
    bashio::log.info "First run: initializing MiHomes database..."
    python3 -c "
from mihomes.config import ensure_dirs
from mihomes.db import init_db
ensure_dirs()
init_db()
print('Database initialized.')
"
fi

# ── Start HA bridge in background if enabled ─────────────────────────────────
if bashio::var.true "${RUN_BRIDGE}"; then
    bashio::log.info "Starting Home Assistant sensor bridge..."
    python3 -c "
import asyncio
from mihomes.ha.bridge import run_bridge
asyncio.run(run_bridge(log_level='${LOG_LEVEL^^}'))
" &
    BRIDGE_PID=$!
    bashio::log.info "HA bridge started (PID: ${BRIDGE_PID})"
fi

# ── Start automation scheduler in background if enabled ──────────────────────
if bashio::var.true "${RUN_AUTOMATION}"; then
    bashio::log.info "Starting automation scheduler (every ${AUTO_INTERVAL} min)..."
    while true; do
        python3 -c "
from mihomes.db import get_session
from mihomes.services import automation as auto_svc
from mihomes.services.alerts import generate_alerts
from mihomes.services.predictive_maintenance import run_predictive_maintenance
with get_session() as s:
    auto_svc.escalate_overdue_tasks(s)
    auto_svc.generate_expiration_alerts(s)
    generate_alerts(s)
    run_predictive_maintenance(s)
    s.commit()
print('Automation run complete.')
" 2>&1 | bashio::log.info
        sleep $(( AUTO_INTERVAL * 60 ))
    done &
fi

# ── Start MiHomes web UI + API (foreground) ───────────────────────────────────
bashio::log.info "Starting MiHomes on port 8080..."
exec python3 -m uvicorn mihomes.api.app:app \
    --host 0.0.0.0 \
    --port 8080 \
    --log-level "${LOG_LEVEL}"
