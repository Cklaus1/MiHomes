# Scheduler, alerts and database backups — evo-dev runbook (2026-11-03)

**What this turns on**
- Nightly `pg_dump` of the `mihomes` database (02:30 UTC), checked, 14 days kept on evo-dev.
- A copy pulled to the laptop daily. evo-dev's disk is otherwise the only copy.
- cron, with every `mihomes jobs` workload on its `SCHEDULE` cadence. None has run since the
  2026-11-02 rebuild.
- `jobs alerts` hourly. The web Alerts page finally fills, and alerts clear once the task is
  done or the issue is resolved.

**Why by hand:** evo-dev is a Docker container inside WSL2. It has no systemd, and a rebuild
keeps only `/home`. So cron is installed and started by `scripts/ops/install-cron.sh`, which is
safe to re-run and is called from `boot.sh`.

## 0. Get the code onto evo-dev

evo-dev tracks `worktree-spec-build-harness`. On the laptop, merge the branch, then push:

```bash
cd .claude/worktrees/spec-build-harness
git merge --ff-only ops-scheduler-backups   # or open a PR from ops-scheduler-backups
git push origin worktree-spec-build-harness
```

## 1. On evo-dev (`ssh millena@evo-dev` from PowerShell)

```bash
cd ~/MiHomes && git pull --ff-only origin worktree-spec-build-harness
```
No migration and no new dependencies.

Restart the app so the web process has the new code (alert auto-resolve):
```bash
sudo -n ~/.mihomes/run/boot.sh
```

Take a backup now; don't wait for 02:30:
```bash
bash ~/MiHomes/scripts/ops/pg-backup.sh; tail -3 ~/.mihomes/logs/jobs/pg-backup.log
```
Expect `ok: <bytes>; 1 dump(s) kept`. A `FAIL:` line means nothing was kept.

Install and start cron, and write the crontab:
```bash
bash ~/MiHomes/scripts/ops/install-cron.sh
```
It prints the crontab: 10 `run-job.sh jobs …` lines plus `pg-backup.sh`, between
`# BEGIN MiHomes` / `# END MiHomes`.

Re-arm it on every container start. `boot.sh` runs as root, so add this line to
`~/.mihomes/run/boot.sh`, after Postgres has started:
```bash
sudo -u millena -H bash /home/millena/MiHomes/scripts/ops/install-cron.sh >> /var/log/mihomes-boot.log 2>&1
```

## 2. Check it ran (after ~10 minutes)

```bash
ls ~/.mihomes/logs/jobs/
tail -4 ~/.mihomes/logs/jobs/jobs-drain-outbox.log     # every 5 min
tail -4 ~/.mihomes/logs/jobs/jobs-alerts.log           # at :23 past each hour
```
Each run is logged as `=== <time> mihomes jobs <name>`, its output, then `=== exit <code>`.

Expected noise: `drain-outbox` logs `RESEND_API_KEY is not set` until an email provider is
configured. Queued mail is **not** lost. The account's transaction rolls back and the mail stays
queued.

## 3. Laptop: pull the backups off the box

Run it once by hand from the repo, in PowerShell:
```powershell
.\scripts\Pull-MiHomesBackup.ps1
```
Dumps land in `%USERPROFILE%\MiHomes-backups`, and the newest 30 are kept. To run it daily,
register the scheduled task shown in the script's header. If the laptop is asleep at that time,
the task runs on the next wake.

## What users will see

- **The first `jobs alerts` run makes a burst.** It raises every currently overdue task, every
  high or critical issue open for 3+ days, every low-stock item, and anything expiring within 30
  days. Nothing is sent to Telegram or by email; alerts only appear in the web app.
- **Overdue tasks show twice on the dashboard**, under "Overdue Tasks" and again under "Active
  Alerts". The fix is in the UI/UX follow-up (one "Today" list).

## Restore

```bash
# roles first, only if the cluster is new (the 11-02 rebuild lost the `mihomes` role)
sudo -n -u postgres psql -f ~/.mihomes/backups/pg/nightly/globals-<stamp>.sql
# into a scratch DB first, to look before overwriting anything
sudo -n -u postgres createdb mihomes_restore
sudo -n -u postgres pg_restore -d mihomes_restore "$(cat ~/.mihomes/backups/pg/nightly/LATEST)"
```
To replace the live database, stop the app, `dropdb mihomes`, `createdb -O mihomes mihomes`,
`pg_restore -d mihomes <dump>`, then start the app. Take a fresh `pg-backup.sh` first.

Encrypted columns (connector secrets) can only be read with the same `MIHOMES_SECRET_KEY`. Keep
a copy of `~/.mihomes/run/web.env` somewhere safe that isn't evo-dev; it isn't in the dumps.

## Files

| File | Role |
|---|---|
| `scripts/ops/run-job.sh` | Runs one `mihomes` command with web.env; one at a time per job; logs to `~/.mihomes/logs/jobs/` |
| `scripts/ops/pg-backup.sh` | Dumps as `postgres` (the app role is RLS-bound and would dump no tenant rows); checks the result; rotates |
| `scripts/ops/install-cron.sh` | Installs and starts cron; replaces only the MiHomes block in the crontab |
| `mihomes cron crontab` | Renders that block from `cli/jobs.py::SCHEDULE`, so it can't drift |
| `scripts/Pull-MiHomesBackup.ps1` | Laptop copy, checks the byte count |

Tested 2026-11-03 on the laptop:
- **Backup:** dumped `mihomes_dev`, restored it into a scratch DB, and all 60 tables' row counts
  matched. A database without the app tables fails and keeps nothing.
- **Cron install:** with stubbed `sudo`, `crontab` and `flock`, the installer is idempotent, keeps
  foreign crontab lines, and leaves the crontab untouched on failure.
- **Not tested:** the real apt install and the cron daemon. Those only exist on evo-dev.
