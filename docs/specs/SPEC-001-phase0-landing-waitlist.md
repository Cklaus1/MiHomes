# SPEC-001 — Phase 0: Landing Page + Waitlist

**Phase:** 0 (canon — `../product/SAAS_PRD.md` §10)
**Status:** Ready to build
**Written:** 2026-07-31
**Source PRDs:** `../product/GTM_LAUNCH_PLAN.md` §2–5, `../architecture/BILLING_AND_EMAIL.md` §2–3, `../architecture/MULTITENANCY.md` §11

**Goal.** A public marketing page at `mihomes.ai` that captures a waitlist, confirms by email,
and validates demand before the multitenant re-platform is funded with real effort.

**Exit criteria** (`SAAS_PRD` §10): the waitlist gate is met. The number itself is a business
decision — see §1.3.

**What this phase is not.** No `users` table, no sessions, no tenancy, no billing. `SAAS_PRD:125`
is explicit that there is no `users` table before Phase 1, and nothing here creates one.

---

## 1. Decisions

### 1.1 Locked — engineering

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Deploy shape | **New standalone FastAPI app** (`mihomes.landing`), not a route added to the existing app | `GTM` §4 recommends Option A ("route in the existing app") for stack reuse, but `GTM:247` immediately cautions that the existing app is "the single-user product with 23 route modules and **no authentication**" and the Phase 0 deploy "must be a *stripped instance*". A separate app module is the honest way to do that: reuse the stack, share nothing. See §7-N1 |
| D2 | Hosting | Fly.io, single region | `MULTITENANCY` §11 (canon) |
| D3 | Database | Postgres on Fly, `waitlist` table only | `MULTITENANCY` §11 locks Postgres. Phase 0 does not use SQLite — starting on the target engine avoids a pointless migration two weeks later |
| D4 | `waitlist` tenancy | **Global. No `account_id`, no RLS** | It ships before `accounts` exists. `PRD_REVIEW` A5 flags it as bootstrap-class alongside `sessions` and `processed_webhook_events` |
| D5 | Primary key | `uuid` PK, UUIDv7, app-side via `mihomes.ids.new_id()` | Matches the Phase 1 decision so Phase 0 rows never need remapping. **No DB-side default** — `gen_random_uuid()` emits v4, which would mix versions in one column and destroy the index locality that is the reason to pick v7. See §4.1 for why `new_id()` is a helper and not `uuid.uuid7()` directly |
| D6 | Email templates | Server-side Jinja, in-repo | `BILLING` §2.5 — a vendor-hosted template would not survive failover to Postmark/SES, breaking the abstraction requirement |
| D7 | Double opt-in | Required. A row is not "confirmed" until the emailed link is clicked | `GTM:209` specifies double opt-in; `GTM:333` makes confirmation rate a tracked metric; and the Phase 0 gate counts **confirmed** signups (`GTM:293`) |
| D8 | OAuth stub scope | Verify Google ID token → extract verified email + name → write a `waitlist` row. Nothing else | `GTM:243`, `SAAS_PRD:125`. No `users` row, no session, no cookie |
| D9 | Migrations | Alembic, run as an explicit **deploy step**, never on app startup | The existing app calls `init_db()` from `web/server.py:39,63`. That is safe for one process and wrong when Fly may run several — concurrent `alembic upgrade` on boot is a race. See §7-N4 |
| D10 | Rate limiting | In-process, per-IP, on `POST /waitlist` and the OAuth callback | Public unauthenticated endpoints that write rows and send email. Redis would be premature at this traffic |

### 1.2 Locked — content and infrastructure

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D11 | DMARC record | `v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai` — **without** `adkim=s; aspf=s` | `BILLING:224` explains why strict alignment breaks legitimately-signed Resend mail: the return-path sits on its own sub-label. `GTM:273` contradicts this in a copy-pasteable table. `PRD_REVIEW` A6. **This spec uses the BILLING value** |
| D12 | Sending domain | `send.mihomes.ai`, verified in Resend | `BILLING` §3 — isolates transactional reputation from the apex and from future bulk sends |
| D13 | Apex vs app | Marketing on apex `mihomes.ai`; `app.mihomes.ai` reserved, may 503 until Phase 1 | `GTM` §5 rows 1–3 |
| D14 | Pricing display | Plan *shapes* only (Free / Pro / Estate). **No dollar figures** | `GTM:157` — every price in `PRICING_AND_PACKAGING.md` is `PLACEHOLDER` |
| D15 | Chat-intake card | Show **Telegram only**, or omit the card | `GTM:142` — WhatsApp Baileys pairing is currently broken and Twilio is post-GA. Advertising it would be vaporware |

### 1.3 `OPEN — needs decision: founder`

None of these block **writing code**. Each blocks **launching**. Build proceeds; these are
collected in the pre-launch checklist (§6, Step 9).

| # | Question | Blocks | Notes |
|---|---|---|---|
| O1 | **ToS + Privacy Policy published** | Collecting the first real email | Legally required before capture. `GTM:351` flags counsel review; `SAAS_PRD:194` lists it in the GA definition of done. No doc owns drafting it. The footer links exist in the template and will 404 until this lands |
| O2 | **Founding-member offer** — extended trial *or* annual discount? | Final landing copy | `GTM:215`, `PRICING` Q6. The page promises "a founding-member offer"; the confirmation email should state the actual terms. Until decided, copy says only "a founding-member offer" |
| O3 | **Waitlist gate number** | Phase 0 → 1 transition, not the build | `GTM:293` proposes ≥250 confirmed at ≥3% over a trailing 2-week window with ≥500 sessions; `SAAS_PRD:125` leaves it to the founder |
| O4 | **Show queue position publicly?** | One line of the confirmation email | `GTM:212`. Schema supports it either way (`created_at` ordering). Default: compute it, do not display it |

---

## 2. Doc-fix prerequisites

Contradictions this phase would otherwise inherit. Fix in the PRDs, not just here.

| Ref | Fix | File |
|---|---|---|
| **A6** | Delete `adkim=s; aspf=s` from the DMARC row | `GTM_LAUNCH_PLAN.md:273` |
| **A5** | Add `waitlist` to the §3.1 table list and the §5.2 baseline step-1 list, marked global | `MULTITENANCY.md` |
| **E3** | Note that Phase 0 already provisions Fly + Postgres, so Phase 1 inherits rather than creates them | `MULTITENANCY.md` §11 |

Not blocking, worth doing in the same pass: `SAAS_PRD.md` §13 and `product/README.md` do not
index `OMNICHANNEL_GATEWAY_PRD.md` or `WHATSAPP_GATEWAY_PRD.md`, though both claim to list the
complete doc set (`PRD_REVIEW` §G).

---

## 3. File manifest

### New — landing app

```
src/mihomes/landing/__init__.py            app factory: create_landing_app()
src/mihomes/landing/app.py                 FastAPI app, route registration, rate-limit middleware
src/mihomes/landing/routes.py              GET /, POST /waitlist, GET /waitlist/confirm, GET /healthz
src/mihomes/landing/oauth.py               Google OAuth stub: start + callback
src/mihomes/landing/ratelimit.py           in-process per-IP token bucket
src/mihomes/landing/server.py              entry point: mihomes-landing
src/mihomes/landing/templates/base.html    layout: <head>, inlined critical CSS, footer
src/mihomes/landing/templates/index.html   the 9 landing sections (GTM §2.1-2.9)
src/mihomes/landing/templates/confirmed.html   post-confirmation page
src/mihomes/landing/static/hero.svg        static hero image — no JS, no video (GTM:82)
```

### New — email package (reused verbatim in Phases 2–4)

```
src/mihomes/services/email/__init__.py
src/mihomes/services/email/provider.py           Protocol, exceptions, EmailResult, factory
src/mihomes/services/email/resend_provider.py    ResendProvider
src/mihomes/services/email/console_provider.py   ConsoleProvider (dev/CI — logs, sends nothing)
src/mihomes/services/email/service.py            EmailService — templates, rendering, retry
src/mihomes/services/email/render.py             render_template() -> (subject, html, text)
src/mihomes/services/email/templates/base.html
src/mihomes/services/email/templates/waitlist_confirmation.html
src/mihomes/services/email/templates/waitlist_confirmation.txt
```

### New — shared

```
src/mihomes/ids.py                         new_id() -> uuid.UUID  (UUIDv7)
src/mihomes/models/waitlist.py             Waitlist model
src/mihomes/services/waitlist.py           business logic: signup, confirm
alembic/versions/xxxx_waitlist.py          creates the waitlist table
```

### New — deployment

```
Dockerfile                                 landing image
fly.toml                                   Fly app config
.dockerignore
docs/deploy/PHASE0-DEPLOY.md               DNS records, Fly setup, secrets, launch checklist
```

### Modified

```
pyproject.toml    add: resend, psycopg[binary], authlib, itsdangerous
                  add script: mihomes-landing = "mihomes.landing.server:main"
```

**Not modified:** `src/mihomes/web/app.py`, `src/mihomes/web/server.py`, and every existing
route module. Phase 0 does not touch the single-user app. See §7-N1.

---

## 4. Schemas as code

### 4.1 `src/mihomes/ids.py`

```python
"""UUIDv7 generation — one helper so every id in the system is time-ordered."""

from __future__ import annotations

import os
import time
import uuid

__all__ = ["new_id"]


def _uuid7_fallback() -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit unix_ts_ms | ver | rand_a | var | rand_b."""
    ts_ms = int(time.time() * 1000) & 0xFFFF_FFFF_FFFF
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand >> 62) & 0x0FFF          # 12 bits
    rand_b = rand & 0x3FFF_FFFF_FFFF_FFFF   # 62 bits
    value = (
        (ts_ms << 80)
        | (0x7 << 76)        # version 7
        | (rand_a << 64)
        | (0b10 << 62)       # variant RFC 4122
        | rand_b
    )
    return uuid.UUID(int=value)


# uuid.uuid7() is stdlib from Python 3.14; pyproject declares requires-python >=3.11.
# Bind once at import so the per-call path stays a plain function reference.
new_id = getattr(uuid, "uuid7", _uuid7_fallback)
```

> **Why a helper and not `uuid.uuid7()`.** Verified: `uuid.uuid7()` exists in Python 3.14
> (confirmed on the dev machine, 3.14.3) but **not** at the `>=3.11` floor declared in
> `pyproject.toml:9`. Calling it directly would break on the declared minimum. This adds **no
> dependency** — the fallback is ~15 lines and the floor stays 3.11.

### 4.2 `src/mihomes/models/waitlist.py`

Follows the existing model convention (`models/configuration.py`, `models/__init__.py`).

```python
"""Waitlist model — Phase 0 signup capture. GLOBAL: no account_id (see SPEC-001 D4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base


class Waitlist(Base):
    __tablename__ = "waitlist"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )

    # Identity. Stored lowercased+stripped; see normalize_email() in services/waitlist.py.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Light qualification (GTM section 3 — optional, never gates the signup).
    num_homes: Mapped[str | None] = mapped_column(String(10), nullable=True)   # '1' | '2-3' | '4+'
    has_staff: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Attribution (GTM section 3 segmentation table).
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)     # 'form' | 'google'
    utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    referred_by: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # Double opt-in (D7). Raw token lives only in the email; we store its SHA-256.
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    confirm_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirm_send_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Diagnostics. Not shown to users.
    signup_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)   # INET6 max length
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

**Field notes.**
- `email` at `String(320)` — the RFC 5321 maximum. `unique=True` implements `GTM:206`'s
  "one row per email; upsert on repeat".
- `num_homes` is a **string**, not an int: the form offers `1 / 2-3 / 4+` (`GTM:202`), and `2-3`
  is not an integer. Do not "fix" this to `Integer`.
- `has_staff` is nullable three-state — yes / no / didn't answer. `GTM:202` makes it optional,
  so `False` and "unanswered" must stay distinguishable.
- `confirm_token_hash` stores a hash, never the token. Same discipline as invite tokens
  (`ONBOARDING_AUTH_RBAC` §10).
- `confirm_send_count` bounds resend abuse (§5.4).

### 4.3 Alembic migration

Postgres-only. Unlike the 36 legacy revisions this uses plain `op.create_table` — the
`batch_alter_table` wrapper in the existing migrations is a SQLite workaround
(`MULTITENANCY` §5.4) and is not needed here.

```python
"""create waitlist table

Revision ID: 0001_waitlist
Revises:
Create Date: 2026-07-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_waitlist"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "waitlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("num_homes", sa.String(10), nullable=True),
        sa.Column("has_staff", sa.Boolean(), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("utm_campaign", sa.String(200), nullable=True),
        sa.Column("utm_source", sa.String(200), nullable=True),
        sa.Column("utm_medium", sa.String(200), nullable=True),
        sa.Column("referred_by", sa.String(320), nullable=True),
        sa.Column("confirm_token_hash", sa.String(64), nullable=True),
        sa.Column("confirm_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirm_send_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signup_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_waitlist_email", "waitlist", ["email"])
    op.create_index("ix_waitlist_email", "waitlist", ["email"])
    op.create_index("ix_waitlist_confirm_token_hash", "waitlist", ["confirm_token_hash"])
    # Supports the funnel metric: confirmed signups over a trailing window (GTM:293).
    op.create_index("ix_waitlist_confirmed_at", "waitlist", ["confirmed_at"])


def downgrade() -> None:
    op.drop_table("waitlist")
```

> **`DEFERRED (Phase 1)`** — this migration is **not** the Phase 1 baseline. `MULTITENANCY`
> §5.2 squashes the 36 legacy SQLite revisions into `0001_pg_baseline`. When that lands, the
> `waitlist` table must appear in it (`PRD_REVIEW` A5) and this revision becomes part of the
> squash. Phase 0 rows must survive — do not drop and recreate.

---

## 5. Function signatures

### 5.1 `src/mihomes/services/email/provider.py`

Mirrors `services/ai/provider.py` exactly: Protocol + exception hierarchy + factory.

```python
class EmailProviderError(Exception): ...
class EmailAuthError(EmailProviderError): ...
class EmailSendError(EmailProviderError): ...


@dataclass(frozen=True)
class EmailResult:
    provider_message_id: str
    provider: str  # "resend" | "console"


class EmailProvider(Protocol):
    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        *,
        text: str | None = None,
        reply_to: str | None = None,
    ) -> EmailResult:
        """Send a pre-rendered message. Returns the provider message id."""
        ...


def get_email_provider(
    provider_name: str | None = None,
    api_key: str | None = None,
    from_address: str | None = None,
) -> EmailProvider:
    """Factory. Defaults: EMAIL_PROVIDER env or 'resend'; EMAIL_FROM env."""
```

**The Protocol is deliberately narrow: a provider transports an already-rendered message.**
Template selection and rendering live in `EmailService` so they happen once, identically,
regardless of vendor — a provider that rendered its own templates would break failover
(`BILLING` §2.1).

### 5.2 `src/mihomes/services/email/render.py`

```python
def render_template(template: str, data: dict) -> tuple[str, str, str]:
    """Render a template key to (subject, html, text).

    Subject is the first line of the .html template's {% block subject %}.
    A .txt sibling is required — never ship HTML-only mail.
    """
```

### 5.3 `src/mihomes/services/email/service.py`

```python
class EmailService:
    def __init__(self, provider: EmailProvider) -> None: ...

    def _send(self, to: str, template: str, data: dict) -> None:
        """Render and dispatch. Catches EmailSendError, logs, never raises to the caller."""

    def send_waitlist_confirmation(self, to: str, *, confirm_url: str,
                                   position: int | None = None) -> None: ...
```

**Delivery semantics** (`BILLING` §2.4): calls from a request handler must not block or fail
the caller. Catch `EmailSendError`, log with template key + recipient, return. A failed
confirmation email must never roll back the signup row — the user can request a resend.

### 5.4 `src/mihomes/services/waitlist.py`

```python
def normalize_email(raw: str) -> str:
    """Lowercase, strip. Raises ValueError if not a plausible address.

    Deliberately NOT aggressive: no plus-address stripping, no dot-folding.
    Gmail treats a+b@gmail.com as a@gmail.com but most providers do not, and
    silently merging two people's signups is worse than a duplicate row.
    """


def signup(
    session: Session,
    *,
    email: str,
    name: str | None = None,
    num_homes: str | None = None,
    has_staff: bool | None = None,
    source: str = "form",
    utm: dict[str, str] | None = None,
    referred_by: str | None = None,
    signup_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[Waitlist, str | None]:
    """Create or update a waitlist row. Idempotent per email (GTM:206 'upsert on repeat').

    Returns (row, raw_confirm_token). The raw token is returned exactly once, for the
    email; only its hash is persisted. Returns (row, None) when the row is already
    confirmed — an already-confirmed signup does not get a new token.
    """


def confirm(session: Session, *, raw_token: str) -> Waitlist | None:
    """Confirm by raw token. Returns the row, or None if unknown/expired.

    Idempotent: confirming an already-confirmed row returns it unchanged rather
    than erroring — users click links twice, and mail scanners pre-fetch them.
    """


def position(session: Session, row: Waitlist) -> int:
    """1-based queue position by created_at among confirmed rows (GTM:212)."""


def confirmed_count(session: Session) -> int:
    """Confirmed signups — the Phase 0 gate metric (GTM:293)."""
```

### 5.5 `src/mihomes/landing/routes.py`

```python
@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    """The marketing page. Captures utm_* from query params into the form."""


@router.post("/waitlist")
async def join_waitlist(request: Request, ...) -> HTMLResponse:
    """Form submit. Rate-limited. Always renders the same success partial,
    whether the email is new or already known — see section 7-N3."""


@router.get("/waitlist/confirm", response_class=HTMLResponse)
async def confirm_waitlist(request: Request, token: str) -> HTMLResponse:
    """Double opt-in landing. Sets confirmed_at."""


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness for Fly. Checks DB connectivity. No auth, no PII."""
```

### 5.6 `src/mihomes/landing/oauth.py`

```python
@router.get("/auth/google/start")
async def google_start(request: Request) -> RedirectResponse:
    """Begin OIDC authorization-code flow with PKCE. State + verifier in a signed
    short-lived cookie."""


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str, state: str) -> Response:
    """Verify state, exchange code, VERIFY THE ID TOKEN SIGNATURE, extract
    email + name, call waitlist.signup(source='google').

    Creates NO users row and NO session — there is no users table in Phase 0
    (SAAS_PRD:125). Discards the access token immediately (BILLING/ONBOARDING 3.2).
    """
```

---

## 6. Sequenced steps

Each step is independently verifiable and separately committable.

**Step 1 — `mihomes.ids`.**
Write `ids.py` (§4.1) + tests. Verify: 1,000 generated ids are unique, sort in creation order
when sorted as bytes, and report `.version == 7`.

**Step 2 — `Waitlist` model + migration.**
Model (§4.2), migration (§4.3), register in `models/__init__.py`. Verify: `alembic upgrade head`
against a local Postgres, then `downgrade` back cleanly.

**Step 3 — email package.**
Protocol, `ConsoleProvider`, `ResendProvider`, factory, `render_template`, `EmailService`,
templates. Verify: `ConsoleProvider` renders `waitlist_confirmation` to stdout with both HTML and
text parts; factory raises on an unknown provider name.

> Build this to final quality now. It is the one Phase-0 artifact reused **verbatim** in Phases
> 2–4 (`BILLING` §1 phasing table) — welcome, invites, receipts, and dunning all ride on it.

**Step 4 — waitlist service.**
`normalize_email`, `signup`, `confirm`, `position`, `confirmed_count`. Verify: signup is
idempotent per email; the token round-trips; only the hash is persisted (assert the raw token
appears nowhere in the row).

**Step 5 — landing app skeleton.**
`create_landing_app()`, `/healthz`, rate-limit middleware, `mihomes-landing` entry point.
Verify: app boots, `/healthz` returns 200, and **`GET /properties` returns 404** — proving the
single-user app is not mounted (§7-N1).

**Step 6 — templates + `GET /`.**
The nine sections from `GTM` §2.1–2.9. Inlined critical CSS, one static hero, no JS framework.
Verify: page renders, contains no dollar amounts (D14), and the chat-intake card does not
mention WhatsApp (D15).

**Step 7 — `POST /waitlist` + confirm route.**
Wire the form to `signup()`, send via `EmailService`, implement `GET /waitlist/confirm`. Verify
the full loop against `ConsoleProvider`: submit → token in console → GET confirm → `confirmed_at`
set.

**Step 8 — Google OAuth stub.**
`/auth/google/start` and `/auth/google/callback`. Verify with a mocked ID token: a valid token
creates a `waitlist` row with `source='google'`; a token with a bad signature is rejected;
**no `users` row and no session cookie are created**.

**Step 9 — deploy.**
`Dockerfile`, `fly.toml`, `docs/deploy/PHASE0-DEPLOY.md`. Migrations as a release command, never
on boot (D9). The deploy doc carries the DNS table (with the D11 DMARC value) and the pre-launch
checklist:

- [ ] `send.mihomes.ai` verified in Resend; SPF, DKIM, MX passing
- [ ] Test confirmation email lands in an inbox, not spam
- [ ] Apex serves the landing over HTTPS; `www` 301s to apex; HSTS on
- [ ] `app.mihomes.ai` reserved (503 placeholder is fine)
- [ ] **O1** — ToS + Privacy published and linked in the footer
- [ ] **O2** — founding-member offer wording final
- [ ] **O3** — waitlist gate number recorded in `GTM_LAUNCH_PLAN.md`

---

## 7. Non-goals and deferred scope

### Do NOT do these

**N1 — Do not mount the existing app, or any of its routers, in the landing app.**
`src/mihomes/web/app.py` calls `include_router` **22** times (there are 23 route modules;
`reports.py` is not currently mounted), with **no authentication of any kind**, over live estate
data — properties, staff, financials, documents. `web/server.py:50,76` defaults the bind host to
`127.0.0.1` precisely because of this, with the comment "the app has no auth and holds sensitive
estate data". Exposing any of it publicly is a data breach, not a bug.
`GTM:247` says the Phase 0 deploy "must be a *stripped instance* mounting only the landing,
waitlist, and OAuth-stub routes". The route allowlist is exactly:

```
GET  /                      landing page
POST /waitlist              signup
GET  /waitlist/confirm      double opt-in
GET  /auth/google/start     OAuth stub
GET  /auth/google/callback  OAuth stub
GET  /healthz               liveness
GET  /static/*              hero image, CSS
```

Step 5's test asserts `GET /properties` → 404. Do not delete that test.

**N2 — Do not create a `users` table, a session, or a login cookie.**
`SAAS_PRD:125`: the OAuth flow in Phase 0 "verifies the email and adds it to the waitlist; full
sessions/tenancy arrive in Phase 1 (there is no `users` table before then)". Building it early
means building it against a schema Phase 1 then changes.

**N3 — Do not reveal whether an email is already on the list.**
`POST /waitlist` renders the identical success state for a new and an existing address. A
distinguishable response turns the endpoint into an email-enumeration oracle. Resend the
confirmation to an unconfirmed row (bounded by `confirm_send_count`); say nothing different.

**N4 — Do not call `init_db()` or run migrations on app startup.**
`web/server.py:39,63` does this today. With more than one Fly machine, concurrent
`alembic upgrade` on boot is a race against the same database. Migrations are a release step
(D9).

**N5 — Do not add a JS framework, an analytics SDK, or web fonts.**
`GTM:55` requires <1.5s LCP on 4G and "no heavy JS". Static HTML + inlined critical CSS + one SVG
hits that comfortably; a bundler does not.

**N6 — Do not hardcode Stripe prices or dollar amounts anywhere.**
D14. Every figure in `PRICING_AND_PACKAGING.md` is `PLACEHOLDER`.

**N7 — Do not write the raw confirmation token to the database or to logs.**
Hash only (§4.2). Same discipline as invite tokens (`ONBOARDING_AUTH_RBAC` §10).

### `DEFERRED (Phase N)` — leave room, do not build

| Item | Phase | Interface room to leave |
|---|---|---|
| Referral bump ("move up 20 spots") | 4 | `referred_by` column exists and is populated; no logic reads it yet (`GTM:212`) |
| Queue position display | 0/4 | `position()` is implemented and callable; whether the email shows it is **O4** |
| `FailoverEmailProvider` (Resend → Postmark/SES) | 4 | Wrapping the same `EmailProvider` Protocol is enough — no caller changes (`BILLING` §2.7). Pre-verify the standby's DKIM **before** an outage; DNS propagation is not available mid-incident |
| Remaining email types (welcome, invites, receipts, dunning) | 2–3 | One `EmailService` method per type, `BILLING` §2.6 table |
| Metered-billing hooks | 3+ | Nothing in Phase 0 touches billing |
| `waitlist` → `accounts` conversion | 1 | Phase 1 reads `waitlist` to invite the cohort. Do not delete rows on conversion; `confirmed_at` is the funnel baseline |

---

## 8. Acceptance criteria

Each criterion names the test that proves it. A criterion without a test does not count as met.

| # | Criterion | Test |
|---|---|---|
| A1 | UUIDv7 ids are unique, time-ordered, and report version 7 | `test_ids.py::test_uuid7_properties` |
| A2 | `new_id` works on the declared 3.11 floor, not only 3.14 | `test_ids.py::test_fallback_generates_valid_v7` |
| A3 | Migration applies and reverses cleanly on Postgres | `test_migration_waitlist.py::test_upgrade_downgrade` |
| A4 | Duplicate email updates the existing row; never a second row | `test_waitlist_service.py::test_signup_is_idempotent` |
| A5 | Raw confirm token is never persisted | `test_waitlist_service.py::test_token_stored_hashed_only` |
| A6 | Confirm sets `confirmed_at`; a second confirm is a no-op | `test_waitlist_service.py::test_confirm_idempotent` |
| A7 | An expired or unknown token does not confirm | `test_waitlist_service.py::test_confirm_rejects_bad_token` |
| A8 | Email renders both HTML and text parts | `test_email_render.py::test_waitlist_confirmation_has_both_parts` |
| A9 | Provider factory raises on an unknown name | `test_email_provider.py::test_unknown_provider_raises` |
| A10 | A send failure does not roll back the signup | `test_waitlist_routes.py::test_signup_survives_email_failure` |
| A11 | **The single-user app is not reachable** | `test_landing_app.py::test_existing_routes_are_404` |
| A12 | Response is identical for new and existing emails | `test_waitlist_routes.py::test_no_email_enumeration` |
| A13 | Rate limiting returns 429 past the threshold | `test_ratelimit.py::test_burst_is_limited` |
| A14 | Valid Google ID token creates a waitlist row, no session | `test_oauth_stub.py::test_callback_creates_waitlist_row_only` |
| A15 | Bad ID token signature is rejected | `test_oauth_stub.py::test_callback_rejects_forged_token` |
| A16 | Landing page contains no dollar amounts | `test_landing_page.py::test_no_prices_rendered` |
| A17 | `/healthz` returns 200 with DB reachable | `test_landing_app.py::test_healthz` |
| A18 | DMARC record in the deploy doc omits strict alignment | `test_deploy_docs.py::test_dmarc_relaxed_alignment` |

> A18 is a docs test on purpose. `PRD_REVIEW` A6 is a copy-pasteable wrong value in a DNS table;
> a grep-level test is the cheapest way to stop it coming back.

---

## 9. Test manifest

```
tests/unit/test_ids.py                      UUIDv7 shape, ordering, fallback path
tests/unit/test_waitlist_model.py           column types, nullability, unique constraint
tests/unit/test_waitlist_service.py         signup/confirm/position/confirmed_count, hashing
tests/unit/test_email_provider.py           Protocol conformance, factory, ConsoleProvider
tests/unit/test_email_render.py             subject/html/text extraction, both parts present
tests/unit/test_ratelimit.py                token bucket, per-IP isolation, 429
tests/integration/test_migration_waitlist.py  alembic upgrade/downgrade on real Postgres
tests/integration/test_landing_app.py       app boots, /healthz, 404s for non-allowlisted routes
tests/integration/test_waitlist_routes.py   POST /waitlist, confirm loop, enumeration, failure isolation
tests/integration/test_oauth_stub.py        mocked ID token: accept, reject, no users row
tests/integration/test_landing_page.py      rendered HTML: no prices, no WhatsApp, sections present
tests/unit/test_deploy_docs.py              DMARC value guard (A18)
```

**Fixtures.** `tests/conftest.py` currently builds an **in-memory SQLite** engine. Phase 0 is
Postgres-only, so add a `pg_session` fixture (testcontainers or a `TEST_DATABASE_URL` env var,
skipping when unset) rather than changing the existing `session` fixture — the 780+ existing
tests depend on its current behaviour.

**Existing deps cover this.** `pytest`, `pytest-cov`, `httpx` (for `TestClient`), and `ruff` are
already in `[project.optional-dependencies].dev`. Only `psycopg[binary]` is new for tests.

---

## 10. Environment

| Var | Purpose | Example |
|---|---|---|
| `DATABASE_URL` | Postgres connection | `postgresql+psycopg://user:pw@host/db` |
| `EMAIL_PROVIDER` | Provider selector | `resend` (prod) / `console` (dev, CI) |
| `RESEND_API_KEY` | Resend auth | — |
| `EMAIL_FROM` | Default From | `MiHomes <no-reply@send.mihomes.ai>` |
| `GOOGLE_CLIENT_ID` | OIDC | — |
| `GOOGLE_CLIENT_SECRET` | OIDC | — |
| `LANDING_BASE_URL` | Absolute confirm links | `https://mihomes.ai` |
| `SECRET_KEY` | Signs the OAuth state cookie | — |

Secrets come from `fly secrets`, never from the repo. `.env`, `.env.*`, `*.pem`, and
`*secret*.json` are already gitignored (`BILLING` §9).
