# Shared by the scripts in scripts/ops. Sourced, not run.
#
# Loads the app's launch environment into the array E, for `env -i "${E[@]}" ...`.
# Never `env -i $(cat web.env)`: it word-splits values containing spaces (SSH_CONNECTION) and
# fails with "env: '60103': No such file" (evo-dev, 2026-09-23).

MIHOMES_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MIHOMES_ENV_FILE="${MIHOMES_ENV_FILE:-$HOME/.mihomes/run/web.env}"
MIHOMES_LOG_DIR="${MIHOMES_LOG_DIR:-$HOME/.mihomes/logs/jobs}"
MIHOMES_BIN="${MIHOMES_BIN:-$MIHOMES_REPO/.venv/bin/mihomes}"

load_env() {
    if [ ! -r "$MIHOMES_ENV_FILE" ]; then
        echo "cannot read $MIHOMES_ENV_FILE (the app's launch environment)" >&2
        return 1
    fi
    # A PATH first, so a web.env without one still finds bash/date; web.env's own PATH wins.
    E=("PATH=/usr/local/bin:/usr/bin:/bin" "HOME=$HOME")
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        case "$line" in ''|'#'*) continue ;; esac
        E+=("$line")
    done < "$MIHOMES_ENV_FILE"
}

# Keep a log under ~1 MB: the 5-minute jobs would otherwise grow it forever.
rotate_log() {
    local log="$1"
    if [ -f "$log" ] && [ "$(stat -c %s "$log")" -gt 1048576 ]; then
        mv -f "$log" "$log.1"
    fi
}
