# Chat notifications on evo-dev (SPEC-014 Step 9)

What has to be true on the server for chat push and digest emails to work. Run as `millena` on
evo-dev unless stated. Back up first (`TELEGRAM-WEBHOOK.md` §2): this deploy runs migration
`0028_chat_notifications`.

## 1. Environment — `~/.mihomes/run/web.env`

| Variable | Value | Notes |
|---|---|---|
| `VAPID_PUBLIC_KEY` | from step 2 | Sent to browsers; not a secret |
| `VAPID_PRIVATE_KEY` | from step 2 | **Secret.** Never paste it into chat |
| `VAPID_SUBJECT` | `mailto:<operator address>` | Push services contact this on abuse |
| `MIHOMES_BASE_URL` | `https://evo-dev.tail6765ef.ts.net` | Otherwise every email link points at production |
| `EMAIL_PROVIDER` / its key | as already configured | Digests sit in the outbox until `drain-outbox` sends them |

Without the VAPID variables the job still runs and digests still go out; push is simply off and
the Settings card says so.

## 2. Generate the key pair once (on evo-dev, values not shown)

```bash
cd ~/MiHomes
read -r PUB PRIV < <(.venv/bin/python -c "from mihomes.services.chat.push import generate_vapid_keys as g; print(*g())")
printf 'VAPID_PUBLIC_KEY=%s\nVAPID_PRIVATE_KEY=%s\n' "$PUB" "$PRIV" >> ~/.mihomes/run/web.env
unset PUB PRIV
grep -c '^VAPID_' ~/.mihomes/run/web.env    # expect 2 (add VAPID_SUBJECT by hand → 3)
```

**Rotating the pair invalidates every browser subscription** (browsers pin the public key and then
answer 403, not 410, so rows are not cleaned up automatically). If you ever rotate, also run
`DELETE FROM push_subscriptions;` and ask people to turn push back on.

## 3. Cron — two lines, both required (U1)

evo-dev currently has **no cron installed** (`crontab: command not found`). Installing it needs the
server owner's OK. Then, with the same env-file loading the deploy script uses:

```
*/5 * * * *  cd ~/MiHomes && env -i $(grep -v '^\s*$' ~/.mihomes/run/web.env | xargs) .venv/bin/mihomes jobs chat-notify  >> ~/.mihomes/run/cron.log 2>&1
*/5 * * * *  cd ~/MiHomes && env -i $(grep -v '^\s*$' ~/.mihomes/run/web.env | xargs) .venv/bin/mihomes jobs drain-outbox >> ~/.mihomes/run/cron.log 2>&1
17 4 * * *   cd ~/MiHomes && env -i $(grep -v '^\s*$' ~/.mihomes/run/web.env | xargs) .venv/bin/mihomes jobs chat-prune-events >> ~/.mihomes/run/cron.log 2>&1
```

Without `drain-outbox`, digests queue forever. (Values containing spaces need the `mapfile`
form from `deploy.sh` instead of `xargs`.)

## 4. Checks after deploy

1. `curl -sI https://evo-dev.tail6765ef.ts.net/sw.js` → `200`, `Service-Worker-Allowed: /`.
2. Settings → Chat notifications → "Turn on push for this device" on Android Chrome and desktop;
   on iPhone, first Share → Add to Home Screen, open from the icon, then turn it on (iOS 16.4+).
3. Have someone send you a DM, leave it unread 10–15 minutes: one push ("Direct message — New
   message", no name, no text). About an hour later, one digest email with counts only.
4. Post 21 times in a minute: the 21st shows a "sending quickly" toast and the text stays.
5. Open 7 chat tabs as one person: the 7th shows no live updates (its stream got 429).

## 5. Load check (A34)

From a tailnet machine, signed in as a test member (save the browser's session cookie to a file):

```bash
python scripts/chat_sse_load.py --base https://evo-dev.tail6765ef.ts.net \
    --cookie-file cookie.txt --clients 50 --minutes 10
```

Pass = all of SPEC-014 §6 Step 9's seven criteria; paste the script's summary into the deploy
commit. Note: the 6-streams-per-member cap applies, so 50 clients need ≥ 9 member sessions —
pass several cookie files, or raise `MAX_STREAMS_PER_MEMBER` for the duration of the test only.
