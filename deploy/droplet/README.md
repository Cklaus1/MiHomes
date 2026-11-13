# MiHomes on the DigitalOcean Droplet (mihomes.ai)

Production-trial server for Chris's home and staff. Set up 2026-11-13.

| | |
|---|---|
| Host | DigitalOcean Basic Regular, 2 vCPU / 4 GB / 80 GB, NYC2, Ubuntu 24.04 |
| IP | `107.170.18.108` (GoDaddy DNS: `A @` → IP, `CNAME www` → `@`) |
| Login | `ssh millena@107.170.18.108` (key `millena@mihomes`; root also key-only) |
| Layout | Same as evo-dev: `~/MiHomes` (branch `worktree-spec-build-harness`), `~/.mihomes/run/web.env`, `~/.mihomes/media`, backups in `~/.mihomes/backups/pg/` |
| App | `mihomes-dev` on 127.0.0.1:5000, started by `mihomes-boot.service` → `~/.mihomes/run/boot.sh` |
| HTTPS | Caddy (`/etc/caddy/Caddyfile`), Let's Encrypt, auto-renew; `www` → 301 to apex |
| DB | Postgres 16 (systemd), db `mihomes`, role `mihomes` NOSUPERUSER NOBYPASSRLS |
| Firewall | ufw: 22, 80, 443 only. Password SSH off. unattended-upgrades on. 2 GB swap. |

## Files here

- `setup-base.sh` — root, one-time: packages, swap, `millena` user, ssh, ufw, Postgres, Caddy.
- `boot.sh` → `~/.mihomes/run/boot.sh` — like evo-dev's, but cron is gated on `~/.mihomes/run/LIVE`.
- `mihomes-boot.service` → `/etc/systemd/system/` (oneshot, `KillMode=process` so the app outlives it).
- `Caddyfile` → `/etc/caddy/Caddyfile`.

## Scheduled jobs are OFF until switch-over

The jobs in `cli/jobs.py::SCHEDULE` act on the outside world (Telegram/email outbox, Google
Calendar sync, Stripe dunning, digests). Run on two servers at once, every one fires twice.
Until `~/.mihomes/run/LIVE` exists, `boot.sh` installs only `pg-backup.sh` (02:30 UTC) and
`keepalive.sh` (every minute). Creating `LIVE` and re-running boot arms the full crontab via
`scripts/ops/install-cron.sh`.

## Pre-launch passcode

While still building, the whole site sits behind one shared passcode
(`src/mihomes/web/site_passcode.py`). Every page — landing, login, signup — asks for it first;
a correct code sets a 30-day cookie. Webhooks (`/webhooks/…`), `/unsubscribe` and `/static/`
are exempt so Stripe/Telegram keep working.

- **Turn on / change:** add `MIHOMES_SITE_PASSCODE=<code>` to `~/.mihomes/run/web.env`, then
  `sudo ~/.mihomes/run/boot.sh`. Changing the code signs every browser out of the curtain.
- **Turn off (launch):** delete that line and re-run boot.

Use a long code (wrong guesses are only slowed by 1 s). It is a curtain, not security — real
access control is still the account sign-in behind it.

## Deploy

Same as evo-dev: copy `C:\Users\MillenaLim\mihomes-deploy\deploy.sh` to `/tmp` and run it as
millena (pulls, pip installs, backs up, migrates, restarts via `sudo ~/.mihomes/run/boot.sh`).

## Switch-over from evo-dev (not done yet)

1. **Google Cloud Console** → the OAuth client → add redirect URI
   `https://mihomes.ai/auth/google/callback` and JavaScript origin `https://mihomes.ai`.
2. **Freeze evo-dev:** stop the app (`pkill -f '[b]in/mihomes-dev'`) and remove its MiHomes
   crontab block, so nothing writes or sends from there.
3. **Final data copy:** `pg_dump -Fc` + `pg_dumpall --globals-only` on evo-dev → drop/recreate
   `mihomes` on the Droplet → `pg_restore` (feed files on stdin: the postgres user cannot read
   millena's 0600 files). Re-copy `~/.mihomes/media`.
4. **Go live:** `touch ~/.mihomes/run/LIVE && sudo ~/.mihomes/run/boot.sh` (arms all jobs).
5. **Telegram:** `mihomes telegram set-webhook --url https://mihomes.ai/webhooks/telegram`
   (run with web.env loaded), then `mihomes telegram webhook-info`.
6. **Stripe (test mode):** evo-dev used a `stripe listen` forwarder. Here, add a dashboard
   webhook endpoint `https://mihomes.ai/webhooks/stripe` instead and put its signing secret in
   `STRIPE_WEBHOOK_SECRET`.
7. **Laptop backup pull:** repoint the 9am pull from evo-dev to `millena@107.170.18.108`.
8. Leave evo-dev stopped (not wiped) for a week as a fallback.

`mihomes` CLI on the server only reaches Postgres with web.env loaded:

```bash
mapfile -t E < <(grep -v '^\s*$' ~/.mihomes/run/web.env)
env -i "${E[@]}" ~/MiHomes/.venv/bin/mihomes --account <slug> ...
```
