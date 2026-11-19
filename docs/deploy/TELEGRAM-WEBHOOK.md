# Telegram webhook — setup on evo-dev

The bot receives Telegram updates at `POST /webhooks/telegram` instead of polling. Every request is
checked against `TELEGRAM_WEBHOOK_SECRET` before anything is parsed; a sender is resolved to an
account by their `/link` code, and only then is anything written.

evo-dev is reachable only inside the tailnet (Tailscale **Serve** on 443). Telegram is not in the
tailnet, so the webhook path alone is published with Tailscale **Funnel** on port **8443** — a port
both Funnel and Telegram accept — leaving the app itself tailnet-only.

## 1. Values

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | The bot's token from @BotFather. **One bot for the deployment**: the route replies with it and `set-webhook` registers it. A secret — never commit it or paste it into chat. |
| `TELEGRAM_WEBHOOK_SECRET` | A random string, A-Z a-z 0-9 _ - only: `openssl rand -hex 32`. Telegram echoes it on every delivery; an unset value makes the route refuse everything. |

Add both to `~/.mihomes/run/web.env` on evo-dev.

## 2. Deploy

```bash
cd ~/MiHomes && git pull --ff-only origin worktree-spec-build-harness
sudo -n -u postgres pg_dump mihomes > ~/.mihomes/backups/pg/pre-0024-$(date +%Y%m%d-%H%M).sql
mapfile -t E < <(grep -v '^\s*$' ~/.mihomes/run/web.env)
env -i "${E[@]}" .venv/bin/alembic upgrade head      # 0024_gateway_bootstrap_policies
sudo -n ~/.mihomes/run/boot.sh                       # restarts the app with the new env
```

Never `pkill -f bin/mihomes-dev` inside an ssh command — it matches its own command line. Use
`pkill -f '[b]in/mihomes-dev'`.

## 3. Stop any poller

Telegram refuses `getUpdates` while a webhook is set, and a poller left running would fight it.

```bash
pgrep -af 'watchdog|telegram monitor'; tmux ls
```

Kill anything that shows up (`tmux kill-session -t mihomes-bot` for the old setup).

## 4. Funnel — the webhook path only

Funnel must be allowed for the node in the tailnet policy (`nodeAttrs` → `funnel`); if the
command below says it is not enabled, a tailnet admin enables it once.

```bash
sudo tailscale funnel --bg --https=8443 --set-path=/webhooks/telegram http://127.0.0.1:5000/webhooks/telegram
sudo tailscale funnel status
```

`funnel status` must list **only** `:8443/webhooks/telegram` as Funnel (public). The existing
`:443` entry must still be Serve (tailnet only).

**Check the path reaches the route** — a request with a wrong secret must get the route's `401`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'X-Telegram-Bot-Api-Secret-Token: wrong' -d '{}' \
  https://evo-dev.tail6765ef.ts.net:8443/webhooks/telegram
```

`401` → correct. `404` → Funnel forwarded a doubled path; switch the target to the bare origin:

```bash
sudo tailscale funnel --https=8443 --set-path=/webhooks/telegram off
sudo tailscale funnel --bg --https=8443 --set-path=/webhooks/telegram http://127.0.0.1:5000
```

## 5. Register with Telegram

```bash
env -i "${E[@]}" .venv/bin/mihomes --account default telegram set-webhook \
  --url https://evo-dev.tail6765ef.ts.net:8443/webhooks/telegram
env -i "${E[@]}" .venv/bin/mihomes --account default telegram webhook-info
```

`webhook-info` is the real outside check — Telegram is outside the tailnet. A `Last error` of
`none` after a message has been sent means deliveries are landing.

To go back to polling: `telegram delete-webhook`.

## 6. Link yourself, then the group

1. **You (an owner or admin)**: Settings → Connections → Telegram → get a code, then DM the bot
   `/link <code>`. The bot answers "You're linked."
2. **The group**: in the group, send `/linkchat`. The bot answers "This group is now linked to
   your estate." From then on every message in the group belongs to the estate — including from
   members with **no MiHomes login**, who are processed as untrusted: they can report issues and
   tasks, but expenses, PTO, work orders, task completion and resolving issues need a linked or
   allow-listed sender. Nobody in a linked group is ever told "I don't recognise you".
   `/unlinkchat` (owner/admin) undoes it.
3. **Which house** a group's messages belong to, if the estate has more than one property:

   ```bash
   env -i "${E[@]}" .venv/bin/mihomes --account default telegram link-chat --chat-id <group id> --property <slug>
   ```

   With a single property this is not needed.
4. Send one fresh message in the group and confirm it is logged **once** (an issue or task appears,
   the bot confirms in the group).

Migration `0025_telegram_chat_bindings` must be applied (step 2 runs `alembic upgrade head`).

## Troubleshooting

| Symptom | Cause |
|---|---|
| `webhook-info` shows `Wrong response from the webhook: 401` | `TELEGRAM_WEBHOOK_SECRET` in web.env ≠ the one registered — re-run `set-webhook` after restarting |
| `… 404` | Funnel path mapping — see step 4 |
| `… Connection refused` / timeout | Funnel not running (`funnel status`) or the app is down |
| Every message answered "I don't recognise you" | Sender has not sent `/link <code>`, or migration 0024 not applied |
| No reply at all to an unlinked sender | `TELEGRAM_BOT_TOKEN` not in the app's environment (logged as a warning) |
| `set-webhook` says the token is not set | Run it through `env -i "${E[@]}"` so web.env is loaded |
