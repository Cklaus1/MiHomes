# MiHomes Multitenancy Architecture

Purpose: define how MiHomes converts from a single-user local app into a shared-Postgres, per-account multi-tenant SaaS.
Status: Draft — 2026-07-27

---

## 1. Current state (honest baseline)

MiHomes today is **local-first and single-user**. There is no concept of a user,
account, or tenant anywhere in the codebase. The facts, verified against the code:

- **Storage:** one global SQLite database, WAL mode, opened through a single engine
  singleton. See `src/mihomes/db.py`:
  - `get_engine()` lazily builds one process-wide `Engine`.
  - `get_session()` is a context manager yielding an **unscoped** `Session` that
    commits/rolls back/closes. No filter, no tenant, no auth.
  - A `PRAGMA journal_mode=WAL` / `foreign_keys=ON` / `busy_timeout` hook fires on
    every SQLite connect.
- **ORM:** SQLAlchemy 2.0 (`Mapped[...]` / `mapped_column`), Alembic for schema —
  currently **36 revision files** under `alembic/versions/` (grows over time).
- **Domain:** 28 model modules under `src/mihomes/models/` (property, space, task,
  issue, staff, vendor, asset, work_order, contract, consumable, budget,
  recurring_expense, document, note, event, appointment, ai_conversation, alert,
  audit_log, tag, template, zone, book, insurance, staff_pto, vendor_rating,
  ha_entity, configuration), together defining **36 tables** — several modules
  declare child tables (`task_schedules`, `transactions`, `asset_price_entries`,
  `consumable_price_entries`, `guests`, `event_guests`, `template_items`,
  `tag_assignments`). Every one is a subclass of a shared `Base` and every row lives
  in the same physical database.
- **Surfaces:** Typer CLI (`mihomes <entity> <action>`) and a FastAPI + htmx web app
  (24 route modules, 140+ endpoints, under `src/mihomes/web/`). Both call
  `get_session()` directly.

**Why this is a re-platform, not a bolt-on.** Multitenancy is not a feature you add
to a query here and there — it is a global invariant: *every* row of *every*
tenant-owned table must be attributable to exactly one account, and *every* read and
write must be provably scoped to the caller's account. That invariant touches the
engine, the session lifecycle, all 36 tables, all migrations, and both surfaces. It
also requires a database that can enforce isolation at the row level (Postgres RLS),
which SQLite cannot. Treat this as a deliberate migration, sequenced by the phases in
[the product phasing](#8-phasing-recap).

---

## 2. Tenancy model (CANON)

**Shared PostgreSQL, one database, one schema, `account_id` on every tenant-owned
table.** Every query is scoped by the current account. Postgres **Row-Level Security
(RLS)** is a defense-in-depth backstop — it catches a forgotten application filter;
it is *not* the primary mechanism. Application-level scoping is primary.

Rejected alternatives (for the record): database-per-tenant (every tenant database
migrated in lockstep, connection-pool explosion, ops burden) and schema-per-tenant
(migration fan-out, `search_path` juggling). A shared table with `account_id` is the
right fit for many small households/estates.

---

## 3. Target data model

### 3.1 New core identity tables

```
accounts            one row per household / estate (the tenant)
users               one row per human, GLOBAL (not tenant-owned)
memberships         join: user ↔ account, carries the role
```

**`accounts`** — the tenant boundary.

| column               | type          | notes                                             |
|----------------------|---------------|---------------------------------------------------|
| id                   | uuid PK       | tenant key; referenced by every scoped table      |
| slug                 | text unique   | consistent with existing slug convention          |
| name                 | text not null | display name of the household/estate              |
| plan                 | text not null | `free` \| `pro` \| `estate` (see pricing doc)     |
| stripe_customer_id   | text null     | Stripe Billing customer; billing state lives here |
| stripe_subscription_id | text null   | active provider subscription, if any              |
| subscription_status  | text null     | mirror of Stripe subscription state               |
| current_period_end   | timestamptz null | end of the paid period (from webhooks)         |
| created_at           | timestamptz   |                                                   |
| updated_at           | timestamptz   |                                                   |

(`stripe_customer_id` is set once at customer creation; the subscription-state
columns — `plan`, `stripe_subscription_id`, `subscription_status`,
`current_period_end` — are written **only** by the billing webhook handler — see
[`BILLING_AND_EMAIL.md`](BILLING_AND_EMAIL.md) §5–6; webhooks are the source of
truth for entitlement state.)

**`users`** — a person. **Global**, one row per human, keyed off Google `sub`.

| column        | type        | notes                                          |
|---------------|-------------|------------------------------------------------|
| id            | uuid PK     |                                                |
| google_sub    | text unique | OIDC subject; the stable identity key          |
| email         | text        | from Google claims; may change, not the key    |
| name          | text null   |                                                |
| avatar_url    | text null   |                                                |
| created_at    | timestamptz |                                                |
| last_login_at | timestamptz |                                                |

**`memberships`** — user↔account with a role. A user may belong to many accounts.

| column     | type        | notes                                              |
|------------|-------------|----------------------------------------------------|
| id         | uuid PK     |                                                    |
| account_id | uuid FK     | → accounts.id                                      |
| user_id    | uuid FK     | → users.id                                         |
| role       | text        | `owner` \| `admin` \| `staff`                      |
| status     | text        | `active` \| `revoked`                              |
| invited_by | uuid null   | → users.id (who created the membership)            |
| created_at | timestamptz |                                                    |

A membership always references a real `users` row, so it is created **only at invite
acceptance** — a pending invitee may not have a `users` row yet. Pending invitations
therefore live in a separate `invites` table (see [`../product/ONBOARDING_AUTH_RBAC.md`](../product/ONBOARDING_AUTH_RBAC.md) §6),
**not** as an `invited` membership status. This is why `status` has no `invited` value.

Constraints: `UNIQUE (account_id, user_id)`; a **partial unique index** enforcing
exactly one `role = 'owner'` per account (`WHERE role = 'owner' AND status = 'active'`).

**Roles** (CANON): `owner` = billing + full control, one per account, transferable.
`admin` = full operational control, no billing. `staff` = scoped operational access
for invited external help (housekeeper, property manager). RBAC *enforcement* is
Phase 2; this doc only fixes the shape.

### 3.2 The `account_id` rule

**Every one of the 36 existing tables becomes tenant-owned and gains a non-null
`account_id uuid` FK → `accounts.id`, indexed.** This includes child tables
(`task_schedules`, `transactions`, `tag_assignments`, …): `account_id` is
**denormalized onto every table**, not just aggregate roots, because RLS policies are
per-table and cannot cheaply join to a parent. Add a composite FK or trigger check
that a child's `account_id` matches its parent's, so denormalization cannot drift.
Composite indexes lead with `account_id` (e.g. `(account_id, status)`,
`(account_id, slug)`), and slug uniqueness becomes **per-account**:
`UNIQUE (account_id, slug)` rather than global.

### 3.3 Tenant-owned vs. global

| table                     | ownership   | rationale                                                        |
|---------------------------|-------------|------------------------------------------------------------------|
| the 36 domain tables      | per-account | all business data; each gets `account_id` not-null               |
| `users`                   | **global**  | a person exists independent of any account; keyed on Google sub  |
| `accounts`, `memberships` | identity    | define the boundary itself; `memberships` carries `account_id`   |
| `audit_log`               | per-account | security records must be tenant-attributed and tenant-visible    |
| `configuration`           | **per-account** | see below                                                     |

**`configuration` decision — per-account.** Today `configurations` is a global
key/value table (`key` PK, `value`). Its contents are tenant preferences (units,
locale, feature toggles for that household), so it must be scoped: the primary key
becomes composite **`(account_id, key)`** and it gains an `account_id` FK. Any truly
system-wide/deployment setting (not currently present) would live in an environment
variable or a separate `system_settings` table, never in per-account rows — do not
overload one table with both.

### 3.4 ER sketch

```mermaid
erDiagram
    USERS ||--o{ MEMBERSHIPS : "is member via"
    ACCOUNTS ||--o{ MEMBERSHIPS : "has members"
    ACCOUNTS ||--o{ PROPERTY : owns
    ACCOUNTS ||--o{ TASK : owns
    ACCOUNTS ||--o{ AUDIT_LOG : owns
    ACCOUNTS ||--o{ CONFIGURATION : owns
    ACCOUNTS ||--o{ "…32 more domain tables" : owns

    USERS {
        uuid id PK
        text google_sub UK
        text email
    }
    ACCOUNTS {
        uuid id PK
        text slug UK
        text plan
        text stripe_customer_id
    }
    MEMBERSHIPS {
        uuid id PK
        uuid account_id FK
        uuid user_id FK
        text role
        text status
    }
    PROPERTY {
        uuid id PK
        uuid account_id FK
        text slug
    }
```

---

## 4. Tenant scoping strategy

Every query must be filtered by `account_id`. Two layers, application-primary:

### 4.1 Layer 1 — application scoping (primary)

A **request-scoped "current account" context** carries the resolved `account_id` for
the duration of a request, plus a session that auto-applies the filter.

1. **Resolve tenant per request.** Auth middleware turns the Google OIDC session into
   `current_user`, then resolves the active `account_id` (from the URL/account picker)
   and asserts an `active` membership exists. Store both in a `ContextVar`.
2. **Scoped session.** A `TenantSession` (or a `with_loader_criteria` global filter)
   injects `WHERE account_id = :current_account` into every query against a
   tenant-owned model, and stamps `account_id` on every insert.

```python
# sketch — request-scoped tenant context + auto-filter
current_account: ContextVar[UUID] = ContextVar("current_account")

class TenantOwned:                         # mixin on all 36 tenant-owned models
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False, index=True
    )

@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(state):
    # NOT just is_select: with_loader_criteria also applies to ORM-enabled
    # UPDATE/DELETE. Guarding on is_select alone would leave bulk
    # update()/delete() statements unscoped — a cross-tenant write path.
    if (
        (state.is_select or state.is_update or state.is_delete)
        and not state.execution_options.get("skip_tenant")
    ):
        state.statement = state.statement.options(
            with_loader_criteria(
                TenantOwned,
                lambda cls: cls.account_id == current_account.get(),
                include_aliases=True,
            )
        )

@event.listens_for(Session, "before_flush")
def _stamp_tenant_on_insert(session, flush_context, instances):
    for obj in session.new:
        if isinstance(obj, TenantOwned) and obj.account_id is None:
            obj.account_id = current_account.get()   # raises if unset — fail closed
```

Notes on the sketch:

- `current_account.get()` with no default **raises `LookupError`** when the context
  is unset — that is the fail-closed behavior we want (an unauthenticated or
  misconfigured code path cannot run an unscoped query).
- Any use of `skip_tenant` (admin/ops tooling only) must be grepable and
  code-reviewed; it is the equivalent of `sudo`.
- `with_loader_criteria` covers lazy loads, `selectinload`, and joined eagerloads,
  but **not raw Core `text()` queries** — those rely entirely on RLS (§4.2).

### 4.2 Layer 2 — Postgres RLS (backstop)

Enable RLS on every tenant-owned table so the database refuses cross-tenant rows even
if the application forgets a filter:

```sql
ALTER TABLE properties ENABLE ROW LEVEL SECURITY;
ALTER TABLE properties FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON properties
  USING     (account_id = current_setting('app.current_account', true)::uuid)
  WITH CHECK (account_id = current_setting('app.current_account', true)::uuid);
```

The second argument to `current_setting(..., true)` (`missing_ok`) matters: without
it, an unset GUC makes every query **error**; with it, an unset GUC yields `NULL`,
the predicate is never true, and the query returns **zero rows** — fail closed
either way, but zero-rows is the predictable behavior §9 relies on.

Each request sets the GUC transaction-scoped. Note: `SET LOCAL x = :param` does
**not** accept bind parameters in Postgres — use `set_config()`, which is an
ordinary function call and does:

```python
@contextmanager
def get_session(account_id: UUID):
    session = SessionLocal()
    # set_config(..., is_local=true) == SET LOCAL: auto-reset at tx end
    session.execute(
        text("SELECT set_config('app.current_account', :aid, true)"),
        {"aid": str(account_id)},
    )
    ...
```

Because `set_config(..., true)` is transaction-local, it must be re-issued **after
every commit/rollback** if the same session runs multiple transactions in one
request. Hook it on the session's `after_begin` event rather than calling it once at
session open, so a second transaction can never silently run with the GUC unset
(which would return zero rows and look like missing data, or worse, mask a bug).

**The forgotten-filter risk.** The realistic failure is a hand-written query, a raw
`session.execute(text(...))`, or a new model that misses the mixin — any of which
would otherwise read across tenants. RLS is the safety net: even a completely
unscoped `SELECT * FROM properties` returns only the current account's rows, because
the `USING` clause is enforced by Postgres, not by our code. Application scoping keeps
queries efficient and correct; RLS makes a mistake fail closed instead of leaking.

> The app's DB role must **not** be `BYPASSRLS` and must not be the table owner
> (owners bypass RLS unless `FORCE ROW LEVEL SECURITY` is set). Use a dedicated
> non-owner `app` role and `ALTER TABLE ... FORCE ROW LEVEL SECURITY`. Alembic
> migrations run as the owner/migration role, which bypasses these policies — that
> is correct and intended; only the runtime `app` role is constrained.

**Bootstrap exception — `users` and `memberships`.** `users` is global (no
`account_id`), so it carries no tenant RLS. `memberships` *does* carry
`account_id`, but the **account-picker query** ("which accounts does this user
belong to?") must run *before* any account context exists — an
`app.current_account`-keyed policy would return zero rows and lock every user out.
`memberships` therefore needs a second policy keyed on the authenticated user
(e.g. `USING (user_id = current_setting('app.current_user', true)::uuid)`), OR'd
with the tenant policy, with `app.current_user` set by auth middleware the same way
as `app.current_account`. This is the only table with a user-keyed policy; keep it
that way.

---

## 5. SQLite → Postgres migration

### 5.1 Engine change

- Replace the SQLite URL with a Postgres URL (`postgresql+psycopg://…`); drop the
  SQLite `PRAGMA` connect hook (WAL/`foreign_keys` are SQLite-only). Postgres
  enforces FKs natively.
- Move connection pooling to a real pool (see §7). The `_active_url()` /
  `MIHOMES_DEMO` switch stays only for the local CLI mode (§6).

### 5.2 Alembic history — squash, don't drag

Recommendation: **squash the 36 existing revisions into a single baseline** targeting
Postgres, rather than replaying a SQLite-shaped history against a new engine. The
current migrations contain SQLite-isms (batch-mode `ALTER`, `PRAGMA`, SQLite type
affinities) that are noise on Postgres and a replay risk. Because there are no hosted
tenants yet, there is no production history to preserve. Steps:

1. Cut a new baseline migration `0001_pg_baseline` that creates the identity tables
   (`accounts`, `users`, `memberships` + constraints) **and** all 36 domain tables
   with Postgres-native types (see §5.4) and non-null `account_id` columns, slug
   uniqueness as `(account_id, slug)`, and the `configurations` PK as
   `(account_id, key)`. Since there is no hosted data yet, there is nothing to
   backfill — the nullable→backfill→NOT NULL dance (§10.8) applies only to future
   changes against live tenant data, not to this baseline.
2. `0002_rls` — RLS policies + `FORCE ROW LEVEL SECURITY` per tenant table, and the
   non-owner `app` role grants.
3. Archive the old `alembic/versions/*` under a `legacy_sqlite/` folder for reference;
   they no longer run.

**The catch: the local SQLite mode (§6) still needs migrations.** Two options —
(a) keep the legacy SQLite chain alive for local mode and maintain two Alembic
branches (drift risk, double maintenance), or (b) make the squashed baseline
dialect-aware (skip RLS/`jsonb`/`timestamptz` specifics on SQLite) so one chain
serves both engines. Recommendation: **(b)** — one history, small
`if bind.dialect.name == "postgresql"` guards in the two Postgres-only migrations.
Existing local installs upgrade by stamping the new baseline after a verified
schema-equivalence check, or by export/import through the §5.3 path.

### 5.3 Data migration for existing local installs

Each existing local SQLite database becomes **exactly one account** (see §6). A
one-shot importer:

1. Create one `accounts` row + the owner `users`/`memberships` rows.
2. Stream each SQLite table into Postgres **in FK dependency order** (parents before
   children), stamping the new `account_id` on every row.
3. Remap integer autoincrement PKs per the PK decision in §10.1 — if UUIDs win,
   build an old-id→uuid map per table during import and rewrite every FK column
   through it; decide once and apply uniformly.
4. Validate after import: per-table row counts match the source, FK integrity checks
   pass, and a spot-check of slugs/dates round-trips correctly (SQLite stores
   datetimes as text — parse as UTC, §5.4). Run the importer inside one transaction
   so a failed import leaves nothing behind.

### 5.4 SQLite-isms to fix

| SQLite behavior                     | Postgres target                                     |
|-------------------------------------|-----------------------------------------------------|
| `PRAGMA journal_mode/foreign_keys`  | drop; native FKs, `SET LOCAL` GUCs instead          |
| `INTEGER PRIMARY KEY` autoincrement | `uuid` PK (`gen_random_uuid()`) or `bigint identity`|
| `Boolean` stored as 0/1             | native `boolean`                                    |
| dynamic type affinity / `Text` JSON | native `jsonb` for JSON columns                     |
| datetimes as text                   | `timestamptz` (store UTC)                            |
| case-insensitive `LIKE` quirks      | use `ILIKE` / `citext` where matching mattered      |
| batch `ALTER TABLE` (Alembic)       | plain `ALTER TABLE` (Postgres supports it directly) |

---

## 6. The "one local install = one account" bridge

The CLI and local mode do **not** go away. Two deployment modes coexist off the same
codebase and the same models:

| mode                | storage                      | tenant resolution                     | auth              |
|---------------------|------------------------------|---------------------------------------|-------------------|
| **Local** (CLI)     | SQLite file (as today)       | implicit single account (id fixed)    | none (local user) |
| **SaaS** (hosted)   | shared Postgres + RLS        | per-request from OIDC session         | Google OIDC       |

- In **local mode**, the app runs as if there is exactly one account. The
  `account_id` columns still exist; `current_account` is pinned to a single constant
  local account row seeded at `init_db`. Application scoping still runs (same code
  path, one tenant, so it is a cheap no-op in effect); **RLS does not exist on
  SQLite** — local mode has no database backstop, which is acceptable because the
  database contains exactly one tenant. The CLI keeps working unchanged for the
  local single-tenant user.
- Migrating a local user to SaaS = the §5.3 importer: their SQLite DB is uploaded and
  becomes one Postgres account, with the local user promoted to `owner`.
- The engine/session selection (SQLite vs Postgres, tenant pinned vs per-request) is
  driven by config, so most application code is identical across modes. Keep the mode
  fork thin and centralized in `src/mihomes/db.py`.

---

## 7. Session / connection architecture

Move from the global engine + unscoped `get_session()` (today's shape in
`src/mihomes/db.py`) to a **per-request session that knows its tenant**:

1. **Engine/pool.** Keep a process-wide engine, but back it with a real connection
   pool (`pool_size`, `max_overflow`, `pool_pre_ping=True`). The engine is shared; the
   *tenant* is per session, never baked into the engine.
2. **Request lifecycle (SaaS):** middleware resolves `current_user` → active
   `account_id` → sets the `ContextVar` → opens a session → issues
   `set_config('app.current_account', :id, true)` at the start of each transaction
   (§4.2 `after_begin` hook) → hands the scoped session to routes →
   commit/rollback/close, which ends the transaction and **auto-resets** the GUC.
3. **`get_session()` gains a tenant parameter** (or reads the `ContextVar`) and both
   the CLI and web surfaces call the tenant-aware version.

**Pooled-connection leak — the sharp edge.** Connections are reused across requests.
A plain `SET app.current_account = X` **persists on the physical connection** and, if
the next request reuses it without re-setting, that request would run under the
previous tenant's GUC. Two defenses, use both:

- Prefer **transaction-local** setting (`set_config(..., true)`, §4.2) inside the
  request transaction — it is cleared automatically on commit/rollback, including
  SQLAlchemy's implicit rollback when a connection is returned to the pool. Never
  use a bare session-level `SET`.
- Belt-and-braces: a **pool `checkin`/`reset` event** that issues
  `RESET app.current_account` (`DISCARD ALL` also works but drops prepared
  statements and is heavier), so even a code path that mistakenly used session-level
  `SET` cannot carry tenant state to the next checkout. This hook is cheap and makes
  the invariant hold independent of application discipline.
- Combined with `current_setting(..., missing_ok)` semantics (§4.2), a connection
  with a reset GUC yields **zero rows** under RLS, not another tenant's rows — the
  failure mode of a missed re-set is visible breakage, not silent leakage.

**PgBouncer caveat:** if a transaction-pooling proxy (PgBouncer/pgcat in
`transaction` mode) is ever introduced, session-level `SET` becomes actively
dangerous (the server connection is multiplexed across clients between
transactions) and even the checkin hook runs against the *proxy*, not the server
connection. Transaction-local `set_config(..., true)` issued inside each
transaction remains correct under transaction pooling — one more reason it is the
primary mechanism.

---

## 8. Phasing recap

- **Phase 0** — landing + waitlist + Google sign-in (validate demand).
- **Phase 1** — *this doc's core*: Postgres, `accounts`/`users`/`memberships`, tenant
  scoping, auth, RLS.
- **Phase 2** — onboarding + staff invites + roles/RBAC enforcement.
- **Phase 3** — billing/freemium (Stripe) + entitlements.
- **Phase 4** — polish, email lifecycle (Resend), GA launch on **mihomes.ai**.

Entitlement/limit enforcement (Free = 1 home + 3 seats; Pro/Estate expand) is checked
in a central **entitlements service** at the seam where tenancy meets billing — e.g.
membership creation checks the seat limit, property creation checks the home limit,
both reading `account.plan` **and** `account.subscription_status` (a `past_due` or
`canceled` Pro account is not entitled to Pro limits — see the status→behavior table
in [`BILLING_AND_EMAIL.md`](BILLING_AND_EMAIL.md) §5). The price table itself lives in
[`../product/PRICING_AND_PACKAGING.md`](../product/PRICING_AND_PACKAGING.md); do not
duplicate it here. Billing/email specifics (`BillingProvider`, `EmailProvider`,
`account.stripe_customer_id`, subscription state) are out of scope for this doc.

---

## 9. Security & isolation

Cross-tenant data leakage is the **#1 risk** of this entire re-platform. Controls:

- **Defense in depth:** application scoping (§4.1) + Postgres RLS (§4.2). Neither is
  trusted alone.
- **Isolation test (mandatory, CI-gated):** seed accounts A and B; for **every**
  tenant-owned model, assert that a session bound to A can never read, update, or
  delete B's rows — via ORM queries, ORM bulk `update()`/`delete()`, *and* raw
  `session.execute(text(...))` — and can never **insert** a row stamped with B's
  `account_id` (the RLS `WITH CHECK` clause must reject it). Run this test against
  Postgres with RLS enabled, not SQLite — the raw-SQL cases are only defended by
  RLS. Derive the model list from the `TenantOwned` mixin registry so a new model is
  covered automatically. This test is the executable definition of the tenancy
  invariant; it must run on every PR.
- **Membership revocation is immediate:** tenant resolution (§4.1) checks for an
  `active` membership on **every request**, so revoking a membership cuts access on
  the next request — no long-lived per-account token to invalidate.
- **Fail-closed default:** if `current_account` is unset, the scoped session raises
  rather than running unscoped. RLS with an unset GUC returns zero rows, not all rows.
- **Non-privileged DB role:** the app connects as a non-owner role without
  `BYPASSRLS`; tables use `FORCE ROW LEVEL SECURITY`.
- **Connection hygiene:** transaction-local `set_config` + pool `RESET` (§7) so
  tenant state never leaks across pooled requests.
- **Audit:** `audit_log` is per-account; membership/role changes and account switches
  are logged.
- **Global-table discipline:** at launch, `users` is the only cross-account business
  table, and writes to it are limited to auth/identity flows, never general request
  handlers. Additional global tables are introduced only by deliberate design — e.g.
  the Vendor Discovery growth bet adds `GlobalVendor`, its research snapshots, and a
  global moderation audit (see [`../product/VENDOR_DISCOVERY_PRD.md`](../product/VENDOR_DISCOVERY_PRD.md)).
  Any new global table must be justified here, carry no `account_id`, and gate its
  writes behind a dedicated privileged path rather than tenant request handlers.

---

## 10. Open questions / risks

1. **PK strategy:** UUID everywhere vs. keep integer PKs + `account_id`. UUIDs
   simplify import and avoid cross-tenant id guessing but are wider indexes. Decide
   before the baseline migration.
2. **Global filter mechanism:** `with_loader_criteria` event hook vs. a bespoke
   `TenantSession` subclass — validate that the event approach covers relationship
   loads, `selectinload`, and bulk operations without gaps.
3. **Raw SQL surface:** audit every existing `session.execute(text(...))` — these
   bypass the ORM filter and rely entirely on RLS. Known call sites today:
   `src/mihomes/services/archive.py`, `src/mihomes/services/backup.py`,
   `src/mihomes/services/ai/tools.py` (the AI tool layer running SQL is the
   scariest one — it must run under RLS, never as a privileged role).
4. **RLS performance:** `current_setting(...)::uuid` in every policy predicate — verify
   the planner uses the `account_id` index; benchmark on realistic data. Known
   mitigation if it re-evaluates per row: wrap the call as
   `(SELECT current_setting('app.current_account', true)::uuid)` in the policy so it
   is planned as an InitPlan and evaluated once per query.
5. **Local↔SaaS drift:** two modes off one codebase risks the local path silently
   skipping tenant logic. Keep the fork centralized in `db.py` and cover both in tests.
6. **Account switching UX:** a user in multiple accounts needs an explicit active-account
   selector; how is it carried (subdomain? path prefix? session field?) — affects §4.1.
7. **Owner transfer & last-owner deletion:** enforce the one-active-owner invariant on
   transfer and prevent removing the sole owner.
8. **Backfill downtime:** adding non-null `account_id` to large tables — for hosted
   scale, use the nullable→backfill→NOT NULL pattern to avoid long locks.
