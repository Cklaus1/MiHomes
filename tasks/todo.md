# MiHomes — Project State & Next Actions

## ACTIVE: Omnichannel gateway — retire telegram-bot, then Phase 0 → Telegram → WhatsApp → SMS/email (2026-10-22)

Order agreed with the user: (1) reconcile + retire `telegram-bot`, (2) Phase 0 shared seam,
(3) Telegram as the end-to-end reference channel on webhooks, (4) WhatsApp Cloud API with Meta
in parallel, (5) Twilio SMS then email as further adapters. Design: `docs/product/OMNICHANNEL_GATEWAY_PRD.md`
+ SPEC-006 (shipped 25/25; U1–U9 open — see `tasks/build-loop-spec006-report.md`).

### Step 1 — reconcile `telegram-bot` (branch `worktree-retire-telegram-bot`) — U1
- [x] Diff: 18 non-merge commits, none patch-equivalent in the harness
- [x] Already superseded on harness (not ported): PRDs + PRD review (harness copies repaired), `telegram stop`,
      cross-platform PID check, watchdog zombie fix (harness keeps opt-in WhatsApp supervision on purpose),
      documents upload (`a7e2bd2`), sidebar "Home Management", new-member-join ignore, busy_timeout DB-lock fix
- [x] Ported: room-level book counts (`e4c2e75` `ea02c64` `687f980`) + follow-up fix (property-scoped by-room
      breakdown, "No room assigned" instead of `space None`) + tests
- [x] Ported: worktree workflow rules in CLAUDE.md / lessons.md (`012178c` `222bc4a`)
- [x] Ported: Generate Transactions on every budget tab (`78853af`)
- [x] Rebuilt, not cherry-picked: staff/vendor Documents tab (`6d077ae` wrote to public `/static/uploads`)
- [ ] Deferred to backlog: floating AI chat widget (`b095a67`) — harness dashboard drifted (onboarding card,
      SPEC-009); re-derive from the archive tag, check AI endpoint + tier gating
- [ ] NOT ported: Jen candidate notes (`701b869` `e7b1a19`) — HR notes; repo is PUBLIC
- [ ] User: archive tag, delete local + remote `telegram-bot`, move main checkout off it, confirm no poller
      runs from old code on evo-dev

### Step 2 — Phase 0: one adapter seam, one message type (plan — awaiting user check-in)
- [ ] `gateways/message.py`: `InternalMessage` TypedDict for the canonical envelope the code already emits
      (`webhook.py:158-191`, camelCase keys) + a `channel` key. Fix PRD §7 to match the code, not the reverse.
- [ ] `ChannelAdapter` Protocol (`name`, `normalize(raw) -> list[InternalMessage]`, `send(target, text)`);
      `GatewayAdapter` becomes the Telegram/WhatsApp-Baileys/WhatsApp-Cloud implementations of it
- [ ] One shared entry point `review_common.handle_inbound(session, messages, adapter, property_slug)`;
      the two `process_and_respond` copies become thin callers
- [ ] BUG: `web/routes/gateways.py:221` `_reply` builds `TelegramClient()` with no token → TypeError swallowed →
      unlinked senders never get LINK_PROMPT / LINK_SUCCESS. Tests miss it (fake takes `*a, **k`). Needs a
      platform-level token (`TELEGRAM_BOT_TOKEN` env) since the unscoped path can't read tenant config (U6)
- [ ] BUG: `review_common.py:372` `ai_response` rolls back the whole batch on AI failure but reports `logged: 1`
      → SAVEPOINT per item
- [ ] Contract test: every normalizer returns the same keys (extend A19 to the Protocol)

### Step 3 — Telegram end to end on evo-dev (needs user decisions + deploy)
- [ ] DECISION U3: public ingress. evo-dev is Tailscale **Serve** (tailnet-only) — Telegram can't reach it.
      Option: `tailscale funnel` scoped to `/webhooks/telegram` only
- [ ] `mihomes telegram set-webhook | delete-webhook | webhook-info` (TELEGRAM_PRD §152; nothing calls setWebhook)
- [ ] Group routing: webhook passes `chat_account_id` from `telegram.chat_links` so group senders aren't AmbiguousSender
- [ ] Deploy (user runs `!` commands), set `TELEGRAM_WEBHOOK_SECRET` + token, link Belle group, `/link` a test user,
      send one fresh message → logged once, reply received; no poller running (getUpdates blocks webhooks)

### Step 4 — WhatsApp Cloud API (user-only, start now)
- [ ] Meta Business verification, WABA, phone number, permanent system-user token, webhook verify token
- [ ] DECISION U2: Cloud API group support — Belle is a group; if groups unavailable, choose a routing key

### Step 5 — SMS (Twilio) then email
- [ ] Write SPEC-007 from `TWILIO_PRD.md` against the Phase 0 Protocol; then inbound email (no code yet)

## ACTIVE: Playbooks are documents with tick boxes, not task templates (2026-10-22)

> User: "these turn into tasks? I think we need to remove that. The playbook isn't like the task
> templates … have the checkbox options available to tick off when user clicks it … the option to
> reset it". Decisions: ticks **shared by the whole household** (one live checklist per playbook,
> shows who/when) · **Reset = untick everything** · anyone who can open a playbook can tick; only
> editors (owner/admin/granted staff) can Reset.

- [ ] Remove Run: panel + route + `run_playbook` + CLI `run` + stagger/`day_offset` + tests/copy that
      say "become tasks" (editor help, list empty state, `docs/millena-howto.md`).
- [ ] `playbook_checks` table (tenant-owned, RLS): playbook_id, item_key, checked_by, checked_at,
      UNIQUE(playbook_id, item_key); migration 0023; registry/isolation counts → 53.
- [ ] Item key = hash of the step text + occurrence (duplicates OK; reordering keeps ticks; editing a
      step's text drops its tick). Editing a playbook prunes ticks for steps that no longer exist.
- [ ] Detail page: full-width document, each `- [ ]` is a real checkbox → `POST /playbooks/{slug}/check`
      (htmx, swaps just that line), shows "Name · date", progress "N of M", Reset (editors) with confirm.
- [ ] Preview / template pages stay static (no ticking).
- [ ] Tests (service, routes, isolation, role rules, key stability) + real-browser click-through.

## ACTIVE: Editable, per-account playbooks + template gallery (2026-10-21)

> User: playbooks are "set in stone" with no edit button; they should be made by the admin or
> whoever the admin gives access; the existing ones are strictly for mlim@fusen.world's account;
> new users get templates. Decisions (user, 2026-10-21): edit = owner + admins + **chosen staff**
> (per-person toggle) · templates = **gallery** (new accounts start empty, one-click copy) ·
> template content = **new generic versions** (not copies of Belle's).

Root problem: playbooks are 7 markdown files in `knowledge/playbooks/`, read by
`services/playbook.py` for **every account** — a cross-tenant leak. The repo is **public on
GitHub**, so those files (and all of `knowledge/`) are also world-readable.

Branch: `worktree-editable-playbooks` off `worktree-spec-build-harness` (c26b935).

### Data + permissions
- [x] Model `Playbook(Base, TenantOwned, SlugMixin, TimestampMixin)`: name, slug (unique per
      account), kind (one-off / recurring), audience (who runs it, free text), estimated_time,
      content (markdown). Registered in `TENANT_TABLES`, `_entity_classes` as ACCOUNT_SHARED
      (staff read all, like Template); registry/isolation test counts bumped.
- [x] `memberships.can_edit_playbooks` boolean, default false.
- [x] Migration `0022_playbooks`: table + RLS policy + triggers (0020 recipe) + the column.
- [x] Matrix: `playbook.manage` split from the task row (precedent `automation.manage`):
      owner ALLOW, admin ALLOW, staff ALLOW with rule "only with can_edit_playbooks", enforced
      in the service so no route can skip it. Viewing/running stays `task.manage`.

### UI
- [x] Playbooks list: New playbook · Start from a template · Edit/Delete for permitted users.
- [x] Editor (create/edit): name, kind, audience, estimated time, content with checklist help
      (`- [ ] step`) and a Preview; Delete with confirm. Run keeps working off the checklist.
- [x] Template gallery `/playbooks/templates`: generic starter playbooks shipped in the app
      (package data, read-only, not tenant rows); "Use this template" copies into the account.
- [x] Settings → Members: "Can edit playbooks" toggle per staff member (owner/admin only;
      audited). Admins/owners always can.
- [x] **Security:** `_md_to_html` never HTML-escapes and renders `| safe` — stored XSS once
      content is user-editable. Escape first, then apply formatting.

### Content + migration of Belle's playbooks
- [x] Write 7 generic templates: housekeeping standards, daily operations, emergency
      response, hiring, onboarding a new hire, offboarding, household communication.
- [x] `scripts/import_playbooks.py --account <slug> --dir <path>`: idempotent import of
      markdown files into one account (sets the tenant GUC; skips existing slugs).
- [ ] Run it for `belle` on laptop dev (done, as mihomes_dev_app: 7 imported, re-run 0) + evo-dev; verify other accounts see 0.
- [x] Remove `knowledge/playbooks/*.md` from the repo after import (content lives in the DB).
      Git **history** still holds them — purging history is a separate, destructive decision.
- [x] CLI `mihomes playbook` reads the DB (account context) instead of files.

### Verify + ship
- [x] Tests: cross-account isolation; staff without toggle → 403 on edit, with toggle → ok;
      admin always; XSS escaped; gallery copy; import idempotent; matrix/registry tests.
- [x] Full suite diffed by test id vs baseline; live walk (owner, staff with/without toggle).
- [ ] Merge `--no-ff`, push; deploy = backup DB → `alembic upgrade head` → import → restart.

### Review (build)
- Commits 44e6cae (feature), 85d554b (editor insert fix), 25ecd4e (0022 created_at NOT NULL +
  slug count), plus LF CSS stamps. New tests: tests/integration/test_playbooks_web.py (8),
  tests/unit/test_playbook.py rewritten (DB-backed; parser/search tests kept).
- Full suite vs baseline by test id: no regressions after 25ecd4e (the run before it caught the
  two now-fixed failures). `test_isolation::test_registry_covers_every_tenant_table`, red since
  0020, now passes.
- Migration 0022 verified up → down → up on a scratch DB (RLS enabled + forced).
- Live walk (laptop, Pro tester): no Belle playbooks visible, gallery 7, copy → editor →
  preview → save → detail with Edit. Found + fixed: "+ Step" inserted above the title.
- Communication and Emergency (Belle's) have 0 steps — reference prose, as before.
- Still exposed in the public repo (user chose: list only): knowledge/staff/ (job descriptions,
  candidate notes), knowledge/properties/; and all of it in git history.

## DONE (merge pending): UI/UX fix-all from the 2026-10-21 audit

> User: "go through mihomes app and see what improvements can be made to make the ui/ux better",
> then "Let's fix all of them". Decisions (user, 2026-10-21): `/team` → redirect to Settings →
> Members · Pro-only controls on Free → shown disabled with a Pro badge + upgrade choice ·
> Settings gets a Danger zone (export, sign out everywhere, delete with typed confirm).

Branch: `worktree-uiux-fixes` off `worktree-spec-build-harness`. One commit per area (built in parallel). Rule: any
fix that would need a migration or new model field stops and goes back to the user.

### P0 — infrastructure (main agent only; everything else builds on it)
- [x] Global HTMX error toast in a static JS file loaded once (body swaps re-run inline scripts)
- [x] 401/403 to HTMX requests → `HX-Redirect` (login / onboarding); JSON contract kept for API
- [x] HTMX 402 → toast with the plan reason + link to plans
- [x] Guard `after-request` modal closes / `this.reset()` on `event.detail.successful`
- [x] Global request indicator + `hx-disabled-elt` default (no double submit)
- [x] One shared modal helper (role=dialog, aria-modal, Escape, focus in/out, labelled close);
      delete the ~10 per-page copies of openModal/closeModal/switchTab
- [x] Success toast for full-body swaps (HX-Trigger from routes, or a flash in context)

### P1 — broken today
- [x] `/team` → 303 to `/settings#members`; onboarding + checklist links updated
- [x] Mobile header: title keeps room at 375px (search collapses to an icon below sm)
- [x] Warranty highlight compares to today (+ "expiring soon" window), not `year < 2027`
- [x] Inline-JS interpolation → `data-*` (templates Run, vendor category filter)
- [x] AI chat: `resp.ok` check, always reset on stream end, keep question on error, Stop button,
      don't clobber partial answer — AI page and dashboard box; dashboard `msg.e` escaped
- [x] AI quota / attachment-limit errors: plain wording (not "AI request failed"), linkable
- [x] Budget modals: stay open + show error on failure

### P2 — daily loop
- [x] Assignee field on task add/edit and work-order edit (columns already exist)
- [x] Filters/view survive actions (tasks, issues, work orders)
- [x] "Create work order" from an issue (prefilled); issue ↔ work-order links go to the record
- [x] Dashboard: rows link to records, open issues shown, quick-add Issue/Task, action lists
      above property cards, "Due this week" not capped at 10, WO "View all" includes assigned
- [x] Work-order lifecycle keeps the modal open between steps
- [x] Task list sorted by urgency (overdue → due date → priority) with a sort control
- [x] Issue/task forms: "Select property…" placeholder, required markers
- [x] Board quick-status control; consistent status chip colours via one macro

### P3 — quick wins
- [x] Favicon · Ctrl+K vs ⌘K by platform · nav labels (Rooms→Assets, Directory→Staff) ·
      distinct nav icons · `aria-current` · 44px nav targets
- [x] Login: Google dev-setup text only in debug; hide broken button
- [x] 402 page: plain headline per action, back to Referer, include upgrade_choice
- [x] "Ack" → "Acknowledge"; alerts empty-state action; hover-only controls visible on touch
      / focus (rooms, notes, alert snooze, AI sessions, calendar day "+")
- [x] Password show/hide toggle; errors with role=alert; input kept after server errors
- [x] "Getting started" dismiss: confirm + bigger target; trial "ends today" wording
- [x] Labels tied to inputs (`for`/`id`) on the main forms; search results as real links,
      query urlencoded
- [x] Wrong asset-type counts under a filter; library/property-detail links to records

### P4 — structural
- [x] Sidebar grouped into sections; Billing + Team reachable
- [x] Onboarding wizard: minimal layout + step indicator + labels
- [x] Settings Danger zone (export · sign out everywhere · delete with typed confirm)
- [x] Pro-only controls on Free: disabled + Pro badge (WO due date, Staff invite role)
- [x] Unified filter bar look; text search on vendors/staff; sortable tables
- [x] Inventory: POST-redirect-GET; edit/delete item
- [x] Documents: search, keep modal + input on upload error
- [x] Calendar: Today button, keyboard-reachable arrows, clickable day, "+N more", mobile list
- [x] Vendor detail: tel:/mailto: links, related work orders/contracts
- [x] `vendors.html` dedupe (own commit, last)

### P5 — known-open + cleanup
- [x] Currency symbol in all "$" form labels
- [x] Delete dead `recurring.html` + orphan partials; regenerate UI_MANUAL_CHECKLIST.md
- [x] Full suite diffed by test ID vs baseline; live walk 375/1440 as Pro and Free
- [ ] Merge `--no-ff` into `worktree-spec-build-harness`, push, clean up (NOT deployed to evo-dev)

### Review
- Built by the main agent (P0 shell: `static/app.js`, `base.html`, alerts, search, notes/docs
  partials, property delete) plus four area agents on disjoint files (A daily workflow, B records,
  C documents/calendar/budget/AI, D account/onboarding/billing). 7+26+17+9+2+1 new tests.
- **Withdrawn audit items:** sidebar "Directory" and "Rooms" are deliberate renames (`c81ea4e`,
  test_web_smoke pins Directory) — kept; search now labels staff results "People".
- **Live bugs found while fixing** (all pre-existing): issue Resolve button and inventory
  price-entry edit were JS syntax errors (unquoted UUIDs in onclick); WO page JS died after any
  save (top-level `let` re-declared on body swap); saving a vendor wiped its properties (digit-only
  id filter vs UUIDs); room delete and property delete 500'd on foreign keys. Room delete now
  unassigns contents; property delete removes its task history/zones/links and **refuses** (plain
  message) while books, inventory, work orders, contracts, insurance, recurring expenses,
  appointments, events or vendor ratings are attached — erasing those is a product call, not made.
- **Gotchas recorded for later work:** htmx fires `HX-Trigger` *before* a body swap, so the toast
  region lives on `<html>`; body swaps re-run inline `<script>`s, so shared behaviour lives in
  `static/app.js` (loaded once from `<head>`); `<main>` must be `relative` or absolutely placed
  `sr-only` labels escape its scroll clip and an anchor jump scrolls the whole shell.
- Suite (Postgres `mihomes_test`): before 86 failed / 3088 passed; after 87 / 3162. Diffed by
  test id: **no test that passed before fails now.** All remaining failures are the baseline set
  — 71 × `test_content_globs_cover_every_template` (uses `PurePath.full_match`, Python 3.13+; this
  venv is 3.12, so every template fails identically — the count moves only with template count),
  billing/Stripe-key, waitlist, oauth stub, pg_baseline, quarterly recurrence, route-declaration
  harness, staff-member-route.
- Live walk (laptop `mihomes_dev`, Pro + Free testers, 375 and 1440): header intact at 375,
  Issues/Tasks/Calendar/Vendors usable at 375, deep link → modal → Escape, inline resolve →
  toast survives body swap, invalid HTMX save → readable error toast, Free WO due date disabled
  with Pro badge, Settings Danger zone, 21 pages 200 with zero JS errors. Applied migration 0021
  to laptop `mihomes_dev` earlier in the session (as postgres) so the dev server would start.
- Not done / follow-ups: U3 full manual 375 walk is still a human task; inventory/books on a
  property block its deletion (decide: cascade or archive?); calendar filter still uses a Filter
  button; failed task-create does not reopen the modal with input; all-properties budget analysis
  is 2 queries per property (belongs in services/financial_report).

## ACTIVE: Onboarding — "Getting started" checklist on the dashboard (2026-10-19)

> User: after signup the wizard showed steps, but after creating a room no further suggested
> steps ever appeared. Wants a better first-run experience (option 2 of 3 offered).

Root cause: the wizard (`/onboarding/`) is one-way. "Add rooms" / "Invite someone" are plain
links out of it that record nothing, creating a room never marks step 4, and nothing ever routes
a member back to `/onboarding/` — so the account sits at `completed_steps=[2,3]` forever, and the
dashboard has no follow-up surface at all.

Branch: `worktree-onboarding-checklist` off `worktree-spec-build-harness` (the live evo-dev code).

- [x] `onboarding_service.checklist(session, account_id)` — read-only, items derived from real
      rows (ground truth, like the mandatory steps): Add your first home (Property) · Add rooms
      (Space) · Create a task (Task) · Add a vendor or staff member (Vendor|Staff) · Upload a
      document (Document) · Invite your team (Invite or >1 membership). Each item: key, title,
      one-line why, href, done.
- [x] `current_step`: step 4 counts as done when a Space exists, step 5 when an Invite exists
      (a row is unambiguous completion; absence stays ambiguous, so `completed_steps` still
      rules there). Un-sticks existing mid-wizard accounts.
- [x] Wizard exits record progress: `POST /onboarding/leave` — "Add rooms" marks step 4 then
      goes to /assets; "Invite someone" finishes the wizard then goes to /team. Destination is
      fixed per step (no `next` param → no open redirect).
- [x] Dismiss: `STEP_CHECKLIST_DISMISSED = 7` sentinel in `completed_steps` + `POST
      /getting-started/dismiss` (no migration; `current_step` ignores 7). Declared
      `property.add`/ACCOUNT, not `Access.SESSION` — the session exemption is pinned small.
- [x] Dashboard: card above the stat row, "Getting started · N of 6", progress bar, each item
      ticks itself; shown only to owner/admin, only when an onboarding_state row exists (i.e.
      accounts that came through signup — existing estates like Belle never see it), not
      dismissed, and not all done. GET never writes (`db.get`, not `get_state`).
- [x] Tests: `tests/integration/test_onboarding_checklist.py` (15) — derivation, step-4/5
      inference, exits, dismiss (owner ok / staff 403), visibility (owner / staff / no-row).
- [x] `build:css` + `stamp`; full suite — see review.
- [x] Live check (laptop `mihomes_dev` as non-superuser `mihomes_dev_app`, so RLS is real):
      signup → account → home → "Add rooms" → /assets; dashboard 1 of 6 → add room → 2 of 6,
      rooms struck through; `/onboarding/` resumes at invite; `/getting-started/dismiss` hides
      it. Second signup: skip rooms → "Invite someone" → /team, `/onboarding/` → `/`, card
      still shows invite open. No tracebacks/500s in either server log.
- Note: on Free, "Invite your team" is completable (3 seats, admin invites allowed) but a
  *staff* invite is refused (`staff_invites_allowed: False`) — the copy mentions staff.
- [ ] Merge into `worktree-spec-build-harness` (--no-ff), push, clean up worktree.

### Review
- Pre-existing failures on the base branch (verified by running them there, unchanged by this
  work): 4 × test_billing_routes, test_full_workflow quarterly recurrence, test_isolation
  registry, test_migration_waitlist, test_oauth_stub, test_pg_baseline up/down,
  test_settings staff member routes, 2 × test_waitlist_service.
- Laptop `mihomes_dev` was 3 migrations behind (0017); upgraded to 0020 as `postgres` so
  `mihomes-dev` could start.
- Not done: AI Advisor checklist item (user chose the 6 core items); seeded demo account
  (option 1) remains a separate follow-up — `services/demo.py` is SQLite-era and untenanted.

## ACTIVE: Tasks — repeating tasks reset in place; one-time tasks archive (2026-10-16)

> User: completing a repeating task must reset it per its frequency without the list growing;
> only one-time tasks stay under Completed until the user removes them. Decisions: late tasks
> keep their rhythm (skip missed dates); a completion log keeps reports/staff stats counting;
> one-time tasks are archived (hidden, kept) with a bulk "Clear completed".

Found while checking: Done counted the next date from the old due date (late task → born
overdue); the Edit form's status=completed never advanced the schedule; every completion of a
repeating task created a new row + schedule. Consumers of COMPLETED tasks: AI estate report
(services/ai/reports.py:238), CLI staff stats (cli/staff.py:369), CLI recent (cli/task.py:159),
CLI report YTD (cli/report.py:61).

- [x] Migration 0020: `task_completions` (id, account_id, task_id → tasks CASCADE, property_id
      copied from the task so it is PROPERTY_SCOPED and filterable for staff, completed_at,
      completed_by_staff_id SET NULL, due_date, notes) + RLS + drift guard; `tasks.archived_at`.
      TENANT_TABLES, model, ENTITY_CLASSES (scoped), models/__init__
- [x] `complete_task`: one-time → COMPLETED as today. Recurring → log a completion, roll the
      SAME task's due_date forward on its rhythm until it is after the estate's today, status back
      to pending, schedule.next_due/last_generated updated, alerts resolved, audit "completed".
      Idempotent: a recurring task already advanced past today, or completed this period, is a
      no-op (Telegram redelivery). Return shape stays `(task, next_or_None)` → (task, task).
- [x] `estate_today()` back in config_service (server is UTC)
- [x] Edit route: status=completed → `complete_task`, not a raw status write
- [x] Archive: `archive_task`, `clear_completed` (one-time COMPLETED only), list views hide
      archived; "Clear completed" button on the board's Completed column + per-task Archive
- [x] Consumers count completions from `task_completions` (+ one-time COMPLETED): AI report,
      CLI staff stats, CLI recent, CLI report YTD
- [x] Data: the 1 existing completed recurring copy — back up, then fold it into a completion
      row and delete the copy (ask first) — user chose this; done 2026-10-16
- [x] Tests (unit for rolling date + idempotency; integration written); CSS on evo-dev; deploy;
      live check on a throwaway free-tester task: Done twice = one advance; list length constant
- [x] (advisor) 0020 index for `TenantOwned.account_id`; deploy order pull→backup→migrate→restart;
      "Done — next due" confirmation; clear gcal_event_id on reset

**Review (2026-10-16):** eb473af, f78fd69, 6c399ce, a9dca3b. evo-dev: pg_dump backup, then
0019→0020; task_completions has RLS+FORCE, 1 policy, 3 drift-guard triggers, 4 indexes;
`alembic check` = no drift. Live (free-tester, throwaway tasks, read by task id): a late daily task
Done → due tomorrow, 1 completion; Done again → unchanged, still 1; task count constant; "Last
done" renders; one-time Done → COMPLETED, Clear → archived, off the list, row kept; Edit
"Completed" + weekly→daily → due tomorrow on DAILY with the confirmation. Default /tasks/ renders.
Unit suite 1341 passed / 70 failed, all pre-existing. The old "Property check" copy folded into
task_completions and deleted (backup pre-fold-copy-20261016-192354.sql).

Known limits: "Last done by" shows the assignee, not who clicked (no clicker identity is passed);
the duplicate guard is 10 minutes, so a gateway redelivery hours later would advance again;
integration tests written but not run (no TEST_DATABASE_URL).

## Tasks — group by frequency (2026-10-15)

> User: "I just want to see the tasks categorized by frequency." A Calendar (agenda) view was
> built first from a misread of "daily, weekly, monthly" and has been reverted (8d64106,
> 7dcec0e, e0f1bb8 reverted together).

- [x] Add a "Frequency" view beside Board / Priority / List, built like the Priority grouped view:
      sections Daily, Weekly, Biweekly, Monthly, Quarterly, Seasonal, Annual, One-time — in
      that order, empty sections hidden, count in each heading
- [x] One-time = no schedule or frequency `once`
- [x] Rows reuse the Priority view's task card (same modal, same markers)
- [x] Tests; CSS rebuild on evo-dev; deploy; check live

**Review (2026-10-15):** d69914a (revert), 89de2b9 (view), plus the stamp commit; deployed to
evo-dev. Live as the default owner: Frequency button present, Calendar gone; 72 tasks grouped
Weekly 21 · Every 2 weeks 4 · Monthly 11 · Quarterly 14 · Seasonal 9 · Annual 6 · One-time 7;
no Daily section (none are daily — see below). Unit suite 1319 passed / 70 failed, all
pre-existing kinds.

**Data finding (still true):** the default account's recurring tasks ("Daily: Bathroom care…",
etc.) are scheduled **WEEKLY** despite "Daily" titles, so they will group under Weekly.

## ACTIVE: Currency setting made real; weather default location removed (2026-10-15)

> User: keep currency so budgets etc. follow the estate's preference; remove weather location —
> a property with an address shows weather, one without shows none (no fallback).

Findings: `currency.default` is read by nothing; 8 models default `currency="USD"`, 7 services
default `currency: str = "USD"`; 8 templates (23 spots) print a literal `$`. The weather
fallback appends "Atlanta, GA" to every address before geocoding and, on a miss, shows Atlanta's
weather for that property.

**Currency**
- [x] Jinja `money(amount, currency=None, decimals=0)` filter: symbol for USD/EUR/GBP/CAD/AUD/
      CHF/JPY/MXN, code suffix otherwise; falls back to the estate's `currency.default`
- [x] Replace the 23 hard-coded `$` in assets, budget, contracts, inventory, recurring,
      work_orders, partials/budget_report, partials/transaction_list (27 found in the end,
      incl. 4 `'$' + …` forms and the budget chart axis)
- [x] New records take the estate's currency: services' `currency` param defaults to `None` →
      `get_config(session, "currency.default")`. Existing rows keep their stored currency
      (no data migration — changing the setting doesn't convert old amounts)
- [x] Settings page note: "applies to new budgets and costs; existing amounts keep their currency"

**Weather**
- [x] Drop `weather.default_location` from DEFAULTS, USER_SETTING_KEYS and the Preferences form
- [x] `get_forecast_for_property`: geocode the property's address only; no address or no match →
      None. No appended city hint, no Atlanta fallback
- [x] Dashboard widget: omit properties with no forecast (no "❓" card); widget hidden when none
- [x] Widget grouping: ZIP-less properties no longer pooled into one "—" group (found live)
- [x] Tests: weather (no fallback, no city suffix), money filter, service currency default,
      user settings; CSS rebuild on evo-dev; deploy; check dashboard + settings live

**Review (2026-10-15):** commits 9d2f395, 99694a8, e056265; deployed to evo-dev. Live as
free-tester: /budget/ shows `$0` under USD and `£0` under GBP (setting restored to USD after);
no hard-coded `$` on any money page; Settings has no weather field; junk-address test properties
show no weather. Unit suite: 1312 passed, 71 failed — the same pre-existing 69 + 2.

**OPEN — stale cached coordinates (user: leave data alone, 2026-10-15).** The old fallback wrote
wrong lat/lon that the new code still reuses: Oak Street House (Portland OR) and Harbour View
(Newport RI) are cached at Atlanta; Builders/Founders at downtown Atlanta; Creators near its
spot. Fix when wanted: `UPDATE properties SET latitude=NULL, longitude=NULL WHERE …` for those
rows — Founders and Creators then show no weather until their addresses geocode.

## ACTIVE: Settings page — user preferences, no backend config (2026-10-15)

> Branch `worktree-spec-build-harness`. Decisions (user, 2026-10-15): Telegram self-linking via
> one-time code; WhatsApp status only; estate-wide time zone; notification prefs saved now,
> wired to sending later (labelled as such on the page).

Problem: the "Configuration" section is a raw key/value editor over every stored config row
(`ai.provider`, `ai.max_context_tokens`, Telegram poll state…) and `POST /settings` writes any key
with no allowlist — an owner can switch the AI backend or invent keys.

- [x] `config_service`: `USER_SETTINGS` allowlist (key → label, choices/validator, default):
      `currency.default`, `locale.timezone` (new, IANA, default `America/New_York`),
      `weather.default_location`, `notifications.channel` (none/telegram/email),
      `notifications.events` (multi: issues, work orders, tasks due, budget), 
      `notifications.quiet_start` / `quiet_end` (HH:MM, optional)
- [x] `POST /settings`: accept only allowlisted keys, validate each value; anything else → 400.
      AI / retention / telegram.* / calendar.* stay backend-only (CLI `mihomes config set`)
- [x] Template: replace the key/value table + free-text form with labelled fields grouped as
      **Preferences** (currency, time zone, weather location) and **Notifications** (with a
      "saved, not sent yet" note). Drop the raw config table entirely
- [x] **Messenger connections** section (every role, own membership):
      Telegram — linked/not linked for me (`telegram_links`), "Get a link code" →
      `issue_link_token` → show the code once + "send `/link CODE` to the bot", 15-min expiry;
      unlink own Telegram. WhatsApp — "not available yet" status line
- [x] Access: staff can't load /settings today (member.manage). Messenger + profile must work for
      staff → either open GET /settings to all roles with owner/admin-only sections gated in the
      template, or a separate /settings/connections route. Check authz table before choosing
      → chose /settings/connections (gateway.link_self); GET /settings redirects staff there
- [x] Tests: allowlist accept/reject (incl. `ai.provider` → 400), each validator, page never
      shows ai.* keys, link-code issue/display/expiry, staff can link their own Telegram,
      staff cannot change estate preferences; update test_settings.py cases that POST
      arbitrary keys
- [x] `npm run build:css && npm run stamp`; unit suite (70 pre-existing UI-build/route failures
      on 6f730e3 — compare against that baseline, don't count them)
- [~] Deploy to evo-dev; check page as owner + staff; issue a real code and `/link` it in Telegram
      (owner checked live; no staff member exists on evo-dev; no Telegram bot runs there, so
      `/link` was not exercised end to end)

**Review (2026-10-15):** commits 4d95e30, ef59c96, c12b6f5; deployed to evo-dev. Live as the
free-tester owner: /settings renders Preferences, Notifications, Messenger connections, Members,
Profile; no ai.* / retention.* keys on the page; POST `ai.provider=claude` → 400 "ai.provider can't
be changed from the settings page." and the provider stays nim; a link code is shown once.
63 new unit tests pass. Unit suite 71 failed / 1296 passed, every failure a pre-existing kind
(69 `content_globs`, one per template; 2 `route_declarations`). NOT run: integration suite (no
TEST_DATABASE_URL), a staff render (no staff on evo-dev), a real `/link` in Telegram (no bot).

## ACTIVE: Billing page redesign (2026-10-13)

> Mockup: https://claude.ai/artifact/PuLbfwYuucmvpB84Vy9wbf · branch `worktree-spec-build-harness`

- [x] Prices on cards, read from Stripe by price id (process cache, fail soft → no price shown)
- [x] `accounts.billing_interval` (migration 0019), written only by `apply_subscription_state`
- [x] One Monthly/Yearly switch; one form per card whose hidden `interval` the switch rewrites
- [x] Plain-language summary box: plan · price · renews; past_due banner; card actions moved here
- [x] "Switch to yearly" prompt for monthly subscribers (POST /billing/change, same plan, annual)
- [x] "Everything in X, plus:" feature lists; "Most popular" on Pro; Pro first on phones
- [x] Keep every existing state + testid (cancel-pending undo, trial, non-Stripe Estate→Pro)
- [x] `npm run build:css && npm run stamp`; tests; deploy + reconcile on evo-dev


**Review (2026-10-13):** commit 2cb1709, deployed to evo-dev after a pg_dump backup; migration
0019 applied; reconcile set the tester to monthly; server fetches all 4 Stripe prices. 1290 unit
tests pass. NOT run: integration suite (no TEST_DATABASE_URL anywhere).

---

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

---

## Upgrade prompt instead of the silent trial (2026-09-23)

**Problem.** A Free account adding a 2nd home silently started a 14-day Pro trial
(`_check_home_entitlement` → `maybe_start_trial`) and let the add through — nothing on screen.
`PRICING` §4.1 specifies a modal: *"Adding another home is a Pro feature. Start your 14-day Pro
trial."* with a one-click trial start. Also: the add form is `hx-post`, and htmx 2 does not swap
4xx responses, so a refused POST did nothing visible at all.

- [x] Gate no longer starts a trial: `_check_home_entitlement` just raises `EntitlementError`
- [x] `POST /billing/trial` — explicit trial start via `maybe_start_trial` (one per account, never
      for a paying customer), owner-only, redirects to a safe `next`
- [x] Pure helper `home_upgrade_prompt(session)` → None when allowed, else reason/target/trial flag
- [x] `/properties` Add button opens the popup at the cap: **Start 14-day Pro trial** /
      **See plans** / **Stay on Free**; `/properties/new` shows the same choice, no form (no-JS path)
- [x] Property form → plain `method="post"` so a refusal renders the paywall page
- [x] Paywall page (402) offers the trial button when one is available (staff invite path too)
- [x] Trial banner, lazy-loaded from `GET /trial-status` (`hx-trigger="load"`) — cannot hold
      another request's transaction open, which is what hung pages last time (6628d39)
- [x] Tests on the non-superuser `rls_app` path; mutation-check by restoring the silent trial
- [ ] Push; restart evo-dev; verify on the Free tester without spending its trial

**Result.** 336 affected tests pass; full suite 2893 passed, with 4 failures that are not this
change (2 landing-DB isolation tests fail on the base commit c650baa; 2 waitlist tests pass alone
and fail only in a full run — order-dependent). Mutation check: restoring the silent trial makes
`test_a_refused_add_does_not_start_the_trial` and `test_the_gate_refuses_and_does_not_start_the_trial`
fail.

Found on the way: the exception handler runs after the request's authz context is reset, so it
cannot read the role — `is_owner` is captured on `EntitlementError` at the raise site. The banner
is at `/trial-status`, not under `/billing` (every `/billing` route is owner-only, held by a test).

**Deferred (deviation):** the 4th-seat trigger stays an inline "no seats left" error — making it a
paywall touches `accept_invite`'s `FOR UPDATE` re-check.

---

## Billing page reflects the current plan (2026-09-23)

**Problem.** `/billing` always showed "Upgrade to Pro" and "Upgrade to Estate": a Pro account was
offered its own plan, there was no Free option for Pro/Estate, and nothing said what a plan
includes. It also branched on `stripe_customer_id`, which `start_checkout` commits before the user
reaches Stripe — an abandoned checkout left only "Manage billing", with no way to upgrade again.

- [x] Three plan cards (Free / Pro / Estate) with bullets built from `PLAN_LIMITS`
      (`services/billing/plans.py`), current plan highlighted
- [x] Current plan = `effective_plan()` (extracted from `limits_for`), so a canceled Pro reads Free
- [x] Paying (`stripe_subscription_id`): every change incl. "Cancel subscription" → Stripe portal,
      never a checkout (would bill twice)
- [x] Not paying: checkout only for plans above the current one; trial's Free card says when it
      returns to Free; no button to drop to Free (no subscription; `free` not sellable, D4)
- [x] Tests: unit matrix over every account state + one HTTP page test on `rls_app`
- [ ] Deploy; read /billing as the Pro and Free testers

**Not shown:** prices. The doc's $20/$60 are PLACEHOLDER and the charged amount is Stripe's
`STRIPE_PRICE_*` — a shown price that disagrees with checkout is worse than none.

---

## Cancel, deactivate, delete, 3-month rule (2026-09-23)

Decisions (user, 2026-09-23): cancel → Free **at end of paid period** (immediate when not paying
through Stripe); delete → **30-day undo window**, then permanent; 3-month auto-delete applies to
**deactivated accounts only**; owner's delete/deactivate takes **the whole household**, and
non-owners get "Leave household" / "Delete my profile".

Four slices, each committed + deployed on its own (a `pg_dump` before every migrating deploy):

**Slice 1 — Cancel / downgrade to Free** (owner only)
- [x] Non-Stripe account: immediate drop to Free (same field set as `_expire_trial`)
- [x] Paying account: `provider.cancel(at_period_end=True)`; persist `cancel_at_period_end`
      (migration 0018); page says "Pro until X, then Free" + **Undo** (`resume`)
- [x] Confirm step lists consequences ("2 homes become read-only; nothing is deleted")
- [x] Update `plan_cards` + its tests (Free card now has a button)
- [x] Found on the way: "paying" was the stored subscription id, never cleared when Stripe ends a
      subscription — now `has_live_subscription` (id + a status Stripe can still modify)

**Slice 2 — Deactivate / reactivate** (owner only)
- [ ] `accounts.deactivated_at`; deactivating revokes every member's sessions and cancels billing
- [ ] Enforced at sign-in, not per request (no account read in `enforce_declared_action` — the
      async path that just deadlocked); owner sees "Reactivate", others "deactivated by owner"
- [ ] Jobs skip deactivated / deletion-pending accounts

**Slice 3 — Delete** (owner only; on the existing `request_deletion`/`purge`)
- [ ] Settings "Delete account": download-my-data first, typed confirmation, locks like
      deactivation; restore during the 30 days = `cancel_deletion`
- [ ] `mihomes jobs purge-deletions` in `SCHEDULE`; purge also scrubs the account row, deletes
      users whose only membership it was, deletes the Stripe customer
- [ ] Purge tested as the app role; mutation-check it refuses before `purge_after`

**Slice 4 — 3-month rule + non-owners**
- [ ] `purge-deletions` also deletes accounts deactivated > 90 days (`last_login_at` is never
      written; reactivating = signing in, so `deactivated_at` is the honest clock)
- [ ] Non-owners: "Leave household", "Delete my profile"

**Blocker to report:** no scheduler on evo-dev — purges and the 90-day sweep never run by
themselves until Chris schedules `mihomes jobs purge-deletions` (or someone runs it). Backups
outlive deletion.

## Google OAuth sign-up / sign-in (2026-10-14)

Flow code already existed (SPEC-002 G12); this is configuration plus two gaps.
- [x] Refuse Google sign-in onto a password-only row (`PasswordAccountExists`) — mirror of
      40b0600; it previously forked a second, empty identity. Refuse, not link (pre-hijack)
- [x] "Sign up with Google" on `/signup`, shown only when credentials are set
- [x] Tests: unit refusal, dual-credential user still signs in, callback → `/login?error=`, button gating
- [x] `docs/deploy/GOOGLE-OAUTH.md` — console steps, env vars, troubleshooting
- [x] User: create Web OAuth client, redirect `https://evo-dev.tail6765ef.ts.net/auth/google/callback`, add test users
- [x] evo-dev: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `LANDING_BASE_URL` in web.env; deploy + restart
- [x] Live proof (2026-10-19): new account created via Google sign-up on the Tailscale URL. Existing-user path not testable — mlim@fusen.world is not a Google account
- Open: SPEC-010 U2 — link Google to an existing password account from signed-in Settings

## Sign-in methods in Settings (2026-10-19) — approved: + change password, + email alerts, email must match

User decision 2026-10-19 overrides SPEC-010 N7 / resolves U2: one person may hold both a
password and a Google identity. A new "Sign-in methods" section on the personal settings
(reachable by every role, not only owners).

**Security defaults (not optional)**
- Every new form is POST + CSRF-verified (`tokens_match`) — settings forms skip CSRF today
- Fix first: `upsert_user` stops overwriting `email` on rows with a password (it is the login
  handle; rewriting it can 500 on `uq_users_email_password` and undoes a profile email change)

**A. Connect Google** (password user)
- [x] POST `/settings/sign-in/google/connect` → OAuth in "link" mode, intent cookie bound to session user
- [x] Callback branches before `upsert_user`; keeps the current session
- [x] Refuse: sub on another account · already connected · Google email ≠ account email
      (the match proves mailbox control and stops a stolen session linking a backdoor)

**B. Add a password** (Google-only user)
- [x] Requires sign-in within ~10 min (`sessions.created_at`), else "Confirm with Google" re-auth
      that must return the SAME Google sub
- [x] `set_password`; refuse cleanly if an old duplicate password row holds the email

- [x] Change password (current password, throttled, revokes other sessions)
- [x] Security notice email (`sign_in_method_changed`)
- Out of scope (user): disconnect Google, remove password
- [x] Deployed 2ea44ea to evo-dev; live (2026-10-19): Google account added a password and signed in with it. Not yet live-tested: change password, connect Google

**Housekeeping:** `PasswordAccountExists` message → "…then connect Google in Settings";
SPEC-010 U2/N7 note; `GOOGLE-OAUTH.md`; tests for every refusal; build:css + stamp; full
suite vs the 8 known failures; deploy only with user go-ahead
