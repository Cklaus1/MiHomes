#!/usr/bin/env bash
# Run one `mihomes` command with the app's environment — the runner every crontab line uses.
#
#   bash scripts/ops/run-job.sh jobs drain-outbox
#
# - Environment: ~/.mihomes/run/web.env (override with MIHOMES_ENV_FILE), loaded with `env -i`
#   so cron's bare environment and the operator's shell both get exactly what the app gets.
# - One at a time per command: a run still going when the next fires is skipped, not stacked.
# - Output: ~/.mihomes/logs/jobs/<command>.log, rotated at ~1 MB. Exit code is the command's.
set -uo pipefail
umask 077
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ $# -lt 1 ]; then
    echo "usage: run-job.sh <mihomes args...>   e.g. run-job.sh jobs alerts" >&2
    exit 2
fi

mkdir -p "$MIHOMES_LOG_DIR"
name="$(printf '%s-' "$@" | tr -c 'A-Za-z0-9_-' '_')"
name="${name%-}"
log="$MIHOMES_LOG_DIR/$name.log"
rotate_log "$log"

load_env || { echo "$(date -u +%FT%TZ) $name: cannot read env file" >> "$log"; exit 1; }

if ! command -v flock > /dev/null; then
    # Not "lock held": without this check every job would be skipped forever, silently.
    echo "$(date -u +%FT%TZ) FAIL: flock not installed (util-linux)" >> "$log"
    exit 1
fi
exec 9>"$MIHOMES_LOG_DIR/$name.lock"
if ! flock -n 9; then
    echo "$(date -u +%FT%TZ) skipped: the previous run is still going" >> "$log"
    exit 0
fi

{
    echo "=== $(date -u +%FT%TZ) mihomes $*"
    env -i "${E[@]}" "$MIHOMES_BIN" "$@"
    rc=$?
    echo "=== exit $rc"
} >> "$log" 2>&1
exit "$rc"
