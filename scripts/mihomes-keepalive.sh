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

mkdir -p "$(dirname "$LOG")"

# Already running? Nothing to do. `pgrep -f` matches the full command line, which is what
# distinguishes the server from this wrapper.
if pgrep -f "$VENV/mihomes-web" >/dev/null 2>&1; then
    exit 0
fi

echo "[$(date -Is)] mihomes-web not running — starting" >> "$LOG"

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

# `setsid` detaches it from this cron job's process group, so the server is not killed when
# the cron invocation finishes — the same mistake in a different shape as dying with the login
# shell.
setsid nohup "$VENV/mihomes-web" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"

echo "[$(date -Is)] started, pid $(cat "$PIDFILE")" >> "$LOG"
