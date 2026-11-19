#!/bin/bash
# Droplet base setup — run as root. Idempotent.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
timedatectl set-timezone UTC
apt-get update -qq
apt-get -y -qq -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
apt-get install -y -qq postgresql postgresql-contrib python3-venv python3-dev build-essential \
  libpq-dev git curl cron ufw unattended-upgrades caddy >/dev/null
echo "packages ok"

# swap 2G
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo "swap: $(swapon --show --noheadings | awk '{print $3}')"

# app user, same layout as evo-dev
id millena >/dev/null 2>&1 || adduser --disabled-password --gecos "" millena >/dev/null
usermod -aG sudo millena
echo "millena ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/millena && chmod 440 /etc/sudoers.d/millena
install -d -m 700 -o millena -g millena /home/millena/.ssh
install -m 600 -o millena -g millena /root/.ssh/authorized_keys /home/millena/.ssh/authorized_keys
chmod 751 /home/millena

# ssh: keys only
sed -ri 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
for f in /etc/ssh/sshd_config.d/*.conf; do [ -f "$f" ] && sed -ri 's/^PasswordAuthentication .*/PasswordAuthentication no/' "$f"; done
systemctl reload ssh || systemctl reload sshd || true

# firewall
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status | head -8

# automatic security updates
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true

systemctl enable --now postgresql cron >/dev/null
echo "postgres: $(sudo -u postgres psql -Atc 'show server_version')"
echo "python: $(python3 -V)  caddy: $(caddy version | cut -d' ' -f1)"
echo BASE-DONE
