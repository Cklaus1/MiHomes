#!/bin/bash
# Keep the MiHomes web app running. Started by cron `@reboot` and re-checked every minute.
#
# **Why a wrapper rather than a bare `@reboot mihomes-web`.** The app was started by hand from
# an interactive shell and so died with it (measured 2026-09-18: the login session showed
# "gone - no logout", `pgrep mihomes` empty, port 5000 dead). `@reboot` alone fixes the
# *restart* case and not the *crash* case, and this container has no init to supervise it —
# sshd is PID 1 and systemd is not booted.
#
# **Idempotent on purpose.** Cron runs this every minute; if the app is already up it exits
# immediately. That makes "start it" and "keep it up" the same command, so there is no second
# mechanism that can disagree with the first.
#
# Credentials come from /etc/mihomes/mihomes.env (0600 root:root) rather than ~/.bashrc, which
# is what made the app unrestartable by anyone who did not already know them. Read via sudo
# because the file is deliberately not readable by the app's own user.

set -u

VENV=/home/millena/MiHomes/.venv/bin
LOG=/home/millena/.mihomes/logs/web.log
PIDFILE=/home/millena/.mihomes/web.pid

# **`mihomes-dev`, not `mihomes-web`, and that is not laziness.**
#
# `main()` serves through waitress + a2wsgi and had never run in production — its two imports
# were undeclared, so it died on `ModuleNotFoundError` and every deployment used `mihomes-dev`
# instead. Declaring them made it *start*, which is not the same as making it work: measured
# 2026-09-18, it accepted connections, held port 5000, and then hung — waitress logging "Task
# queue depth is 1, 2, 3…" while every request timed out at 25s. An ASGI app wrapped into WSGI
# is a real port with its own failure modes, and this one has never been exercised.
#
# `dev()` runs uvicorn natively, which is what has actually served this app all along. The one
# thing it does NOT do is call `verify_runtime_role` (N5 — refuse to serve as a role that
# bypasses RLS), so that check is run explicitly below rather than lost in the swap.
SERVER="$VENV/mihomes-dev"

mkdir -p "$(dirname "$LOG")"

# Already running? Nothing to do. `pgrep -f` matches the full command line, which is what
# distinguishes the server from this wrapper.
if pgrep -f "$SERVER" >/dev/null 2>&1; then
    exit 0
fi

echo "[$(date -Is)] server not running — starting" >> "$LOG"

# `sudo -n cat` because the env file is root-only by design. Parsed rather than sourced: it is
# systemd-shaped (bare KEY=value, no export, no quoting), so `source` would be wrong for a
# value containing anything shell-special.
while IFS='=' read -r key value; do
    case "$key" in
        ''|\#*) continue ;;
        *) export "$key=$value" ;;
    esac
done < <(sudo -n cat /etc/mihomes/mihomes.env)

if [ -z "${DATABASE_URL:-}" ]; then
    echo "[$(date -Is)] FATAL: DATABASE_URL not set — refusing to start" >> "$LOG"
    exit 1
fi

cd /home/millena/MiHomes

# N5, which `dev()` does not perform for itself. A superuser bypasses row-level security
# silently — no error, other tenants' rows are simply visible — so this refuses to start rather
# than serve without isolation. `main()` had this inline; running it here keeps the guarantee
# while the server that actually works does the serving.
if ! "$VENV/python" -c "
from mihomes.db import get_engine
from mihomes.tenancy.runtime_role import verify_runtime_role
verify_runtime_role(get_engine())
" >> "$LOG" 2>&1; then
    echo "[$(date -Is)] FATAL: runtime role check failed — not starting" >> "$LOG"
    exit 1
fi

# `setsid` detaches it from this cron job's process group, so the server is not killed when
# the cron invocation finishes — the same mistake in a different shape as dying with the login
# shell.
setsid nohup "$SERVER" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"

echo "[$(date -Is)] started, pid $(cat "$PIDFILE")" >> "$LOG"
