# SPEC-002 — Phase 1: Multitenant Foundation

**Phase:** 1 (canon — `../product/SAAS_PRD.md` §10)
**Status:** Ready to build
**Written:** 2026-07-31
**Source PRDs:** `../architecture/MULTITENANCY.md` (primary), `../product/ONBOARDING_AUTH_RBAC.md` §2–3, `../architecture/BILLING_AND_EMAIL.md` §9
**Depends on:** SPEC-001 (Phase 0) — reuses `mihomes.ids.new_id()`, the `EmailProvider` stack, and the provisioned Fly + Postgres

**Goal.** Turn a single-user SQLite app into a multi-tenant Postgres SaaS where a signed-in user
sees only their own account's data — enforced twice, in the application and in the database.

**Exit criteria** (`SAAS_PRD` §10): a user can sign in with Google and see only their own
account's data; the CI isolation test is green.

**The stake.** `MULTITENANCY` §9 names cross-tenant leakage the **#1 risk of the entire
re-platform**. Every design choice below that looks paranoid is paranoid on purpose.

---

## 1. Decisions

### 1.1 Locked

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | **CLI role** | Operator tool, **not** a user interface. **Local SQLite mode is dropped** | Founder decision. The web app already tells users to run CLI commands (`web/routes/ai.py:47`) — unacceptable for a tenant. Deletes the dual-mode `db.py` fork, the local entitlements bypass, the local↔SaaS drift risk (§10 Q5), the §6 bridge, and the dual-dialect Alembic chain |
| D2 | PK strategy | `uuid` PK, UUIDv7, app-side via `mihomes.ids.new_id()`. **No DB-side default** | Resolves `MULTITENANCY` §10 Q1. Shipped in SPEC-001 §4.1 — reused verbatim, not re-derived. `gen_random_uuid()` emits v4 and would destroy v7's index locality |
| D3 | Global tables | `users`, `sessions`, `processed_webhook_events`, `waitlist` — **no `account_id`, no tenant RLS** | Each is read or written **before** account context exists. A naive `app.current_account` policy returns zero rows and breaks login, webhook dedupe, and Phase 0 signup |
| D4 | Ownership | Partial unique index on `memberships` (`role='owner' AND status='active'`). **No `accounts.owner_user_id`** | `PRD_REVIEW` A2. Membership is already the authorization source of truth (`ONBOARDING` §9.4 reloads it every request); a denormalized column would drift from it |
| D5 | home = `properties` | `membership_property_scopes.property_id → properties.id` | `PRD_REVIEW` A4. **No `homes` table exists** and none is created. "Home" stays a UI word |
| D6 | Membership status | `active` \| `revoked` only — **no `invited`** | `MULTITENANCY:118`. Pending invitations live in `invites`; a membership needs a `user_id` that an un-signed-up invitee does not have |
| D7 | Scoping mechanism | `with_loader_criteria` via `do_orm_execute` (application) **+** Postgres RLS (backstop) | §4. Neither trusted alone |
| D8 | GUC transport | Transaction-local `set_config(..., true)` on `after_begin`. **Never** a session-level `SET` | §7 + §11.2. Fly fronts Postgres with transaction-pooling PgBouncer, so a session-level `SET` leaks tenant context across clients. This is a **hosting requirement**, not a style preference |
| D9 | Alembic | Squash 36 revisions → one Postgres-only baseline | §5.2. 8 of the 36 use `batch_alter_table`, a SQLite workaround. With D1 the §5.2 option (a)/(b) dilemma disappears — one chain |
| D10 | Importer | **Keep**, as `mihomes import <sqlite-path>` | Decoupled from launch: the first hosted tenant is a clean signup, the archive imports later into its own account. Reading a SQLite file does not require the *app* to speak SQLite |
| D11 | Storage | `StorageProvider` Protocol + S3 backend + filesystem dev backend | §11.3 — Fly volumes are single-machine local NVMe, so object storage is mandatory. See §7-N6 |
| D12 | Local dev | docker-compose Postgres | D1 removes the zero-setup SQLite path; every dev and CI run now needs a real Postgres |
| D13 | **Postgres: managed** | Fly Managed Postgres (Supabase-backed) or an external managed provider | Founder decision, 2026-07-31. Backups + PITR become the vendor's responsibility. `MULTITENANCY` §11.1 (canon). **Removes `pg_dump` from Step 14 — but not Step 14 itself**, because no database backup covers object storage (§1.3 F1) |
| D14 | Recovery targets | Automated daily backups + PITR; **a restore rehearsed before the first non-founder tenant** | Exact RPO/RTO come from the selected provider's SLA and get recorded in `MULTITENANCY` §11.1. The rehearsal is ours regardless of vendor — an untested restore is not a backup |

### 1.2 `OPEN — needs decision: founder`

**None.** O1 (managed vs. unmanaged Postgres) and O2 (RPO/RTO) closed 2026-07-31 → D13, D14.

Every decision this phase depends on is settled. Items still marked `DEFERRED (Phase N)` in §7
are future scope with their interfaces already fixed, not gates on this work.

### 1.3 Survey findings that shaped this spec

Five things found in the code that **no PRD mentions**. Each drives a step below.

**F1 — `backup.py` and `doctor` break on hosted.** `services/backup.py:20-24` tars `DB_PATH`
(a SQLite file) and `MEDIA_DIR` (a local directory); neither exists on Fly. Worse,
`run_doctor()` at `:58-60` returns early:

```python
if not DB_PATH.exists():
    findings.append({"level": "error", "message": "Database not found"})
    return findings          # every later check silently skipped
```

So `mihomes doctor` would report a **false error** on hosted and skip its integrity checks —
worse than failing loudly. `mihomes backup` is currently the *only* backup mechanism that exists.

D13 (managed Postgres) resolves the database half — the vendor owns backups and PITR, so the
`pg_dump` branch is dropped. It does **not** resolve the media half: no database backup touches
object storage, so `mihomes backup` becomes a media-only command and must say so in its
docstring. → Step 14.

**F2 — `ai/tools.py` interpolates SQL predicates.** Three queries at `tools.py:792,803,814` use
`text(f"""...WHERE {where}...""")`. Values are properly bound (`:search`); the **predicate
string** is not. Not exploitable today (conditions are code-controlled) but one careless edit
from injection, in the layer an LLM drives. And `with_loader_criteria` never touches raw `text()`
(§4.1), so these depend **entirely** on RLS. → Steps 10, 17.

**F3 — the mixins make the 36-table change tractable.** `SlugMixin`
(`models/__init__.py:28-33`, used by 15 models) and the new `TenantOwned` mean the *column*
change is one edit each. But see §7-N1: three further passes over all 36 tables follow, and only
the column is cheap.

**F4 — only 3 of 4 `unique=True` constraints matter.** `SlugMixin.slug`, `ha_entity.py:21`,
`tag.py:13`. Skip `task.py:90` — `task_id` unique on `task_schedules` is a one-to-one FK, not a
tenant concern. `tag.name` globally unique is outright wrong: two households must both be able to
have a "Plumbing" tag. → Step 5.

**F5 — 5 models use polymorphic associations with no FK.** `alert`, `audit_log`, `document`,
`note`, `tag` carry `entity_type` + `entity_id` with **no `ForeignKey`**. §3.2's "composite FK"
drift guard is therefore *impossible* for them — they need a trigger or accept
application-only enforcement. → Step 4.

**F6 — the test suite has no account context.** `tests/conftest.py:21` builds in-memory
**SQLite**; 28 of 33 test files use the `session` fixture. Under tenant scoping every query needs
`current_account` or raises `LookupError`. This phase **migrates the existing suite**, it does not
merely add tests. → Step 15.

---

## 2. Doc-fix prerequisites

| Ref | Fix | File |
|---|---|---|
| **A0** | Entitlements service → Phase 2 (config-only), Phase 3 wires billing in | `product/README.md:60-61`, `MULTITENANCY.md:466` |
| **A1** | Seat = active `memberships` + pending `invites` rows (two tables, not a status) | `PRICING_AND_PACKAGING.md:98` |
| **A2** | Drop `accounts.owner_user_id`; ownership is the partial unique index | `ONBOARDING_AUTH_RBAC.md:35,43` |
| **A3** | Reconcile §3.1 columns: add `accounts.type`, `trial_ends_at`, `trial_used_at`; one name per field (`subscription_status` not `billing_status`; `last_login_at` not `last_login`) | `MULTITENANCY.md:70-101` + `ONBOARDING:34-36`, `PRICING:143` |
| **A4** | home = `properties`; FK is `membership_property_scopes.property_id` | `ONBOARDING_AUTH_RBAC.md:44,51,134` |
| **A5** | Add the 5 Phase-1 tables to §3.1 and the §5.2 baseline list | `MULTITENANCY.md:340-346` |
| **C** | Delete two stale cross-refs that describe already-fixed problems | `ONBOARDING_AUTH_RBAC.md:38`, `VENDOR_DISCOVERY_PRD.md:92` |
| **C1** | "24 route modules" → 23 | `MULTITENANCY.md:32` |
| **§6** | Rewrite the local-mode bridge — D1 drops it | `MULTITENANCY.md:391-412` |

---

## 3. File manifest

### New — identity and tenancy

```
src/mihomes/models/account.py              Account
src/mihomes/models/user.py                 User (GLOBAL)
src/mihomes/models/membership.py           Membership, MembershipPropertyScope
src/mihomes/models/invite.py               Invite
src/mihomes/models/session.py              Session (GLOBAL)
src/mihomes/tenancy/__init__.py            public API: current_account, current_user
src/mihomes/tenancy/context.py             ContextVars + set/reset helpers
src/mihomes/tenancy/session.py             do_orm_execute filter, before_flush stamp, after_begin GUC
```

### New — auth

```
src/mihomes/auth/__init__.py
src/mihomes/auth/oidc.py                   IdentityProvider Protocol + GoogleOIDCProvider
src/mihomes/auth/session_store.py          create/load/revoke server-side sessions
src/mihomes/auth/middleware.py             resolve user + account, set ContextVars
src/mihomes/auth/csrf.py                   double-submit token
src/mihomes/web/routes/auth.py             /auth/google/start, /callback, /signout
```

### New — storage (F8 / D11)

```
src/mihomes/services/storage/__init__.py
src/mihomes/services/storage/provider.py   StorageProvider Protocol + exceptions + factory
src/mihomes/services/storage/s3_provider.py
src/mihomes/services/storage/fs_provider.py    local dev
```

### New — migration and ops

```
alembic/versions/0001_pg_baseline.py       identity + 36 domain tables + 5 new, Postgres-native
alembic/versions/0002_rls.py               policies, FORCE RLS, app role grants
alembic/legacy_sqlite/                     the 36 archived revisions (reference only, never run)
src/mihomes/services/importer.py           SQLite -> Postgres, one account
src/mihomes/cli/import_cmd.py              mihomes import <sqlite-path>
docker-compose.yml                         local Postgres (D12)
```

### Modified

```
src/mihomes/db.py                  Postgres engine + pool; get_session() takes/reads tenant. Drop PRAGMA hook
src/mihomes/models/__init__.py     add TenantOwned; SlugMixin unique -> per-account
src/mihomes/models/*.py            (36 tables) add TenantOwned + __table_args__
src/mihomes/services/ai/tools.py   rewrite 3 raw-SQL queries onto the ORM (F2)
src/mihomes/services/archive.py    audit remaining text() sites
src/mihomes/services/backup.py     pg_dump/media-sync; doctor loses filesystem assumptions (F1)
src/mihomes/models/document.py     file_path -> opaque storage key
src/mihomes/web/server.py          remove init_db() on startup (see §7-N4)
tests/conftest.py                  Postgres + account-aware fixtures (F6)
pyproject.toml                     add psycopg[binary], authlib, boto3; drop nothing
```

---

## 4. Schemas as code

### 4.1 `TenantOwned` mixin — `src/mihomes/models/__init__.py`

```python
class TenantOwned:
    """Every tenant-owned table carries account_id. 36 tables use this.

    account_id is denormalized onto CHILD tables too (task_schedules, transactions,
    tag_assignments, ...), not just aggregate roots, because RLS policies are
    per-table and cannot cheaply join to a parent (MULTITENANCY 3.2).
    """

    @declared_attr
    def account_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            PGUUID(as_uuid=True),
            ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


class SlugMixin:
    """Human-friendly identifier. Unique PER ACCOUNT, not globally (MULTITENANCY 3.2)."""

    slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # NOTE: uniqueness is enforced per-table via __table_args__:
    #     UniqueConstraint("account_id", "slug", name="uq_<table>_account_slug")
    # It CANNOT live on the mixin column — a mixin cannot see account_id's table.
```

> **Do not** leave `unique=True` on `SlugMixin.slug`. Today it is globally unique
> (`models/__init__.py:32`), which under multitenancy means the second account to create a
> "main-house" property gets an IntegrityError.

### 4.2 Identity tables

Reconciled per A3 — this is the schema of record; `ONBOARDING` §2 and `PRICING` §4.2 defer to it.

```python
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="household")  # A3

    plan: Mapped[str] = mapped_column(String(20), nullable=False, server_default="free")

    # Written ONLY by the billing webhook handler (BILLING 5-6). DEFERRED (Phase 3).
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subscription_status: Mapped[str | None] = mapped_column(String(30), nullable=True)  # A3: NOT billing_status
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # App-managed no-card trial (PRICING 4.2, BILLING:485). DEFERRED (Phase 3) but the
    # columns ship now so Phase 3 needs no migration on a live table.
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), onupdate=func.now())
    # NOTE: no owner_user_id. Ownership is the partial unique index on memberships (D4/A2).


class User(Base):
    """GLOBAL — a person exists independent of any account. No account_id, no tenant RLS."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)  # display only, may change
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # A3


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)     # owner | admin | staff
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    # D6: active | revoked ONLY. No 'invited' — pending invitations live in `invites`.
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())

    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_membership_account_user"),
        # D4: exactly one active owner per account.
        Index("uq_membership_one_owner", "account_id", unique=True,
              postgresql_where=text("role = 'owner' AND status = 'active'")),
    )
```

`Invite`, `MembershipPropertyScope`, and `Session` follow the same shape:

```python
class Invite(Base, TenantOwned):          # tenant-owned
    __tablename__ = "invites"
    # id, email, role, token_hash, expires_at, status, created_by, created_at
    # A pending row CONSUMES A SEAT (PRICING 3.1 as corrected by A1).

class MembershipPropertyScope(Base, TenantOwned):    # A4/D5 — property, not "home"
    __tablename__ = "membership_property_scopes"
    # membership_id FK, property_id FK -> properties.id
    # Staff whitelist. Zero rows = zero properties visible (ONBOARDING 2, fail closed).

class Session(Base):                      # GLOBAL (D3) — no account_id, no tenant RLS
    __tablename__ = "sessions"
    # id, session_id_hash (unique), user_id FK, current_account_id (nullable),
    # created_at, expires_at, last_seen_at
    # Read by auth middleware BEFORE account context exists. A tenant policy here
    # would return zero rows and lock every user out.
```

### 4.3 RLS policies — `0002_rls.py`

**Generate these**, do not hand-write 36 near-identical blocks.

```python
TENANT_TABLES = [...]   # derived from TenantOwned.__subclasses__() at migration authoring time

def upgrade() -> None:
    op.execute("CREATE ROLE app NOLOGIN")   # if absent; the runtime role, NOT the owner

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
              USING      (account_id = (SELECT current_setting('app.current_account', true)::uuid))
              WITH CHECK (account_id = (SELECT current_setting('app.current_account', true)::uuid))
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app")
```

Three details that are load-bearing:

- **`(SELECT current_setting(...))`** — the subquery wrapper makes Postgres plan it as an
  InitPlan, evaluated once per query rather than once per row (§10 Q4).
- **`missing_ok=true`** (the second arg) — an unset GUC yields `NULL`, the predicate is never
  true, and the query returns **zero rows**. Without it every query *errors*. Both fail closed;
  zero-rows is the predictable behavior §9 relies on.
- **`FORCE ROW LEVEL SECURITY`** — table owners bypass RLS otherwise. The app connects as the
  non-owner `app` role; migrations run as the owner, which bypasses policies. That is correct
  and intended.

**The `memberships` bootstrap exception** — the one table with a second, user-keyed policy:

```sql
CREATE POLICY membership_self ON memberships
  USING (user_id = (SELECT current_setting('app.current_user', true)::uuid));
```

The account-picker query ("which accounts does this user belong to?") runs **before** any account
context exists. An `app.current_account`-keyed policy alone would return zero rows and lock every
user out. This is the only table that gets a user-keyed policy — keep it that way (§4.2).

### 4.4 Scoped session — `src/mihomes/tenancy/session.py`

```python
current_account: ContextVar[uuid.UUID] = ContextVar("current_account")
current_user: ContextVar[uuid.UUID] = ContextVar("current_user")


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(state) -> None:
    # NOT just is_select: with_loader_criteria also applies to ORM-enabled UPDATE/DELETE.
    # Guarding on is_select alone leaves bulk update()/delete() unscoped — a cross-tenant
    # WRITE path, which is worse than a read leak.
    if (state.is_select or state.is_update or state.is_delete) \
            and not state.execution_options.get("skip_tenant"):
        state.statement = state.statement.options(
            with_loader_criteria(
                TenantOwned,
                lambda cls: cls.account_id == current_account.get(),
                include_aliases=True,
            )
        )


@event.listens_for(Session, "before_flush")
def _stamp_tenant_on_insert(session, flush_context, instances) -> None:
    for obj in session.new:
        if isinstance(obj, TenantOwned) and obj.account_id is None:
            obj.account_id = current_account.get()   # raises LookupError if unset — fail closed


@event.listens_for(Session, "after_begin")
def _set_tenant_guc(session, transaction, connection) -> None:
    """Transaction-local GUC, re-issued on EVERY transaction (D8).

    set_config(..., is_local=true) == SET LOCAL: auto-reset at transaction end.
    It must be re-issued after every commit/rollback, so this hooks after_begin
    rather than being called once at session open.
    """
    try:
        account_id = current_account.get()
    except LookupError:
        return          # no context: RLS returns zero rows. Fail closed, not open.
    connection.execute(
        text("SELECT set_config('app.current_account', :aid, true)"), {"aid": str(account_id)}
    )
```

---

## 5. Function signatures

```python
# src/mihomes/tenancy/context.py
@contextmanager
def account_context(account_id: uuid.UUID, user_id: uuid.UUID | None = None) -> Iterator[None]:
    """Bind the tenant for a block. Resets ContextVars on exit, exception-safe."""

def require_account() -> uuid.UUID:
    """Current account, or raise LookupError. Never returns None — a nullable
    accessor invites `if account:` checks that silently skip scoping."""


# src/mihomes/db.py  (rewritten)
def get_engine() -> Engine:
    """Process-wide Postgres engine with a real pool (pool_pre_ping=True).
    The engine is shared; the TENANT is per-session, never baked into the engine."""

@contextmanager
def get_session(account_id: uuid.UUID | None = None) -> Iterator[Session]:
    """Tenant-scoped session. Uses account_id if given, else the ContextVar."""


# src/mihomes/auth/oidc.py
class IdentityProvider(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str) -> str: ...
    def exchange_code(self, *, code: str, code_verifier: str) -> IdTokenClaims: ...

def get_identity_provider(name: str = "google") -> IdentityProvider: ...


# src/mihomes/auth/session_store.py
def create_session(session: Session, *, user_id: uuid.UUID,
                   current_account_id: uuid.UUID | None) -> str:
    """Returns the RAW session id (set as a cookie). Only its hash is stored."""

def load_session(session: Session, *, raw_session_id: str) -> SessionRow | None: ...
def revoke_session(session: Session, *, raw_session_id: str) -> None: ...
def revoke_all_for_user(session: Session, *, user_id: uuid.UUID) -> None:
    """'Sign out everywhere' — ships with launch (ONBOARDING 10)."""


# src/mihomes/services/storage/provider.py
class StorageProvider(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def url(self, key: str, *, expires_in: int = 3600) -> str:
        """Presigned read URL. Tenant files are never world-readable."""

def storage_key(account_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    """'{account_id}/{document_id}/{filename}' — tenant-prefixed by construction."""


# src/mihomes/services/importer.py
def import_sqlite(sqlite_path: Path, *, account_name: str, owner_email: str,
                  dry_run: bool = False) -> ImportReport:
    """One SQLite DB -> exactly one new account. See section 6 step 16 for ordering."""
```

---

## 6. Sequenced steps

**Step 1 — identity models.** `Account`, `User`, `Membership`, `MembershipPropertyScope`,
`Invite`, `Session` (§4.2), with the A3 reconciliation. *Verify:* models import; the
one-active-owner partial index rejects a second owner.

> **Steps 2–5 are the largest and riskiest work in the phase.** The `TenantOwned` column is one
> mixin edit; three further passes over all 36 tables follow. See §7-N1.

**Step 2 — `TenantOwned` on 36 tables.** Mixin (§4.1) + 36 class declarations. *Verify:* a script
asserts every non-global model subclasses `TenantOwned` — the same registry the isolation test
uses, so a new model is covered automatically.

**Step 3 — composite indexes lead with `account_id`.** Per-table audit. Today only **one** model
declares `__table_args__` (`note.py:11`), so this is nearly greenfield. *Verify:* `EXPLAIN` on
representative queries shows an index scan, not a sequential scan.

**Step 4 — child-table drift guard.** §3.2 requires a composite FK or trigger so a child's
`account_id` cannot diverge from its parent's. **Two classes of table:**
- *Real FKs* (`task_schedules`, `transactions`, `template_items`, …) → composite FK
  `(account_id, parent_id)` referencing `(account_id, id)` on the parent.
- *Polymorphic, no FK* (F5: `alert`, `audit_log`, `document`, `note`, `tag_assignments` — they
  carry `entity_type` + `entity_id` with **no `ForeignKey`**) → a composite FK is **impossible**.
  Use a trigger, or accept application-only enforcement and say so. Do not silently skip them.

*Verify:* a test attempts to write a child row whose `account_id` differs from its parent's and
asserts it is rejected.

**Step 5 — unique constraints.** `SlugMixin` → `UNIQUE (account_id, slug)` per table;
`ha_entity`, `tag.name` per-account (F4). Skip `task.py:90`. *Verify:* two accounts can each
create a "main-house" property and a "Plumbing" tag.

**Step 6 — `0001_pg_baseline`.** Squashed, Postgres-native types (§5.4), all 36 domain tables +
identity + the 5 new. Archive the old revisions to `alembic/legacy_sqlite/`. *Verify:* `upgrade`
then `downgrade` clean on an empty Postgres.

**Step 7 — `0002_rls`.** Generated (§4.3). *Verify:* connected as `app`, a `SELECT` with no GUC
set returns zero rows — not an error, not all rows.

**Step 8 — scoped session.** §4.4. *Verify:* a query with no `current_account` raises
`LookupError`; a bulk `update()` is scoped (the case a naive `is_select` guard would miss).

**Step 9 — connection hygiene.** `after_begin` GUC, pool `checkin` `RESET`, `pool_pre_ping`.
*Verify:* two sequential transactions on one pooled connection under different accounts never see
each other's rows.

**Step 10 — raw SQL audit.** All 8 `text()` sites across `ai/tools.py`, `archive.py`,
`backup.py`. Rewrite the three in `ai/tools.py` onto the ORM (F2). *Verify:* `grep -rn
'text(f"' src/` returns nothing.

**Step 11 — `StorageProvider`.** Protocol, S3 backend, filesystem dev backend,
`Document.file_path` → opaque key. *Verify:* round-trip put/get/delete against both backends;
keys are tenant-prefixed.

**Step 12 — auth.** Google OIDC + PKCE, `users` upsert on `sub`, server-side sessions, CSRF,
`/signout`, sign-out-everywhere. *Verify:* full sign-in flow; a forged ID token is rejected; the
session cookie is httpOnly + Secure + SameSite=Lax.

**Step 13 — CLI re-point.** `db.py` → Postgres; ops commands take `--account`. *Verify:*
`mihomes task list --account <slug>` returns only that account's tasks.

**Step 14 — `backup.py` + `doctor` rewrite (F1).** Scope is now fixed by D13:

- **Drop the `pg_dump` path.** Managed Postgres owns database backups and PITR. Writing our own
  would be a second, unmonitored backup system competing with the vendor's — worse than none,
  because it invites false confidence.
- **Keep and build the media sync.** No database backup covers object storage. Either enable
  bucket versioning or run a scheduled sync; whichever, `mihomes backup` becomes a **media-only**
  command and its docstring must say so, or the next reader will assume it covers the database.
- **`doctor`** drops its `DB_PATH`/`MEDIA_DIR`/`BACKUPS_DIR` assumptions (which produce a false
  "Database not found" and skip every later check — `backup.py:58-60`) and keeps its ORM
  integrity checks (`:79-100`, orphan detection). Add a check that the managed provider's most
  recent backup is within the RPO window (D14) — the one thing that actually verifies the
  vendor is doing its job.
- **Rehearse a restore** before the first non-founder tenant (D14). Not optional, not automated —
  do it once by hand and write down how long it took. That number is the real RTO.

*Verify:* `mihomes doctor` on hosted reports no false errors, still detects an orphaned task, and
flags a stale backup. `mihomes backup` round-trips media to and from object storage.

**Step 15 — test-suite migration (F6).** Postgres fixture, seeded account, ContextVar set,
docker-compose (D12), Postgres service in CI. *Verify:* the existing 33 test files pass.

**Step 16 — importer.** `mihomes import <sqlite-path>`. Ordering is load-bearing because object
writes are **not** transactional with Postgres:
1. Read SQLite, build the int→UUIDv7 remap table per source table.
2. **Upload all files first** to deterministic keys (idempotent on retry).
3. **Verify** every object exists and its size matches.
4. **Then** commit the DB transaction carrying rewritten keys.

Failure leaves **orphaned objects (garbage), never dangling references (corruption)**. The reverse
order is prohibited. *Verify:* dry-run against a copy of the `telegram-bot` archive; row counts
and FK integrity match; a simulated mid-import failure leaves no partial account.

**Step 17 — CI isolation test.** The executable definition of the tenancy invariant. For **every**
model in the `TenantOwned` registry, assert an account-A session can never read, update, or delete
B's rows — via ORM queries, ORM bulk `update()`/`delete()`, **and** raw `session.execute(text(...))`
— and can never insert a row stamped with B's `account_id` (RLS `WITH CHECK` must reject it).
**Must exercise the three `ai/tools.py` call sites by name** (F2) — they are defended by RLS
alone. Runs against Postgres with RLS, on every PR.

---

## 7. Non-goals and deferred scope

### Do NOT do these

**N1 — Do not treat "add `account_id` to 36 tables" as one task.** The mixin gives you the
*column* (Step 2). Three more passes over the same 36 tables follow: composite index ordering
(Step 3), the drift guard (Step 4), and per-table RLS policies (Step 7). Under-scoping this is
the most likely way the phase slips.

**N2 — Do not guard the ORM filter on `is_select` alone.** `with_loader_criteria` also applies to
ORM-enabled `UPDATE`/`DELETE`. Guarding on `is_select` leaves bulk operations unscoped — a
cross-tenant **write** path (§4.1).

**N3 — Do not use a session-level `SET app.current_account`.** Connections are pooled and Fly
fronts Postgres with transaction-pooling PgBouncer. A session-level `SET` persists on the physical
connection and the next request under a different tenant inherits it. Transaction-local
`set_config(..., true)` only (D8, §11.2).

**N4 — Do not call `init_db()` or run migrations on app startup.** `web/server.py:39,63` does this
today. With more than one Fly machine, concurrent `alembic upgrade` races against the same
database. Migrations are a release step.

**N5 — Do not let the app connect as the table owner or a `BYPASSRLS` role.** Owners bypass RLS
unless `FORCE ROW LEVEL SECURITY` is set. The runtime role is the non-owner `app`; migrations run
as the owner, which bypasses policies — correct and intended (§4.2).

**N6 — Do not store uploads on a Fly volume.** Volumes are single-machine local NVMe (§11.3).
Using one silently caps the app at one machine and puts tenant files outside any backup.

**N7 — Do not add `'invited'` to `memberships.status`.** Pending invitations live in `invites`
because a membership requires a `user_id` an un-signed-up invitee does not have (D6,
`MULTITENANCY:118`).

**N8 — Do not create a `homes` table.** home = `properties` (D5/A4).

**N9 — Do not use `skip_tenant` outside admin/ops tooling.** It is the `sudo` of this codebase.
Every use must be greppable and code-reviewed.

### `DEFERRED (Phase N)` — leave room, do not build

| Item | Phase | Interface room to leave |
|---|---|---|
| Entitlements service | 2 | `accounts.plan` exists and is readable. Phase 2 adds config-only `can()`/`usage()`; Phase 3 wires billing state in (A0) |
| Onboarding flow, invites UI, RBAC enforcement | 2 | `invites` and `membership_property_scopes` tables ship now; no UI reads them yet |
| `require_permission(...)` | 2 | Referenced by `ONBOARDING` §9.4 and both gateway PRDs as if it exists. **It does not.** Phase 1 ships tenant scoping only — role checks are Phase 2 |
| Stripe columns on `accounts` | 3 | Columns ship now (§4.2) so Phase 3 needs no migration on a live table |
| `trial_ends_at` / `trial_used_at` logic | 3 | Same — columns now, behavior later |
| `processed_webhook_events` table | 3 | Global, no RLS (D3). Ships with billing |
| Per-tenant config UI | 2 | `configurations` PK becomes `(account_id, key)` in Step 6. The web UI replacing `mihomes ai setup` is Phase 2 — see `web/routes/ai.py:47` |
| Telegram/WhatsApp tenant-awareness | 4+ | `telegram_links` tables are **not** in this baseline (`PRD_REVIEW` B4) |

---

## 8. Acceptance criteria

| # | Criterion | Test |
|---|---|---|
| A1 | Every non-global model subclasses `TenantOwned` | `test_tenancy_registry.py::test_all_models_tenant_owned` |
| A2 | A second active owner per account is rejected | `test_membership.py::test_one_owner_partial_index` |
| A3 | Two accounts can each hold the same slug | `test_slug_scoping.py::test_slug_unique_per_account` |
| A4 | Two accounts can each hold a "Plumbing" tag | `test_slug_scoping.py::test_tag_name_per_account` |
| A5 | Query with no `current_account` raises `LookupError` | `test_scoped_session.py::test_fails_closed_without_context` |
| A6 | Bulk `update()`/`delete()` are scoped | `test_scoped_session.py::test_bulk_ops_scoped` |
| A7 | Insert is auto-stamped with the current account | `test_scoped_session.py::test_insert_stamped` |
| A8 | RLS with unset GUC returns **zero rows**, not an error | `test_rls.py::test_unset_guc_returns_empty` |
| A9 | RLS `WITH CHECK` rejects an insert stamped with another account | `test_rls.py::test_with_check_rejects_foreign_account` |
| A10 | The account-picker query works before account context (bootstrap) | `test_rls.py::test_membership_self_policy` |
| A11 | Pooled connection reuse never leaks tenant context | `test_connection_hygiene.py::test_no_guc_leak_across_transactions` |
| A12 | Child `account_id` cannot diverge from its parent's | `test_drift_guard.py::test_child_account_mismatch_rejected` |
| A13 | No f-string SQL remains | `test_no_raw_sql_interpolation.py::test_no_fstring_text_calls` |
| A14 | Storage keys are tenant-prefixed; round-trip works | `test_storage.py::test_key_prefix_and_roundtrip` |
| A15 | Google sign-in creates a user + session; forged token rejected | `test_auth.py::test_signin_flow`, `::test_rejects_forged_token` |
| A16 | Session cookie is httpOnly, Secure, SameSite=Lax | `test_auth.py::test_cookie_flags` |
| A17 | Revoking a membership denies access on the next request | `test_auth.py::test_revocation_immediate` |
| A18 | `mihomes doctor` on hosted reports no false error | `test_ops_commands.py::test_doctor_no_filesystem_assumptions` |
| A19 | Importer round-trips row counts and FK integrity | `test_importer.py::test_roundtrip_counts_and_fks` |
| A20 | A failed import leaves no partial account | `test_importer.py::test_failure_leaves_nothing` |
| A21 | **Isolation: A can never read/write/delete B, any path** | `test_isolation.py::test_cross_tenant_denied_all_models` |
| A22 | Isolation holds for the three `ai/tools.py` raw-SQL sites | `test_isolation.py::test_ai_tools_raw_sql_scoped` |
| A23 | The existing 33 test files still pass under tenancy | full suite green in CI |

**A21 is the phase's definition of done.** If it is not green, Phase 1 is not finished regardless
of what else works.

---

## 9. Test manifest

```
tests/unit/test_tenancy_registry.py        every model carries TenantOwned
tests/unit/test_membership.py              roles, one-owner index, status enum
tests/unit/test_slug_scoping.py            per-account slug + tag uniqueness
tests/unit/test_scoped_session.py          filter, stamping, fail-closed, bulk ops
tests/unit/test_storage.py                 Protocol conformance, key shape, both backends
tests/unit/test_no_raw_sql_interpolation.py  static guard against text(f"...")
tests/integration/test_rls.py              policies, WITH CHECK, unset GUC, bootstrap policy
tests/integration/test_connection_hygiene.py  pooled reuse, GUC reset, PgBouncer shape
tests/integration/test_drift_guard.py      child/parent account_id mismatch
tests/integration/test_auth.py             OIDC flow, sessions, cookies, revocation
tests/integration/test_ops_commands.py     doctor/backup without filesystem assumptions
tests/integration/test_importer.py         round-trip, failure isolation, file ordering
tests/integration/test_isolation.py        THE isolation test (A21/A22) — Postgres + RLS
```

**Fixtures.** Replace `conftest.py`'s in-memory SQLite engine with a Postgres fixture
(`TEST_DATABASE_URL`, skipping when unset) plus `account_a` / `account_b` fixtures that seed
accounts and bind the ContextVar. 28 of 33 existing files use the `session` fixture (F6), so keep
its **name and semantics** — it should now yield an account-scoped session — rather than renaming
it and touching 28 files.

**CI.** A Postgres service container is required. The isolation test cannot run on SQLite: the
raw-SQL cases are defended by RLS alone (§9).

---

## 10. Environment

| Var | Purpose | Example |
|---|---|---|
| `DATABASE_URL` | Postgres, as the non-owner `app` role | `postgresql+psycopg://app:pw@host/mihomes` |
| `MIGRATION_DATABASE_URL` | Owner role, for Alembic only | `postgresql+psycopg://owner:pw@host/mihomes` |
| `TEST_DATABASE_URL` | CI/local test Postgres | — |
| `GOOGLE_CLIENT_ID` / `_SECRET` | OIDC | — |
| `SESSION_SECRET` | Signs session + CSRF cookies | — |
| `STORAGE_PROVIDER` | `s3` (hosted) / `fs` (dev) | — |
| `S3_BUCKET` / `S3_ENDPOINT` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Object storage | Tigris on Fly, or any S3-compatible |

Two distinct database URLs is deliberate: the app must **not** connect as the owner (N5), but
Alembic must, because migrations legitimately bypass RLS.
