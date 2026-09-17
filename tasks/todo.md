# MiHomes — Project State & Next Actions

Last updated: 2026-08-06

---

## ACTIVE: SPEC-001 Build Loop (branch `spec-build`)

> Harness: `tasks/build-loop-spec001.md` · Conventions: `tasks/build-loop-conventions.md` ·
> Spec: `docs/specs/SPEC-001-phase0-landing-waitlist.md` · run via `/loop tasks/build-loop-spec001.md`.
> Mirror of the §1 DAG — authoritative checkboxes live in `build-loop-spec001.md`; this is the
> at-a-glance view. Group `[x]` = committed + full suite green.
>
> **Not yet run.** Harness authored 2026-08-06, pending review.

- [ ] **G1 — `mihomes.ids`**: UUIDv7 app-side `new_id()` + the 3.11 fallback path — A1, A2
- [ ] **G2 — `Waitlist` model + migration**: global table (no `account_id`, no RLS) + first Postgres migration — A3 + round-trip gate
- [ ] **G3 — email package**: Protocol, Console/Resend providers, factory, render, EmailService — A8, A9 · *reused verbatim in Phases 2–4*
- [ ] **G4 — waitlist service**: signup/confirm/position, idempotent, hash-only token — A4, A5, A6, A7
- [ ] **G5 — landing app skeleton**: `create_landing_app()`, `/healthz`, rate limit, **single-user app not mounted** — A11, A13, A17
- [ ] **G6 — templates + `GET /`**: nine sections, no prices, Telegram-only chat card — A16
- [ ] **G7 — `POST /waitlist` + confirm**: double opt-in loop, send-failure isolation, no enumeration — A10, A12
- [ ] **G8 — Google OAuth stub**: waitlist row only, no users row, no cookie; forged token rejected — A14, A15
- [ ] **G9 — deploy artifacts**: Dockerfile, fly.toml, PHASE0-DEPLOY.md with the relaxed-alignment DMARC — A18
- [ ] **G-Final — compound stop**: F.1 suite · F.2 all 18 criteria · F.3a steps walk · F.3b criteria walk · F.4 report

**Stop condition (all five):** every DAG box `[x]` · every §6 step tasked **and** every §8
criterion gated · full suite green · smoke *(N/A this phase — declared, not dropped)* · all 18
§8 criteria green by their own named tests. No intermediate review stops.

**Remaining specs** (`docs/specs/`): SPEC-008 is unharnessed. Author each one's harness after the
preceding run's lessons land. SPEC-007 does not exist by design.

**SPEC-006 — harness authored 2026-08-29, not run.** `tasks/build-loop-spec006.md` +
`scripts/spec006_reconcile.py` (25/25 criteria gated, mutation-checked five ways). **The blocker
above was half right and the distinction matters**: `telegram-bot` ↔ `origin/main` is still
unreconciled (30 ahead / 13 behind, unowned) — but the pre-flight *measured* every module §3–§5
consumes as present on `worktree-spec-build-harness`, so the build can proceed here. Harness §0.2
says at length why "the code is here" is not "the branches are reconciled", and carries the
reconciliation as U1 so a green run cannot be read as closing it.

---

## DONE: Hardening Build Loop (branch `hardening-build`)

> Harness: `tasks/build-loop.md` · Spec: `tasks/hardening-spec.md` (v2, adversarially verified) · run via `/loop tasks/build-loop.md`.
> Mirror of the §4 DAG — authoritative checkboxes live in `build-loop.md`; this is the at-a-glance view. Group `[x]` = committed + full suite green.

- [x] **G0 — Stop-the-bleeding (P0)**: D5 demo boot · D7+D8 watchdog · D6+H34+M45 uploads · D1 backup/restore · D3+H10+M32+M33 tools.py · M44 query_inventory · H8+H9 model · H11 round-limit
- [x] **G-R4 — Reconciliation migration** (extra-gated): H7 batch · H2 drop HaEntity · H1+M11–M15+M0 FKs/indexes/uniques/enum-defaults · H5 downgrade · H6 recasting · H17 alerts.property_id · M14 vendor_properties — *846 tests green; G-R4a/b/c/d gates pass; autogenerate empty; env.py FK-OFF-during-migration fix*
- [x] **G-R5 — Money int-cents**: M1 TypeDecorator (`type/money.py`, 15 columns) · cast migration `b3f5c1d9a72e` (dollars→int-cents, round-trip clean) · M2+M3+M4+M6 finance math — *859 tests green; single head; autogenerate empty*
- [x] **G-R1 — Ban silent swallows**: R1 census→`logger.exception` (42 sites/17 files) · smoke test (every tool+report) · L1 logging config — *882 tests green; smoke net caught a real latent bug (`TaskStatus.DONE`→`COMPLETED`)*
- [x] **G-Svc — Silent-corruption sweep**: H15 double-count · H16 health period · H18 daily recurrence · H19 backfill · H20 calendar · H21 vendor soft-delete · H22 WO cost · H23 issue↔WO link · H12 date · H13 image-capability · H14 stream session · M5+M10 fuzzy · M34+M35+M37 provider content — *933 tests green*
- [x] **G-R2 — Gateway dedup core**: R2 `review_common.py` · H24–H28 · H35 PTO notifier · H36 vendor name · M21 poison-guard · M22–M31 · L12–L15 — *994 tests green + 5 bridge JS tests; shared `dedup.py`/`pid.py`; per-jid replies + sender allowlist (M25–M27); WA burst-drain (M30); `whatsapp stop` + telegram-stop reaps WA/bridge (M31); bridge reconnect guard + log compaction (M29)*
- [x] **G-Web — Web hardening**: M43 delete reports · H29 chart · H31+R3 error handlers · M40 ValueError · H30 CSRF/Host · H32 zip · M16 form parse · M17 active toggle · M18 XSS · M19+M20 — *1038 green, ruff-clean*
- [x] **G-CLI — CLI parsing + tail**: M39+M40 date-parse→BadParameter · M41 import-csv exit code · M42 dashboard aggregation · L2–L11 hygiene (belle-estate default, real_data idempotency, `--format` Enum, Rich escape, `--accept` guard, hide_input, PTO state guard, nullable vendor scores + migration, iCal/config/openai/bridge/watchdog residue, create-property full page) — *1076 tests green; alembic check clean; latent `weekly_report.full_name` bug found+fixed+logged*
- [x] **G-Final — Compound stop**: full suite green (1080) · smoke green (18) · spec reconciled (P0 7/7, P1 36/36, P2 43+2 deferred, P3 15/15, R1–R5, A1) · empty autogenerate (single head 4db594964c82) · end-of-run report → `tasks/build-loop-report.md` — *F.3 caught 4 DAG-omissions: H3/M8/M9 fixed test-first, M7 + M9-tz deferred*

**Stop condition (all four):** every DAG box `[x]` · every spec finding landed-or-deferred · full suite green · R1 smoke green. No intermediate review stops.

---

## What's Done

### Phase 1a — Core CLI (Complete)
- [x] Project skeleton: pyproject.toml, src layout, alembic, CLI entry point
- [x] Database foundation: SQLAlchemy models, TimestampMixin, SlugMixin, WAL mode
- [x] Slug system: generate, ensure_unique, resolve_identifier (ID or slug accepted everywhere)
- [x] Audit log: immutable changelog on every create/update/delete
- [x] Property CRUD: types, status, occupancy (occupy/vacate), climate zones
- [x] Space CRUD: rooms and areas per property
- [x] Staff management: profiles, roles, assignments, PTO tracking, workload/schedule
- [x] Vendor management: profiles, service categories, areas, ratings
- [x] Task system: CRUD, recurrence engine (daily/weekly/biweekly/monthly/quarterly/seasonal/annual), priority, assignment
- [x] Issue tracking: full lifecycle (reported → assessed → scheduled → in-progress → resolved → verified), severity
- [x] Budget + transactions: per-property/category budgets, expense logging, variance tracking
- [x] Notes: attachable to any entity
- [x] Init wizard: `mihomes init` + `mihomes init --demo` with sample data
- [x] Dashboard: Rich terminal layout across all properties
- [x] Audit CLI: `mihomes audit <entity> <id>`, `mihomes audit --recent`
- [x] 742 passing tests

### Phase 1b — Supporting Features (Complete)
- [x] Vendor contracts: tracking, renewal alerts
- [x] Recurring expenses: auto-generate transactions on schedule
- [x] Insurance policies: tracking, renewal alerts, coverage gap detection
- [x] Templates and checklists: create, manage, instantiate into tasks
- [x] Tags: polymorphic, attachable to any entity, filter by tag
- [x] Global search across all entity types
- [x] Alerts system: overdue tasks, expiring items, budget variances, SPACE-sorted
- [x] Unified reporting: `mihomes report` with property, vendor, spending, estate views
- [x] Configuration system: `mihomes config set/list/reset`
- [x] Backup/restore + `mihomes doctor` integrity checks
- [x] Data archival: `mihomes archive run/stats`
- [x] CSV import/export

### Phase 2 — AI Intelligence (Complete)
- [x] AIProvider abstraction: ClaudeProvider, OpenAIProvider, OllamaProvider, NIMProvider
- [x] `mihomes ai ask` with context-aware routing to 13 specialist roles
- [x] `mihomes ai review` for proactive recommendations
- [x] SPACE framework integrated into all AI prioritization
- [x] AI-assisted issue severity assessment
- [x] AI-assisted import (paste unstructured text → structured records)
- [x] Conversation history storage (AIConversations)
- [x] Weekly report service with AI narrative
- [x] Predictive maintenance service
- [x] Property health score service

### Phase 3 — Depth + Communication (Mostly Complete)
- [x] Asset management: appliances, vehicles, valuables, consumables
- [x] Work order workflow: estimate → approve → assign → complete → verify
- [x] Vendor performance tracking and comparison
- [x] Event and guest management with preference profiles
- [x] Document storage and expiration tracking
- [x] Seasonal checklists and built-in task templates
- [x] Google Calendar integration (bidirectional sync)
- [x] iCal file import
- [x] Zone management
- [x] Weather-aware task scheduling
- [x] Playbook system: `mihomes playbook run <name>` backed by knowledge/playbooks/
- [x] Resume ranker: AI-assisted candidate evaluation
- [x] WhatsApp gateway: bridge code, message parsing, review queue, estate context injection
- [ ] **WhatsApp pairing — BLOCKED** (Baileys "cannot link device" error on wacli-integration branch)
- [x] Staff PTO request workflow (CLI side)

### Phase 4 — Automation (Partial)
- [x] Automation service and CLI (`mihomes automation`)
- [x] Cron helper (`mihomes cron setup`)
- [x] Weather API integration + weather-triggered task scheduling
- [ ] Scheduled AI digest (daily/weekly via cron) — not wired end-to-end
- [ ] Smart reorder alerts for consumable inventory — service exists, alerts not triggered

### Bonus — Beyond PRD (Built)
- [x] **Home Assistant integration**: `src/mihomes/ha/`, `src/mihomes/cli/ha.py`, `src/mihomes/services/ha_sync.py`, `src/mihomes/models/ha_entity.py`
- [x] **HA custom component**: `custom_components/mihomes/` (sensors, binary sensors, todo integration)
- [x] **Docker deployment**: `Dockerfile`, `docker-compose.yml` (MiHomes + Home Assistant stack)
- [x] **HA addon**: `addon/` (installable from HA addon store)
- [x] **FastAPI + HTMX web UI** (ui-frontend branch): dashboard, properties, issues, tasks, staff, vendors, budget, contracts, assets, alerts, work orders — all with inline editing across 7 detail tabs
- [x] **REST API**: `src/mihomes/api/` — routes, schemas, HA-facing endpoints
- [x] **Knowledge base**: hiring playbook, onboarding, emergency, daily ops, housekeeper, communication, separation SOPs
- [x] **Hiring system**: candidate evaluation files, resume ranker, phone screening rubric (25-pt)

---

## What's Next

### Active: Telegram Bot (telegram-bot branch)
Replace the WhatsApp/Baileys bridge with a Telegram bot — no Node.js, no pairing, plain urllib REST to the Bot API.

**Files to create:**
- [x] `src/mihomes/services/gateways/telegram/__init__.py`
- [x] `src/mihomes/services/gateways/telegram/client.py` — urllib REST client (getUpdates, sendMessage, getFile, getMe)
- [x] `src/mihomes/services/gateways/telegram/review.py` — reuse WhatsApp review.py (same message dict contract, update prompt string only)
- [x] `src/mihomes/services/gateways/telegram/responder.py` — adapt from WhatsApp responder (swap JID → chat_id, swap client calls)
- [x] `src/mihomes/services/gateways/telegram/extractor.py` — adapt from WhatsApp extractor
- [x] `src/mihomes/cli/telegram.py` — CLI: setup, status, chats, link-chat, unlink-chat, send, monitor, review, watchdog

**Files to modify:**
- [x] `scripts/watchdog.py` — replaced bridge health check with Telegram bot health check
- [x] `src/mihomes/cli/__init__.py` — registered `telegram` app
- [x] `pyproject.toml` — updated coverage omit

**Config keys (no DB migration):**
- `telegram.bot_token` — from BotFather
- `telegram.chat_links` — JSON `{chat_id: property_slug}`
- `telegram.last_update_id` — deduplication offset
- `telegram.pto_approver_id` — Telegram user_id of approver (replaces phone-based lookup)

**Design decisions:**
- Internal message dict keys unchanged (`jid`, `senderName`, `text`, `hasMedia`, `mediaPath`, `propertySlug`) — `chat_id` fills the `jid` slot
- No `python-telegram-bot` dependency — plain urllib REST matches existing WhatsApp client pattern
- Staff reporter matching: name-only (Telegram gives no phone numbers)
- Media: download to `~/.mihomes/media/telegram/` before review pipeline sees it

**Prerequisite (user):** Create a bot via BotFather in Telegram → get token → `mihomes config set telegram.bot_token <token>`

### Unblock (Highest Priority)
- [x] ~~Fix Baileys pairing error~~ — replaced by Telegram bot (no pairing required)

### Web UI (ui-frontend branch)
- [ ] **Merge ui-frontend → main** — branch is stable enough; divergence debt compounds weekly
- [ ] **AI chat panel on dashboard** — input + streaming response div; `mihomes ai ask` equivalent in the browser
- [ ] **Mobile-responsive views** for issue logging and task completion — field use case (walk-through inspections)
- [ ] **WhatsApp review queue** web UI — when Baileys is unblocked, `mihomes whatsapp review` needs a web surface
- [ ] Search page
- [ ] Audit trail page
- [ ] Reports page

### Operational
- [ ] **Pre-commit hook** (ruff + basic lint) — same bug categories keep surfacing in code review; a hook eliminates them at source
- [ ] **Update PRD** to include HA integration as a formal phase and the Docker/addon deployment story
- [ ] Confirm `mihomes recurring generate` end-to-end works in production
- [ ] Wire health score into dashboard web UI (service exists, not surfaced)

### Hiring (Active)
- [ ] Finalize trial day candidates: Brandi Beam (comp + background check decision with Chris)
- [ ] Backups if Brandi doesn't clear: Shakita Baker, Zion (both at 17.5/25)
- [ ] Sherri Martinez (16.5/25) — hold as third backup

---

## ACTIVE: Tier tester accounts (`scripts/seed_tiers.py`)

> Goal: see the Free/Pro/Estate difference in a running app. `mlim@fusen.world` is already
> Estate (account `belle`), so only Free and Pro need seeding.
> Target: local `mihomes_dev` only — the tier code exists **only** in this worktree, and the
> VM holds real estate data that fake tenants must not touch.

- [x] **Seed script** — `scripts/seed_tiers.py`, same `mihomes_dev` guard as `dev_setup.py`
- [x] **Free tester** — `free@test.local`, plan `free`, 1 property (its cap — 2 would show an unreachable state)
- [x] **Pro tester** — `pro@test.local`, plan `pro`, 3 properties, vendors + work orders
- [x] **`subscription_status="active"` on both** — `limits_for` collapses `canceled`/`unpaid`/`incomplete` to Free, so a Pro row without this is Free wearing a Pro label
- [x] **Own user row per account** — `uq_membership_one_owner` is a partial unique index; reusing `mlim@` would violate it
- [x] **Password identity** — via `create_password_user`, so the form login works, not just the cookie
- [x] **Session with `current_account_id`** — without it every route 403s ("No account selected")
- [x] **Verify gates differ via `can()`**, not by reading the table:
  - [x] Free denied `vendor.rate` + `work_order.schedule` → `upgrade_target="pro"`
  - [x] Pro allowed both, denied `audit.export` + `predictive_maintenance` → `upgrade_target="estate"`
  - [x] Free `property.add` refused at 1 home; Pro allowed to 5
- [x] **Record lesson** — branch-scoped search concluded a feature did not exist

### Review (2026-09-17)

Verified over real HTTP, served as the unprivileged `mihomes_dev_app` role so RLS is actually
enforced (as `postgres` it is silently bypassed, and the check would prove nothing):

- both testers sign in with **email + password** (303 → `/`, dashboard 200)
- each sees **only its own** properties/vendors — Free: Oak Street House; Pro: Harbour View,
  Lakeside Cabin, Dune Cottage, Kestrel Plumbing, Birchwood Landscaping
- gates differ exactly as `PRICING` §3.1 writes them, and every denial names the plan that
  would allow it (Free → pro for `vendor.rate`/`work_order.schedule`; both → estate for
  `audit.export`/`maintenance.predict`/`report.weekly_ai`)
- 62 entitlement/billing tests pass; no source file changed, the script is additive

**Found while testing:** pushing the Free tester past its 1-home cap converts it to
Pro-on-trial (`maybe_start_trial`, §4.2 by design) with nothing on screen to say so — hence
`--reset`, which clears the trial *and* removes homes past the cap (restoring the plan alone
would leave a Free account holding 2 homes, a state the product cannot itself produce).
New provisioning step: `mihomes_dev` had no non-superuser role, so the server refused to start
(N5). Created `mihomes_dev_app`, granted per `0002_rls`'s documented recipe.

**Two caveats worth knowing before looking at these accounts:**

- **`localhost:5000` is currently an SSH tunnel to the VM** (`ssh -L 5000:localhost:5000
  millena@evo-dev`), so the browser there is showing VM data — these testers are laptop-only
  and are *not* on the VM. The tunnel has to be closed before `mihomes-dev` can bind 5000.
  The script prints the exact env vars and says this.
- **The upgrade prompts are not surfaced in the UI yet.** Every denial carries the right
  `upgrade_target` at the service layer, but `/properties/new` renders no hint and `/billing`
  does not show which plan the account is on — SPEC-004's known §4.3 gap ("working mechanism
  but no UI"). The tier *difference* is visible as data (1 vs 3 homes, vendors only on Pro);
  the *paywall* is provable via `can()` rather than on screen.

**Caveat to report:** every limit in `limits.py` is marked `PLACEHOLDER` except Free's
1 home / 3 seats (SPEC-004 O1, founder's call, blocks-ship). The *gating* is real; the
numbers are not final.

---

## Known Issues / Tech Debt
- `tasks/todo.md` was stale for months (fixed 2026-05-14)
- WhatsApp bridge pairing blocked since wacli-integration branch
- HA integration not documented in PRD (§10 phases)
- `src/mihomes/api/` (REST API layer) is untracked — should be committed with web UI
- No pre-commit hook; code review is the only quality gate
