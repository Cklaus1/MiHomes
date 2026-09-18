#!/bin/bash
# Expire finished trials. **The trial's only clock.**
#
# A no-card trial creates no Stripe subscription, so Stripe's `trial_will_end` webhook never
# fires (PRICING §4.2) — this command is the entire mechanism that ends a trial. Nothing
# scheduled it, so `start_trial` set `plan='pro'` and nothing ever set it back: a lapsed trial
# kept full Pro entitlements indefinitely. Measured 2026-09-18 on this box: no crontab, no
# timers, and the Free tester sitting on `plan=pro` `trialing`.
#
# Idempotent (SPEC-004), so a double run, a catch-up after downtime, or a manual invocation
# alongside the schedule are all safe.

set -u

VENV=/home/millena/MiHomes/.venv/bin
LOG=/home/millena/.mihomes/logs/trial-sweep.log

mkdir -p "$(dirname "$LOG")"

while IFS='=' read -r key value; do
    case "$key" in
        ''|\#*) continue ;;
        *) export "$key=$value" ;;
    esac
done < <(sudo -n cat /etc/mihomes/mihomes.env)

if [ -z "${DATABASE_URL:-}" ]; then
    echo "[$(date -Is)] FATAL: DATABASE_URL not set — refusing to run" >> "$LOG"
    exit 1
fi

cd /home/millena/MiHomes

echo "[$(date -Is)] trial-sweep starting" >> "$LOG"
"$VENV/mihomes" jobs trial-sweep >> "$LOG" 2>&1
status=$?
echo "[$(date -Is)] trial-sweep finished (exit $status)" >> "$LOG"
exit $status
