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

Deploy the usual way. The deploy script does the pull, deps, a pre-deploy dump, migrations (none
in this change), and a real restart. A bare `boot.sh` does **not** restart a running app: it
sees the process and starts nothing, so the old code keeps serving.
```powershell
# from the laptop, PowerShell
scp C:\Users\MillenaLim\mihomes-deploy\deploy.sh millena@evo-dev:/tmp/deploy.sh
ssh millena@evo-dev "bash /tmp/deploy.sh"
```

Take a backup now; don't wait for 02:30:
```bash
bash ~/MiHomes/scripts/ops/pg-backup.sh; tail -3 ~/.mihomes/logs/jobs/pg-backup.log
```
Expect `ok: <bytes>; 1 dump(s) kept`. A `FAIL:` line means nothing was kept.

### Pre-flight: read before arming cron

No job has run since the rebuild. Two of them can **change an account's plan**:
- `trial-sweep` moves an expired trial to Free.
- `reconcile` copies Stripe's state over local drift.

`calendar-sync` writes to connected Google Calendars every 15 minutes. Look first; every command
here is read-only:

```bash
sudo -n -u postgres psql -d mihomes \
  -c "select slug, plan, subscription_status, trial_ends_at, stripe_customer_id is not null as stripe, deactivated_at from accounts order by slug" \
  -c "select count(*) as calendar_connections from calendar_connections"
for j in trial-sweep reconcile dunning drips weekly-digest; do
  bash ~/MiHomes/scripts/ops/run-job.sh jobs $j --dry-run
  tail -3 ~/.mihomes/logs/jobs/jobs-$j---dry-run.log
done
```
Stop and decide before going on if:
- Belle (`default`) shows `trialing` with a past `trial_ends_at`;
- `trial-sweep` would expire an account you use;
- `reconcile` reports drift you didn't expect.

### First alerts run — the first one under the app role and RLS

Every local test ran as the `postgres` superuser, which bypasses row-level security. This run is
the real check:
```bash
sudo -n -u postgres psql -d mihomes -Atc "select count(*) from tasks where due_date < current_date and status not in ('COMPLETED','CANCELLED')"
bash ~/MiHomes/scripts/ops/run-job.sh jobs alerts; tail -3 ~/.mihomes/logs/jobs/jobs-alerts.log
```
Expect `Alerts: N raised, 0 cleared; 0 account(s) failed`, with N at least the overdue count
above, plus open high/critical issues and low stock. **If the overdue count is above 0 and the
job says `0 raised`, RLS is hiding rows. Stop and don't arm cron.** A second run must say
`0 raised`.

### Arm cron

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

**That queue grows.** Drips, weekly digests and chat digests keep queuing with nowhere to go. The
day `RESEND_API_KEY` is set, weeks of stale mail would leave within 5 minutes. Review or purge the
outbox *before* configuring a provider.

## 3. Laptop: pull the backups off the box

Copy the script next to `deploy.sh`. The main checkout is on stale `main`, and worktrees get
removed, so neither is a stable path for a scheduled task. Then run it once and schedule it, in
PowerShell:
```powershell
Copy-Item .claude\worktrees\ops-scheduler\scripts\Pull-MiHomesBackup.ps1 C:\Users\MillenaLim\mihomes-deploy\
C:\Users\MillenaLim\mihomes-deploy\Pull-MiHomesBackup.ps1
$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\Users\MillenaLim\mihomes-deploy\Pull-MiHomesBackup.ps1'
Register-ScheduledTask -TaskName 'MiHomes backup pull' -Action $a -Trigger (New-ScheduledTaskTrigger -Daily -At 9am) -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
```
Dumps land in `%USERPROFILE%\MiHomes-backups`, and the newest 30 are kept. If the laptop is
asleep at 9am, the task runs on the next wake.

## What users will see

- **The first `jobs alerts` run makes a burst.** It raises every currently overdue task, every
  high or critical issue open for 3+ days, every low-stock item, and anything expiring within 30
  days. Nothing is sent to Telegram or by email; alerts only appear in the web app.
- **Resolve means "dealt with".** A resolved alert is not raised again until its task, issue or
  stock item changes. An overdue task that is missed again on a new due date gets a fresh alert.
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

## Crash restart (2026-11-04)

`scripts/ops/keepalive.sh` runs every minute from the same crontab block. It checks three things:

- **Postgres** (`pg_isready`): started at once if it is down.
- **The app**: does `http://127.0.0.1:5000/login` answer within 10 s? A hung app counts as down.
  After two failed checks in a row, the app is killed if it is still running, then started the
  same way `boot.sh` starts it. A deploy restarts the app in seconds, so the watchdog never
  races `deploy.sh`.
- **The Stripe sandbox listener**: restarted if it isn't running, but only when
  `~/.local/bin/stripe` and `STRIPE_SECRET_KEY` both exist.

Where to look:
- Restarts are logged in `~/.mihomes/logs/jobs/keepalive.log`. Quiet runs write nothing.
- `~/.mihomes/run/web.log` is appended to, not truncated, so the crash trace stays above the
  `=== … restarted by scripts/ops/keepalive.sh ===` marker.

It's installed by the next deploy: `boot.sh` runs `install-cron.sh`, which adds the line. To
prove it works, stop the app, wait up to two minutes, and it comes back:
```bash
pkill -f '[b]in/mihomes-dev'; sleep 150; tail -3 ~/.mihomes/logs/jobs/keepalive.log
```
