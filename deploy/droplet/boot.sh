#!/bin/bash
# MiHomes boot — run as root by mihomes-boot.service (and by deploy.sh). Idempotent.
# Starts Postgres if needed, arms cron, then starts the app as millena.
#
# Scheduled jobs (Telegram/email outbox, calendar sync, Stripe dunning, ...) act on the outside
# world, so they run on ONE server only. Until ~/.mihomes/run/LIVE exists (switch-over from
# evo-dev), only the nightly backup and the crash-restart check are armed here.
echo "[$(date)] mihomes boot"
systemctl is-active -q postgresql || systemctl start postgresql
for i in $(seq 1 30); do pg_isready -q && break; sleep 1; done
pg_isready -q || { echo "postgres not ready; giving up"; exit 1; }

if [ -f /home/millena/.mihomes/run/LIVE ]; then
    runuser -u millena -- bash /home/millena/MiHomes/scripts/ops/install-cron.sh || echo "install-cron failed"
else
    runuser -u millena -- bash -c '
        others="$(crontab -l 2>/dev/null | awk "/^# BEGIN MiHomes/{s=1;next} /^# END MiHomes/{s=0;next} !s")"
        { [ -n "$others" ] && printf "%s\n" "$others"
          echo "# BEGIN MiHomes (PRE-SWITCH-OVER: jobs off until ~/.mihomes/run/LIVE exists)"
          echo "30 2 * * * bash /home/millena/MiHomes/scripts/ops/pg-backup.sh"
          echo "* * * * * bash /home/millena/MiHomes/scripts/ops/keepalive.sh"
          echo "# END MiHomes"; } | crontab -'
    echo "pre-switch-over crontab: backup + keepalive only"
fi

if pgrep -f 'bin/mihomes-(dev|web)' >/dev/null; then
    echo "app already running"; exit 0
fi
runuser -u millena -- bash -c '
    cd ~/MiHomes
    mapfile -t E < <(grep -v "^\s*$" ~/.mihomes/run/web.env)
    nohup env -i "${E[@]}" setsid .venv/bin/mihomes-dev >> ~/.mihomes/run/web.log 2>&1 < /dev/null &
'
echo "app started"
