set -euo pipefail

# Schedule the MiHomes trial sweep.
#
# **The sweep only.** This originally also supervised the web app on a per-minute keepalive;
# that was removed at the owner's request, and the reasoning is worth keeping: a cron job
# cannot tell "hung but still listening" from "healthy", so it restarts what has died and sits
# blind through what has wedged. Starting the app is a person's job on this box. The keepalive
# script stays in the repo, unscheduled, for anyone who wants it back.
#
# The sweep is different in kind: nothing else ends a trial, so a missed run is not an
# inconvenience but a Pro account nobody is billing for.
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

# **Invoked through `bash`, not as a bare path.** A bare path needs the executable bit, and that
# bit is one `git checkout` away from vanishing: the scripts were first committed mode 100644,
# so restoring them from git silently left cron unable to run them — installed and doing
# nothing, which is the worst of both. `bash <path>` does not care about the mode.
#
# Expire finished trials — **the trial's only clock.** A no-card trial creates no Stripe
# subscription, so `trial_will_end` never fires and nothing else ends a trial: without this,
# `start_trial` sets plan='pro' and nothing sets it back, so a lapsed trial keeps full Pro
# entitlements indefinitely.
#
# 00:17 UTC: `trial_ends_at` is stored in UTC and this box runs UTC, so "the day it ends" means
# the same to the timer and to the data. A few minutes past the hour keeps it clear of
# everything else that fires at :00. Idempotent, so a catch-up run is safe.
17 0 * * *        bash /home/millena/MiHomes/scripts/mihomes-trial-sweep.sh
CRON

crontab -l | grep -v '^#' | grep -v '^$' | sed 's/^/  /'

echo
echo "=== 5. the web app is NOT supervised, by choice ==="
# `mihomes-keepalive.sh` is kept in the repo and deliberately not scheduled. It was installed
# on a per-minute schedule and then removed at the owner's request: a cron job that restarts a
# process every minute cannot tell "hung but still listening" from "healthy" — measured, when a
# wedged server held port 5000 while serving nothing and the keepalive saw a live process and
# did nothing. Starting the app is a person's job here.
#
# Run it by hand if you want the behaviour back for a while:
#     bash scripts/mihomes-keepalive.sh      # starts the server if it is not already up
echo "  start the app with:  mihomes-dev"
echo "  (or: bash scripts/mihomes-keepalive.sh, which is a no-op if it is already running)"
