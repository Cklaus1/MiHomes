# Email: turning on Resend (2026-11-13)

All mail (signup confirmation, password reset, invites, notifications, digests, later marketing)
goes through Resend. The code is done: `services/email/`. This runbook is the outside-the-code
part. Do the steps in order. **Step 6 (deploying email verification) must come last**, or
every new signup is stranded at "check your inbox".

Free plan (checked 2026-11-13 at resend.com/pricing): 100 emails/day, 3,000/month, 3 domains.
That is fine for the trial with Chris's household. Upgrade before a public launch.

## 1. Add the sending domain in Resend

Resend → Domains → Add domain → **`send.mihomes.ai`** (region: US East).
Resend then shows 3 or 4 DNS records: an MX and a TXT (SPF) for bounces, and a TXT (DKIM).

## 2. Add the records in GoDaddy

GoDaddy → My Products → `mihomes.ai` → DNS → Add record, once per record Resend shows.

**Type only the part before `.mihomes.ai` in GoDaddy's Name box. GoDaddy adds the domain.**

| Resend shows the name | Type this in GoDaddy's Name box |
|---|---|
| `send.mihomes.ai` | `send` |
| `resend._domainkey.send.mihomes.ai` | `resend._domainkey.send` |
| `send.send.mihomes.ai` (if shown this way) | `send.send` |

Pasting the full name creates `send.mihomes.ai.mihomes.ai`, which never verifies. Copy the
**value** exactly as Resend shows it. For the MX record, use the priority Resend gives.

Add a DMARC record as well (one per domain, for `mihomes.ai` itself):

| Type | Name | Value |
|---|---|---|
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai` |

Keep it `p=none` with no `adkim=s`/`aspf=s`. Strict alignment fails Resend's legitimately
signed mail (see `PHASE0-DEPLOY.md` §3). `dmarc@mihomes.ai` needs to be a real inbox or alias to
receive the reports.

Back in Resend, press **Verify**. GoDaddy usually propagates in minutes; allow up to a few hours.

## 3. Create an API key

Resend → API Keys → Create. Permission: **Sending access**, domain: `send.mihomes.ai`.
Copy it once (Resend will not show it again). Do not paste it into chat or commit it.

## 4. Clear the stale queue (both servers)

Weekly and chat digests and onboarding emails have been queuing for weeks with no provider.
The moment a key is set, `drain-outbox` sends all of it within 5 minutes. The Droplet's database
is a copy of evo-dev's, so it holds the same rows.

On each server:

```bash
# look first
sudo -n -u postgres psql -d mihomes -c "select template, count(*), min(created_at) \
  from email_outbox where sent_at is null and failed_at is null group by template order by 2 desc"

# then mark everything unsent as failed (kept for the record, never sent)
sudo -n -u postgres psql -d mihomes -c "update email_outbox set failed_at = now(), \
  last_error = 'purged before Resend go-live' where sent_at is null and failed_at is null"
```

## 5. Set the key on the server real users use

Today that is **evo-dev**. On the Droplet, do it at switch-over (its jobs are off until then).
Add to `~/.mihomes/run/web.env` (no quotes, matching the file's style):

```
RESEND_API_KEY=re_xxxxxxxx
EMAIL_FROM=MiHomes <no-reply@send.mihomes.ai>
```

Restart the app (`deploy.sh` restart, as usual). Then test:

1. Sign out, open `/password/reset`, enter **your own** address.
2. The email should arrive within a minute, from `no-reply@send.mihomes.ai`.
3. In Gmail, "Show original" should read `SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`.
4. `tail ~/.mihomes/logs/jobs/jobs-drain-outbox.log` should no longer say `RESEND_API_KEY is not set`.

Before the domain is verified, Resend only delivers to the address the Resend account was
created with, so test with that address if step 2 is still pending.

## 6. Deploy email verification

Branch `email-verification` (migration `0031`). Deploy only after step 5 works on that server.
Existing users are marked verified by the migration and notice nothing. New password signups
get a "Confirm your email" message and cannot sign in until they click it. Google sign-ins
are unaffected.

Check after deploying: sign up with a fresh address (a `+test` alias works, e.g.
`you+verify1@gmail.com`), confirm the email arrives, click it, press **Confirm my email**, and
land on onboarding.

## Later

- **Bounces and spam complaints**: a Resend webhook to `/webhooks/resend` that adds the address
  to `email_suppressions`. Not built yet; next on the list.
- **Marketing**: use a second domain (e.g. `news.mihomes.ai`), so complaints about campaigns
  cannot hurt delivery of signup and reset mail. The free plan's 3 domains allow it.
- **Replies**: `no-reply@` goes nowhere. Set a real `support@mihomes.ai` inbox before launch.
