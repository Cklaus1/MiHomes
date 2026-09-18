set -euo pipefail

# Install cron-based supervision for the MiHomes app and the trial sweep.
#
# systemd units were written first and removed: this box is a Docker container with sshd as
# PID 1 and systemd not booted, so they could never have run. Cron is the scheduler that
# works here.

echo "=== 1. install cron ==="
if ! command -v crontab >/dev/null 2>&1; then
    sudo -n DEBIAN_FRONTEND=noninteractive apt-get update -qq
    sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cron
    echo "installed"
else
    echo "already present"
fi

sudo -n service cron start >/dev/null 2>&1 || true
service cron status 2>&1 | head -2

echo
echo "=== 2. install the scripts ==="
sudo -n install -d -m 0755 -o millena -g millena /home/millena/.mihomes/logs
install -m 0755 /tmp/mihomes-keepalive.sh   /home/millena/MiHomes/scripts/mihomes-keepalive.sh
install -m 0755 /tmp/mihomes-trial-sweep.sh /home/millena/MiHomes/scripts/mihomes-trial-sweep.sh
ls -l /home/millena/MiHomes/scripts/mihomes-*.sh | awk '{print "  ", $1, $NF}'

echo
echo "=== 3. allow the two scripts to read the credentials file ==="
# They run as `millena` and need `sudo -n cat` on exactly one root-owned file. Scoped to that
# single path and that single command — not blanket sudo — so the grant cannot be reused.
printf '%s\n' \
  'millena ALL=(root) NOPASSWD: /usr/bin/cat /etc/mihomes/mihomes.env' \
  | sudo -n install -m 0440 -o root -g root /dev/stdin /etc/sudoers.d/mihomes-env
sudo -n visudo -c -f /etc/sudoers.d/mihomes-env

echo
echo "=== 4. crontab ==="
# Written wholesale from a known state rather than appended to, so re-running this script
# cannot accumulate duplicate lines.
crontab - <<'CRON'
# MiHomes — installed by scripts/install_cron.sh. Edit there, not here.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Keep the web app up. Idempotent: exits at once if it is already running, so this both
# starts it after a restart and revives it after a crash.
@reboot           /home/millena/MiHomes/scripts/mihomes-keepalive.sh
* * * * *         /home/millena/MiHomes/scripts/mihomes-keepalive.sh

# Expire finished trials — the trial's only clock. 00:17 UTC: `trial_ends_at` is stored in UTC
# and this box runs UTC, so "the day it ends" means the same thing to both. A few minutes past
# the hour keeps it clear of everything else that fires at :00.
17 0 * * *        /home/millena/MiHomes/scripts/mihomes-trial-sweep.sh
CRON

crontab -l | grep -v '^#' | grep -v '^$' | sed 's/^/  /'

echo
echo "=== 5. start the app now (do not wait for the next minute) ==="
/home/millena/MiHomes/scripts/mihomes-keepalive.sh
sleep 8
pgrep -f 'mihomes-web' >/dev/null && echo "app is running (pid $(pgrep -f mihomes-web | head -1))" || echo "NOT RUNNING — see ~/.mihomes/logs/web.log"
