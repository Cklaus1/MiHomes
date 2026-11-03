#!/usr/bin/env bash
# Nightly database dump for a self-hosted Postgres (evo-dev). Scheduled by install-cron.sh.
#
# Why this exists although services/backup.py says database backups are the managed vendor's job
# (D13): evo-dev has no managed vendor. Its Postgres lives in ~/pgdata, and on 2026-11-02 a
# container rebuild lost the database; the restore came from a manual pre-migration dump three
# days old.
#
# - Dumps as the `postgres` superuser over peer auth, NEVER as the app role from web.env: that
#   role is NOBYPASSRLS on FORCE RLS tables, so its dump would hold no tenant rows (or refuse).
# - Also dumps roles (`pg_dumpall --globals-only`): the 11-02 restore had to recreate `mihomes`
#   by hand. Encrypted columns still need MIHOMES_SECRET_KEY (in web.env) to be readable.
# - Verified before it counts: a readable archive, data sections for the core tables, and not
#   suddenly much smaller than the previous good dump. Then it becomes LATEST.
# - Keeps MIHOMES_PG_BACKUP_KEEP_DAYS (default 14) days. Files are 0600.
#
# Restore: see docs/deploy/OPS-SCHEDULER-BACKUPS.md.
set -euo pipefail
umask 077

DB="${MIHOMES_DB_NAME:-mihomes}"
DIR="${MIHOMES_PG_BACKUP_DIR:-$HOME/.mihomes/backups/pg/nightly}"
KEEP_DAYS="${MIHOMES_PG_BACKUP_KEEP_DAYS:-14}"
LOG="${MIHOMES_LOG_DIR:-$HOME/.mihomes/logs/jobs}/pg-backup.log"
# Command prefix that runs as the postgres superuser. Set it empty to run pg_dump directly
# (the local restore test, with PGUSER/PGPASSWORD set).
PG_AS="${MIHOMES_PG_AS-sudo -n -u postgres}"

mkdir -p "$DIR" "$(dirname "$LOG")"
[ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 1048576 ] && mv -f "$LOG" "$LOG.1"
exec >> "$LOG" 2>&1
cd /  # pg_dump as postgres cannot read $HOME; avoids "could not change directory" noise

stamp="$(date -u +%Y%m%d-%H%M%S)"
out="$DIR/mihomes-$stamp.dump"
globals="$DIR/globals-$stamp.sql"
trap 'rm -f "$out.partial" "$globals.partial"' EXIT

echo "=== $(date -u +%FT%TZ) pg-backup $DB -> $out"

$PG_AS pg_dump --format=custom "$DB" > "$out.partial"
$PG_AS pg_dumpall --globals-only > "$globals.partial"

toc="$(pg_restore --list "$out.partial")"
for table in accounts properties tasks alerts; do
    if ! grep -q "TABLE DATA public $table " <<< "$toc"; then
        echo "FAIL: no data section for '$table' — dump not kept"
        exit 1
    fi
done

size="$(stat -c %s "$out.partial")"
if [ -f "$DIR/LATEST" ]; then
    prev="$(cat "$DIR/LATEST")"
    if [ -f "$prev" ]; then
        prev_size="$(stat -c %s "$prev")"
        if [ "$size" -lt $((prev_size / 2)) ]; then
            echo "WARN: $size bytes is under half the previous dump ($prev_size) — check for data loss"
        fi
    fi
fi

mv "$out.partial" "$out"
mv "$globals.partial" "$globals"
echo "$out" > "$DIR/LATEST"

find "$DIR" -maxdepth 1 \( -name 'mihomes-*.dump' -o -name 'globals-*.sql' \) \
    -mtime +"$KEEP_DAYS" -delete

echo "ok: $size bytes; $(ls "$DIR"/mihomes-*.dump | wc -l) dump(s) kept"
