#!/usr/bin/env bash
# Crash restart for evo-dev, run by cron every minute as millena (install-cron.sh adds the line).
#
# Before this, nothing restarted the app if it died: it stayed down until someone noticed.
# Each run checks, in order:
#   1. Postgres: `pg_isready`; started at once if down (restarting the app would not help).
#   2. The app: does http://127.0.0.1:5000/login answer within 10 s? Any HTTP status counts
#      (401 is normal). A hung app counts as down — the 2026-10 session-touch deadlock left the
#      process alive and every page hanging — so this probes a real route, not the process list.
#   3. The Stripe sandbox listener (only if ~/.local/bin/stripe and STRIPE_SECRET_KEY exist):
#      known to die on its own after hours, leaving webhooks undelivered.
#
# The app is restarted only after it fails TWO runs in a row. A deploy restarts it in seconds,
# so the watchdog never races deploy.sh; a real crash costs at most ~2 minutes.
# The app log is appended to, never truncated, so the crash that caused a restart is kept.
# Every restart is logged to ~/.mihomes/logs/jobs/keepalive.log; quiet runs log nothing.
set -uo pipefail
umask 077
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

RUN_DIR="${MIHOMES_RUN_DIR:-$HOME/.mihomes/run}"
URL="${MIHOMES_KEEPALIVE_URL:-http://127.0.0.1:5000/login}"
FAILS_TO_RESTART="${MIHOMES_KEEPALIVE_FAILS:-2}"
STATE="$RUN_DIR/keepalive.fails"
APP_BIN="${MIHOMES_APP_BIN:-$MIHOMES_REPO/.venv/bin/mihomes-dev}"
APP_PATTERN='[b]in/mihomes-(dev|web)'   # brackets: pgrep/pkill must not match this script
STRIPE_PATTERN='[s]tripe listen'

mkdir -p "$MIHOMES_LOG_DIR" "$RUN_DIR"
LOG="$MIHOMES_LOG_DIR/keepalive.log"
rotate_log "$LOG"
say() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

exec 9>"$RUN_DIR/keepalive.lock"
flock -n 9 || exit 0   # a previous run is still restarting something

# ── 1. Postgres ───────────────────────────────────────────────────────────────
if command -v pg_isready > /dev/null && ! pg_isready -q; then
    say "postgres not ready — starting it"
    sudo -n pg_ctlcluster 16 main start >> "$LOG" 2>&1 || say "postgres start FAILED"
    for _ in $(seq 1 30); do pg_isready -q && break; sleep 1; done
fi

# ── 2. The app ────────────────────────────────────────────────────────────────
code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$URL" || true)"
if [ -n "$code" ] && [ "$code" != 000 ]; then
    rm -f "$STATE"
else
    fails=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
    echo "$fails" > "$STATE"
    if [ "$fails" -ge "$FAILS_TO_RESTART" ]; then
        if pgrep -f "$APP_PATTERN" > /dev/null; then
            say "app not answering for $fails checks but still running (hung) — killing it"
            pkill -f "$APP_PATTERN" || true
            for _ in $(seq 1 10); do pgrep -f "$APP_PATTERN" > /dev/null || break; sleep 1; done
            pkill -KILL -f "$APP_PATTERN" || true
            sleep 1
        else
            say "app not running for $fails checks — starting it"
        fi
        if load_env; then
            {
                echo
                echo "=== $(date -u +%FT%TZ) restarted by scripts/ops/keepalive.sh ==="
            } >> "$RUN_DIR/web.log"
            (
                cd "$MIHOMES_REPO" || exit 1
                # setsid before `env -i`: it must be found on cron's PATH, not the cleared one.
                nohup setsid env -i "${E[@]}" "$APP_BIN" \
                    >> "$RUN_DIR/web.log" 2>&1 < /dev/null &
            )
            for _ in $(seq 1 20); do
                code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$URL" || true)"
                [ -n "$code" ] && [ "$code" != 000 ] && break
                sleep 1
            done
            if [ -n "$code" ] && [ "$code" != 000 ]; then
                say "app back up (HTTP $code)"
            else
                say "app still not answering after restart — see $RUN_DIR/web.log"
            fi
            # Either way, a fresh count: a slow start gets two more minutes, not killed next run.
            rm -f "$STATE"
        else
            say "cannot read $MIHOMES_ENV_FILE — app not started"
        fi
    fi
fi

# ── 3. Stripe sandbox listener ────────────────────────────────────────────────
STRIPE_BIN="$HOME/.local/bin/stripe"
if [ -x "$STRIPE_BIN" ] && ! pgrep -f "$STRIPE_PATTERN" > /dev/null && load_env 2>/dev/null; then
    key=""
    for kv in "${E[@]}"; do
        case "$kv" in STRIPE_SECRET_KEY=*) key="${kv#STRIPE_SECRET_KEY=}" ;; esac
    done
    if [ -n "$key" ]; then
        say "stripe listener not running — starting it"
        # --events is required on CLI 1.52; without it `listen` exits at once (2026-11-02).
        setsid nohup "$STRIPE_BIN" listen --api-key "$key" \
            --events checkout.session.completed,customer.subscription.created,customer.subscription.updated,customer.subscription.deleted,customer.subscription.trial_will_end,invoice.paid,invoice.payment_failed \
            --forward-to http://127.0.0.1:5000/webhooks/stripe \
            >> "$RUN_DIR/stripe-listen.log" 2>&1 < /dev/null &
    fi
fi
exit 0
