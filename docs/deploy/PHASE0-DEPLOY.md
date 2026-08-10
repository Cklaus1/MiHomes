# Phase 0 deploy — landing page + waitlist

**Target:** Fly.io, single region, managed Postgres (D2, D3 — `MULTITENANCY` §11).
**App:** `mihomes-landing`, built from `Dockerfile.landing`, serving **only** the
Phase 0 routes.

> **The landing app is a stripped instance.** It shares the stack with the
> single-user product and nothing else (D1). `src/mihomes/web/` mounts 22 routers
> with **no authentication** over live estate data; none of it is reachable here,
> and `tests/integration/test_landing_app.py::test_existing_routes_are_404` exists
> to keep it that way. Do not "simplify" the deploy by serving `mihomes-web`.

---

## 1. One-time setup

```bash
fly launch --no-deploy --name mihomes-landing --region iad
fly postgres create --name mihomes-db --region iad
fly postgres attach mihomes-db --app mihomes-landing   # sets DATABASE_URL
```

`fly postgres attach` writes `DATABASE_URL` as a secret. Everything else is set by
hand:

```bash
fly secrets set \
  RESEND_API_KEY=...              \
  EMAIL_FROM='MiHomes <no-reply@send.mihomes.ai>' \
  GOOGLE_CLIENT_ID=...            \
  GOOGLE_CLIENT_SECRET=...        \
  SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
```

**Secrets never live in the repo** (§10, `BILLING` §9). `.env`, `.env.*`, `*.pem`
and `*secret*.json` are gitignored, and `.dockerignore` keeps them out of the build
context too — anything in the context can land in an image layer even if the
Dockerfile never copies it.

### Google OAuth client

Authorized redirect URI, exactly:

```
https://mihomes.ai/auth/google/callback
```

Scopes are `openid email profile`. Phase 0 requests `access_type=online` — no
refresh token, because there is nothing to refresh against: the flow reads the
verified email and stops (D8).

---

## 2. Migrations are a release step, never a boot step

`fly.toml` carries:

```toml
[deploy]
  release_command = "alembic -n landing upgrade head"
```

**Why this matters (D9, §7-N4).** `web/server.py` calls `init_db()` on startup
today. That is safe for one process and wrong for several: with more than one Fly
machine, concurrent `alembic upgrade` races against the same database. Fly runs the
release command once, in its own machine, before the new version takes traffic.

**Note the `-n landing`.** The landing app owns `alembic_landing/`, a separate
migration tree whose only revision creates the `waitlist` table. Running the main
`alembic/` tree here would try to replay 40 SQLite-era revisions against Postgres —
which fails outright, and would create 37 unrelated tables if it did not (D1, D3).
`alembic/` is excluded from the image for the same reason.

---

## 3. DNS

Apex serves the marketing page; `app.` is reserved for Phase 1; `send.` is the
transactional sending domain (D12, D13).

| Host | Type | Value | Purpose |
|---|---|---|---|
| `mihomes.ai` | `A` / `AAAA` | *(from `fly ips list`)* | landing page |
| `www.mihomes.ai` | `CNAME` | `mihomes.ai` | 301 → apex |
| `app.mihomes.ai` | `A` / `AAAA` | reserved | Phase 1; a 503 placeholder is fine |
| `send.mihomes.ai` | `MX` | *(from Resend)* | bounce handling |
| `send.mihomes.ai` | `TXT` (SPF) | `v=spf1 include:amazonses.com ~all` | *(use the exact value Resend shows)* |
| `resend._domainkey.send.mihomes.ai` | `TXT` (DKIM) | *(from Resend)* | signing |
| `_dmarc.mihomes.ai` | `TXT` | `v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai` | reporting |

### The DMARC value is deliberately relaxed — do not "harden" it

```
v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai
```

**No `adkim=s`, no `aspf=s`.** `BILLING:224` explains why: Resend's return-path
sits on its own sub-label, so strict alignment fails legitimately-signed mail.
`GTM:273` publishes the strict variant in a copy-pasteable table and is **wrong**
(`PRD_REVIEW` A6). This spec uses the `BILLING` value.

The failure mode is quiet and expensive: confirmation emails stop being delivered,
nobody confirms, and the Phase 0 funnel reads as *no demand* rather than as broken
mail. `tests/unit/test_deploy_docs.py::test_dmarc_relaxed_alignment` guards the
value so it cannot come back.

Start at `p=none` and read the `rua` reports for a couple of weeks before
considering `quarantine`.

---

## 4. Deploy

```bash
fly deploy                      # runs the release_command, then rolls machines
fly logs --app mihomes-landing
curl -fsS https://mihomes.ai/healthz     # {"status":"ok"}
```

`/healthz` checks database connectivity and returns **503** when Postgres is
unreachable, so a broken deploy fails its healthcheck instead of sitting in
rotation looking healthy. The body carries a status and nothing else — the endpoint
is public and unauthenticated.

### Verify the isolation after every deploy

```bash
curl -o /dev/null -w '%{http_code}\n' https://mihomes.ai/properties   # expect 404
curl -o /dev/null -w '%{http_code}\n' https://mihomes.ai/staff        # expect 404
```

Anything other than 404 means the single-user app is exposed. Treat it as an
incident, not a bug: those routes have no authentication and hold real estate data.

---

## 5. Pre-launch checklist

Nothing below is done yet. The last three are founder decisions that block
**launch**, not the build (§1.3) — they are here so they stay visible.

- [ ] `send.mihomes.ai` verified in Resend; SPF, DKIM and MX passing
- [ ] Test confirmation email lands in an inbox, not spam
- [ ] Apex serves the landing over HTTPS; `www` 301s to apex; HSTS on
- [ ] `app.mihomes.ai` reserved (a 503 placeholder is fine)
- [ ] **O1** — ToS + Privacy published and linked in the footer
- [ ] **O2** — founding-member offer wording final
- [ ] **O3** — waitlist gate number recorded in `GTM_LAUNCH_PLAN.md`

**O1 is legally load-bearing.** The footer links to `/legal/terms` and
`/legal/privacy` and they **404 today**, by design — no placeholder pages were
stubbed to make them resolve. `GTM:351` flags counsel review, and collecting the
first real email address without them published is the thing this box prevents.

**O2:** the page and the confirmation email both promise "a founding-member offer"
and say nothing more, because the terms are undecided. Do not invent them.

**O3:** the gate number decides when Phase 0 ends. `GTM:293` proposes ≥250
confirmed at ≥3% over a trailing two weeks with ≥500 sessions; `SAAS_PRD:125`
leaves the number to the founder. `mihomes.services.waitlist.confirmed_count()`
reports the current figure.

---

## 6. Rollback

```bash
fly releases --app mihomes-landing
fly deploy --image <previous-image-ref>
```

The one landing migration has a working `downgrade`, but rolling the schema back is
almost never the right move: `waitlist` rows are the Phase 0 funnel baseline and
`confirmed_at` is what the gate counts. Roll the *image* back and leave the table
alone.
