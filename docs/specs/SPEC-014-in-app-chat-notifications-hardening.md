# SPEC-014 — In-app chat: notifications when away + hardening

**Phase:** 4+ — native in-app chat, stage 3 of 3 (plan Phases 6–7)
**Status:** Ready to build once SPEC-012 and SPEC-013 have landed — **3 open decisions** (O1: what a digest email may say; O2: digest interval; O3: property names on a lock screen)
**Written:** 2026-10-28
**Verified against:** `worktree-chat-specs` @ **`9c7d595`** (the harness tip: Conversations 0026 + webhook ack). Every `file:line` below was read at that ref. SPEC-012/013 names (`chat_native.py`, `visible_conversation_ids`, `chat.use`, `0027_native_chat`) are **their contract, not code** — nothing of either exists yet (§0.5)
**Source plan:** `~/.claude/plans/plan-the-native-in-app-validated-dolphin.md` Phases 6–7
**Depends on:** SPEC-012 (tables, `access.py`, `/chat` routes, SSE stream, `chat.use`), SPEC-013 (`chat_mentions` rows), SPEC-005 (email outbox, suppression, RFC 8058), SPEC-010 (rate-limit precedent)

**Goal.** A member who is not looking at MiHomes learns that someone wrote to them, and the chat
cannot be flooded, spoofed cross-site, or exhausted by open streams.

**Exit criterion:** on evo-dev, a staff member receives a web push (Android/desktop, and iPhone
from the home-screen app) and, an hour later, one digest email for a DM that sat unread for ten
minutes. Neither names the sender or quotes the text. A revoked member gets nothing. A 21st post
in a minute returns 429 and the composer keeps the text. Fifty SSE clients pass §6 Step 9.

**The stake.** Every earlier chat stage keeps messages inside the tenant boundary: RLS, the
visible set, re-fetch under the reader's own session. **Notifications are the first chat feature
whose output leaves the building.** An email sits in a third-party inbox forever. A push lands on
a lock screen anyone at the kitchen counter can read. And a notification job has no request and
no principal, so it is the one chat path that runs with no user's visibility applied. **Visibility
is what decides who gets notified.** A mistake there sends private messages to the wrong person,
and from the outside it looks like the feature working.

---

## 0. Things a reader must know before trusting this spec

### 0.1 — "Away" means "unread after ten minutes"

The user ruled out presence (online / last seen). So the job never asks "is this person here?".
It asks whether the post is **still unread ten minutes after it was written**. Someone reading the
conversation advances `chat_participants.last_read_seq` (SPEC-012 `reads.mark_read`) and is never
notified. A false positive costs one push to someone who saw the post on another device and never
scrolled to it, and that is accepted. A presence system would cost the very tracking the user
excluded.

### 0.2 — The job has no principal, so it re-derives visibility for every recipient

Routes get visibility from `resolve_principal` (`web/deps.py:237`) and SPEC-012's
`visible_conversation_ids(session, membership)`. The job runs from cron with only an account bound
(`cli/jobs.py:541-572` `_account_session`). It therefore calls `visible_conversation_ids` itself,
**at send time, per recipient**. A mention can be written while a staff member is scoped to
property X, and the job can run after the owner removes X from their scope. SPEC-013 validating
mentions at write time (its contract) does not cover that gap. A13 and A14 test it under the
non-superuser role (`tests/conftest.py:212` `app_engine`), because superusers bypass RLS.

### 0.3 — SPEC-005's email machinery is reused as-is, and decides more than it looks

- **`EmailService._send` enqueues only when an account is bound.** With a session but no account
  it sends inline and skips the outbox (`services/email/service.py:104-111`). The job must bind
  `account_context` first (D10). Lifecycle mail is suppression-checked at enqueue (`:74-87`) and
  again at drain (`outbox.py:156-161`). Only lifecycle mail gets RFC 8058 headers (`:176-189`).
- **`drain-outbox` is the only path to a provider** (`cli/jobs.py:111`), so chat-notify enqueues
  and nothing more.
- **Links use `MIHOMES_BASE_URL`, which defaults to `https://app.mihomes.ai`**
  (`suppression.py:98-99`). On evo-dev it must be set, or every link points at production.
- **`SCHEDULE` (`cli/jobs.py:70`) is enforced.** `test_all_workloads_scheduled`
  (`test_jobs_enumeration.py:51`) fails on a command with no cadence, and `mihomes cron setup`
  prints from the same dict (`cli/cron.py:35-42`).
- **Suppression is global per address.** A one-click unsubscribe from a chat digest also stops
  the weekly digest.

### 0.4 — Neither existing rate limiter fits

`auth/ratelimit.py` counts failures, not events (`:18-24`). `landing.ratelimit.TokenBucket` has
the right shape, but importing it runs `landing/__init__.py:8`, which loads the whole landing app.
SPEC-001 calls keeping the two apps apart a security boundary (`:3-5`). D8 adds its own limiter.

### 0.5 — Contract facts found wrong or stale while verifying

- **`tests/integration/test_pg_baseline.py:154` pins 57 tables, and the real head count is 62.**
  `0020` (`task_completions`), `0022`, `0023`, `0025` and `0026` each added a table without
  raising the number. The file's own skipif (`:30`) kept that hidden. SPEC-012 adds 7 tables
  (→ 69), and this spec adds 2 (→ **71**). The fix is to **re-measure**, not to apply +9 on top
  of 57. Recorded as B3.
- The tenant-table pins agree: **55** at `test_isolation.py:90` and `test_tenancy_registry.py:276`.
  SPEC-012 raises them to 62, and SPEC-014 raises them to **64**.
- The cross-spec contract said `push_subscriptions.endpoint UNIQUE`. Under RLS that is
  unworkable, so this spec replaces it (D5).

---

## 1. Decisions

### 1.1 Locked

| # | Decision | Rationale |
|---|---|---|
| **D1** | **The rule.** A member *m* is due a notification for post *p* when all of these hold: (a) *p* is in a DM *m* participates in, or a `chat_mentions` row names *m*; (b) `p.seq > m.last_read_seq` for that conversation, with a missing participant row read as 0; (c) `now-48h < p.created_at ≤ now-10min`; (d) `p.author_membership_id IS DISTINCT FROM m`; (e) `p.deleted_at IS NULL`; (f) *m* is `active` and `p.conversation_id ∈ visible_conversation_ids(m)` **at send time**; (g) the participant is not `muted` and `chat_notify_prefs.muted_until` is NULL or in the past | (f) is §0.2. The **48h horizon** exists because the first run after deploy would otherwise mail every member about every historical unread DM. A mention in a muted conversation stays silent: the user's scope said "not muted" without exceptions |
| **D2** | **Two channels, two cadences, separate markers.** Push goes out **per item**, at most once. The marker is `chat_mentions.notified_at` (SPEC-012) or the new `chat_participants.last_notified_seq` (0028 ALTER, for DMs). Email is a **digest** at most once per `DIGEST_INTERVAL` (O2, default 60 min). It covers due items created in `(digest_through, now-10min]` and then advances `chat_notify_prefs.digest_through` | With a shared marker, either push consumes it and email never fires, or email consumes it and sends every 5 minutes. `digest_through` is the cutoff the digest covered, not when it was sent. Storing the send time would lose an item written just before a digest, because it was not yet 10 minutes old |
| **D3** | **The digest is `lifecycle` mail, and it carries counts, titles and one link. No sender names and no text** (O1 may widen this) | Lifecycle means it is suppressible and gets List-Unsubscribe (§0.3). A digest is mail the recipient can reasonably ask to stop, the same reasoning `send_weekly_digest` records (`service.py:438-443`). Minimal content because the email leaves the tenant boundary and outlives revocation. A staff member fired tomorrow keeps today's inbox. The body's first link is **Settings → Chat notifications**, because the global unsubscribe (§0.3) silences more than chat |
| **D4** | **The job is the only sender.** There is no immediate push after commit, even for DMs | Under D1, an instant push would notify someone who is reading the conversation at that moment. One sender also means one code path for visibility (§0.2) instead of two |
| **D5** | **`push_subscriptions` is unique on `(membership_id, endpoint)`, not on `endpoint`** (a deviation from the cross-spec contract) | A browser has one endpoint per origin. With a global unique, a user in two accounts collides with a row RLS hides from them. The upsert cannot see that row, so it raises, and the error reveals that the endpoint exists in another account |
| **D6** | **Endpoints are allowlisted: https only, and the host must be a known push service** (`fcm.googleapis.com`, `updates.push.services.mozilla.com`, `web.push.apple.com`, `*.notify.windows.com`). Checked at subscribe time and again at send time | The job POSTs to a URL the client supplied. Without the allowlist, any member can register `http://169.254.169.254/…` or a tailnet host, and the server sends requests there every 5 minutes. That is SSRF from inside the deployment |
| **D7** | **Lock-screen privacy.** The push payload is `{title, body, url, tag}`. For a group chat, `title` is the conversation title. For a DM it is the fixed string "Direct message", and `body` is the fixed "New message" or "You were mentioned". **There is no sender name and no text.** `tag = "chat-<conversation_id>"`, so a burst collapses into one notification | Web Push is end-to-end encrypted to the browser (RFC 8291), so the push service is not the reader to worry about. The lock screen is. A DM title would be the other person's name, which says who is messaging whom |
| **D8** | **Rate limits are per membership, in-process, using a sliding window.** Posts: 20 per 60s. Reactions: 10 per 10s. Bodies are limited to 4000 characters, checked in the service. A refused POST gets **429, `Retry-After`, plain text, and no partial** | Keyed on the membership because every evo-dev user arrives through Tailscale Serve. htmx does not swap a plain-text 4xx, and `app.js:124-133` toasts it instead, **so the composer keeps the text**, provided it clears only on success (B2) |
| **D9** | **Stream caps.** At most 6 concurrent `/chat/stream` per membership, and 200 per process. Over the cap the server returns 429. `chat.js` closes its stream once the tab has been hidden for 60s and reopens it with `Last-Event-ID` when the tab becomes visible | Browsers allow 6 HTTP/1.1 connections per host, so a seventh tab would starve every other request from that browser. EventSource does not retry after a non-200 response, and SPEC-012's replay makes reopening lossless |
| **D10** | **The job's core is a function taking injected factories.** `run_chat_notify(session_factory, *, push_sender, email_factory, now)` mirrors `outbox.drain_all` (`outbox.py:236`). It binds `account_context` per account, and one account's failure does not stop the sweep | Tests can run it under `app_engine` with a fake push sender. Binding the account is also what makes `_send` enqueue instead of sending inline (§0.3) |
| **D11** | **Order within an account.** Take `pg_try_advisory_xact_lock(hashtext('chat-notify:'‖account))`; skip the account if the lock is busy. Then set the markers, enqueue the digest, prune `chat_events` older than 7 days, **commit**, and only then send push | Overlapping runs cannot double-send. Email is exactly-once, because the outbox row commits with the marker. Push is at-most-once, the claim-first shape of `5fec742`. No transaction stays open across a 10s HTTP call (the session-touch deadlock lesson) |
| **D12** | **`/sw.js` and `/manifest.webmanifest` are public routes at the root**, in a new `web/routes/pwa.py` that goes on the permanent allowlist | A service worker's scope is capped at its script's directory, so `/static/sw.js` could never control `/chat/`. A manifest fetch carries no cookies by default, so one behind authentication breaks install. Both are fixed bytes, the `/healthz` argument (`test_route_declarations.py:76-81`) |
| **D13** | **The preferences UI is a partial loaded from `settings/_personal.html`**, the file both settings pages share (`_personal.html:1-2`). It loads via `hx-get="/chat/notifications"` and posts to `/chat/notifications` under `chat.use` | `/settings` redirects staff away (`routes/settings.py:230-231`). The existing Notifications section is estate-wide config posted under `member.manage` (`settings/index.html:79-129`). A loaded partial leaves both context builders unchanged |
| **D14** | **`pywebpush>=2.5`** is the one new dependency. VAPID keys are generated with `cryptography` (§5.3). A missing VAPID env disables push without failing the job | Measured 2026-10-28: 2.5.0 requires `cryptography>=47.0.0` (installed: 46.0.6), `aiohttp`, `requests`, `http-ece` and `py-vapid`. Step 4 therefore reruns the SPEC-010 KDF tests |

### 1.2 `OPEN — needs decision: founder`

| # | Question | Blocks |
|---|---|---|
| **O1** | **What may a digest email contain?** The default is counts plus conversation titles. Sender names make it more useful, and snippets more useful still, but each one copies tenant data into a mailbox MiHomes cannot reach, retained after the member is revoked | **Only `chat_digest.html/.txt`.** Build to the default. A widened template is additive |
| **O2** | **Digest interval.** Default `DIGEST_INTERVAL = 60 min` | Nothing; it is one constant |
| **O3** | **May a property name appear on a lock screen?** Under D7, group pushes show the conversation title, e.g. "Belle Estate". The alternative is a generic "MiHomes chat" for every push | Nothing; it is one branch in `build_payload` |

---

## 2. Doc-fix prerequisites

| # | Location | Fix |
|---|---|---|
| **B1** | `settings/index.html:84-86`, *"Notifications themselves aren't being sent yet"* | Scope the sentence to **estate alerts**, and add "Chat notifications are set under *Chat notifications* below". Left as is, it would be false for the one notification that does send |
| **B2** | SPEC-012 `static/chat.js` composer | Clear the composer **only when `event.detail.successful`** on `htmx:afterRequest`. Clearing on every response is what would turn D8's 429 into lost text. SPEC-012 also needs to expose `stream_slots()` (D9) around the stream generator |
| **B3** | `tests/integration/test_pg_baseline.py:154` (57) | Re-measure at head (62 at `0026`, 69 after `0027`, **71** after `0028`), with one comment line per migration, following the file's own rule (`:136-139`) |
| **B4** | `docs/specs/README.md` spec table | Add the SPEC-014 row: "Ready to build — 3 open decisions". This spec does not edit the README; the orchestrating commit does |

---

## 3. File manifest

### New

```
alembic/versions/0028_chat_notifications.py      two tenant tables + chat_participants.last_notified_seq
src/mihomes/models/chat_notify.py                PushSubscription, ChatNotifyPref
src/mihomes/services/chat/notify.py              D1 query, D2 planning, run_chat_notify (D10/D11)
src/mihomes/services/chat/push.py                allowlist (D6), payload (D7), WebPushSender, subscribe/unsubscribe, VAPID keygen
src/mihomes/services/chat/ratelimit.py           SlidingWindow + POSTS / REACTIONS limiters (D8)
src/mihomes/services/chat/streams.py             per-membership / process stream counters (D9)
src/mihomes/web/routes/pwa.py                    GET /sw.js, GET /manifest.webmanifest (D12)
src/mihomes/web/pwa/sw.js                        push + notificationclick only — no fetch handler (N4)
src/mihomes/web/pwa/manifest.webmanifest         name, start_url "/chat/", display "standalone", icons
src/mihomes/web/static/icons/icon-192.png, icon-512.png, apple-touch-icon.png
src/mihomes/web/templates/settings/_chat_notifications.html   toggles + iPhone note + data-vapid-key
src/mihomes/services/email/templates/chat_digest.html + .txt  ** both, or render raises (render.py:41-60) **
scripts/chat_sse_load.py                         Step 9's 50-client check
docs/deploy/CHAT-NOTIFICATIONS.md                VAPID env, MIHOMES_BASE_URL, crontab lines for evo-dev (U1)
```

### Modified

| Path | Change |
|---|---|
| `src/mihomes/models/chat_native.py` (SPEC-012) | `ChatParticipant.last_notified_seq`, mirroring 0028. `test_baseline_matches_metadata` compares the model to the schema |
| `src/mihomes/models/__init__.py` | import `chat_notify` |
| `src/mihomes/web/app.py` | `app.include_router(pwa_route.router)`. Without it the routes do not exist, and the allowlist entry fails `test_allowlists_name_only_real_modules` |
| `src/mihomes/tenancy/registry.py` | `push_subscriptions`, `chat_notify_prefs` → `TENANT_TABLES` |
| `src/mihomes/authz/actions.py` `_entity_classes` | both models → `ACCOUNT_SHARED`, the same class as SPEC-012's chat models. The row filter is `membership_id == principal.membership_id` in the service, because `ACCOUNT_LEVEL` would deny staff their **own** row (`query_scope.py:352-365`) |
| `src/mihomes/services/chat/posts.py`, `reactions.py` (SPEC-012) | Rate-limit hooks only. The 4000-character limit already exists as SPEC-012's `posts.MAX_MEMBER_BODY` (service check in `create_post` / `edit_post` + the `ck_chat_posts_body_len` CHECK); this spec adds no second constant |
| `src/mihomes/web/routes/chat.py` (SPEC-012) | limiter calls on post, edit and react; stream wrapped in `stream_slots`; the four routes in §5.4 |
| `src/mihomes/web/static/chat.js` (SPEC-012) | B2; hidden-tab close/reopen (D9); push opt-in (`Notification.requestPermission` on a click, `pushManager.subscribe`, POST) |
| `src/mihomes/web/templates/settings/_personal.html` | `<div hx-get="/chat/notifications" hx-trigger="load">` |
| `src/mihomes/web/templates/settings/index.html` | B1 |
| `src/mihomes/web/templates/base.html` | `<link rel="manifest">`, `apple-touch-icon`, `theme-color` |
| `src/mihomes/services/email/service.py` | `send_chat_digest(...)`, `klass="lifecycle"` |
| `src/mihomes/cli/jobs.py` | `chat-notify` command and `SCHEDULE["chat-notify"] = ("*/5 * * * *", …)` |
| `pyproject.toml` | `"pywebpush>=2.5"`, with a comment on the cryptography bump (D14) |
| `tests/unit/test_route_declarations.py` | `PERMANENT_ALLOWLIST` + `ALLOWLIST_MECHANISMS` entries for `mihomes.web.routes.pwa` |
| `tests/unit/test_tenancy_registry.py:276`, `tests/integration/test_isolation.py:90` | SPEC-012's value **+2** (expected 64) |
| `tests/integration/test_pg_baseline.py:154` | B3 |
| `docs/UI_MANUAL_CHECKLIST.md` | rows: push on Android/desktop/iPhone PWA, 429 toast keeps text, 7th tab paused |

---

## 4. Schemas as code

### 4.1 `models/chat_notify.py`

```python
class PushSubscription(Base, TenantOwned):
    """One browser's push endpoint for one membership (D5). Written only by `push.subscribe`,
    always keyed on the *caller's* membership; there is no route that names another member."""
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)          # https + allowlisted host (D6)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)     # base64url, 65-byte point
    auth: Mapped[str] = mapped_column(String(100), nullable=False)       # base64url, 16 bytes
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)  # for "this device" UI
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("membership_id", "endpoint", name="uq_push_subscriptions_member_endpoint"),
    )


class ChatNotifyPref(Base, TenantOwned):
    """Per-membership switches. **No row means defaults** (both on), so the job outer-joins."""
    __tablename__ = "chat_notify_prefs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    email_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    push: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # D2: the created_at cutoff the last digest covered — NOT when it was sent.
    digest_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

`ChatParticipant` (SPEC-012) gains `last_notified_seq: Mapped[int] = mapped_column(BigInteger,
nullable=False, server_default="0")`, using the same type as its `last_read_seq`.

### 4.2 `alembic/versions/0028_chat_notifications.py`

```python
revision: str = "0028_chat_notifications"
down_revision: Union[str, None] = "0027_native_chat"

_TABLES = ("push_subscriptions", "chat_notify_prefs")

def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=200), nullable=False),
        sa.Column("auth", sa.String(length=100), nullable=False),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["membership_id"], ["memberships.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("membership_id", "endpoint", name="uq_push_subscriptions_member_endpoint"),
    )
    op.create_index(op.f("ix_push_subscriptions_account_id"), "push_subscriptions", ["account_id"])
    op.create_index(op.f("ix_push_subscriptions_membership_id"), "push_subscriptions", ["membership_id"])

    op.create_table(
        "chat_notify_prefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_digest", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("push", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("digest_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["membership_id"], ["memberships.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_notify_prefs_account_id"), "chat_notify_prefs", ["account_id"])
    op.create_index(op.f("ix_chat_notify_prefs_membership_id"), "chat_notify_prefs",
                    ["membership_id"], unique=True)

    op.add_column("chat_participants", sa.Column(
        "last_notified_seq", sa.BigInteger(), server_default="0", nullable=False))

    if op.get_bind().dialect.name == "postgresql":          # the 0026 pattern, 0026:49-57
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements
        for table in _TABLES:
            for stmt in policy_statements(table):
                op.execute(stmt)
        # membership_id -> memberships is a tenant->tenant link: guarded (drift_guard.py:118-137)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables=set(_TABLES)):
            op.execute(stmt)

def downgrade() -> None:
    op.drop_column("chat_participants", "last_notified_seq")
    op.drop_index(op.f("ix_chat_notify_prefs_membership_id"), table_name="chat_notify_prefs")
    op.drop_index(op.f("ix_chat_notify_prefs_account_id"), table_name="chat_notify_prefs")
    op.drop_table("chat_notify_prefs")
    op.drop_index(op.f("ix_push_subscriptions_membership_id"), table_name="push_subscriptions")
    op.drop_index(op.f("ix_push_subscriptions_account_id"), table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
```

There are no new enums, so `test_pg_baseline.py:155`'s enum count is unchanged. The policies and
triggers go with `drop_table`, as in 0026's downgrade (`0026:60-63`).

---

## 5. Function signatures

### 5.1 `services/chat/notify.py`

```python
NOTIFY_AFTER = timedelta(minutes=10)          # D1(c)
NOTIFY_HORIZON = timedelta(hours=48)          # D1(c) — no backlog blast on first deploy
DIGEST_INTERVAL = timedelta(minutes=60)       # O2
EVENTS_RETENTION = timedelta(days=7)          # D11 — SPEC-012's catch-up window

@dataclass(frozen=True)
class Due:
    membership_id: uuid.UUID
    conversation_id: uuid.UUID
    post_id: uuid.UUID
    seq: int
    created_at: datetime
    reason: Literal["dm", "mention"]
    pushed: bool                       # already past the push marker (D2)

def due_items(session: Session, *, now: datetime) -> list[Due]:
    """The SQL half of D1: (a)–(e) and the participant mute in (g). (f) and the prefs half
    of (g) are `_deliverable`, because visibility is a Python function SPEC-012 owns."""
    lo, hi = now - NOTIFY_HORIZON, now - NOTIFY_AFTER
    P, C, Part, M = ChatPost, ChatConversation, ChatParticipant, ChatMention
    window = (P.deleted_at.is_(None), P.created_at > lo, P.created_at <= hi)

    dms = (
        select(Part.membership_id, P.conversation_id, P.id, P.seq, P.created_at,
               literal("dm"), P.seq <= Part.last_notified_seq)
        .join(C, C.id == P.conversation_id)
        .join(Part, Part.conversation_id == P.conversation_id)
        .where(C.kind == "dm", *window,
               P.seq > Part.last_read_seq,
               P.author_membership_id.is_distinct_from(Part.membership_id),
               Part.muted.is_(False))
    )
    mp = aliased(ChatParticipant)          # groups create participant rows lazily → outer join
    mentions = (
        select(M.membership_id, P.conversation_id, P.id, P.seq, P.created_at,
               literal("mention"), M.notified_at.is_not(None))
        .join(P, P.id == M.post_id)
        .outerjoin(mp, and_(mp.conversation_id == P.conversation_id,
                            mp.membership_id == M.membership_id))
        .where(*window,
               P.seq > func.coalesce(mp.last_read_seq, 0),
               P.author_membership_id.is_distinct_from(M.membership_id),
               func.coalesce(mp.muted, false()).is_(False))
    )
    rows = session.execute(union_all(dms, mentions)).all()
    # A mention inside a DM shows up twice. Keep one per (member, post), preferring the
    # DM row, whose marker is the seq.
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r[5] != "dm"):
        if (r[0], r[2]) not in seen:
            seen.add((r[0], r[2]))
            out.append(Due(*r))
    return out

def _deliverable(session: Session, items: list[Due], *, now: datetime
                 ) -> dict[uuid.UUID, tuple[Membership, ChatNotifyPref | None, list[Due]]]:
    """D1(f)/(g). For each membership: load it, and drop it if it is not `active`.
    Compute `visible_conversation_ids(session, m)` once and keep only items inside it.
    Then drop it if `prefs.muted_until > now`. **No cache across members or runs**: scope
    changes between runs are exactly what this re-check exists to catch (§0.2)."""

@dataclass
class AccountPlan:
    pushes: list[tuple[uuid.UUID, dict]]           # (membership_id, payload), one per conversation
    digests: list[tuple[str, dict]]                # (address, template context)
    mention_ids: list[uuid.UUID]                   # → notified_at = now
    dm_marks: dict[tuple[uuid.UUID, uuid.UUID], int]   # (conv, member) → last_notified_seq
    digest_marks: list[tuple[uuid.UUID, datetime]]     # (member, new digest_through)

def plan_account(session: Session, *, now: datetime) -> AccountPlan:
    """D2. **Reads only.** Push: items with `pushed=False` for members with `prefs.push`.
    Every un-pushed item is marked, even for members with push off or no subscription, so a
    device added later gets no burst of old items. Digest: members with
    `prefs.email_digest`, when `digest_through + DIGEST_INTERVAL ≤ now - NOTIFY_AFTER` (or it
    is NULL) and at least one item has `created_at > digest_through`. The context is
    [{title, count}] per conversation plus a link. `AccountPlan.digest_marks` lists the
    (membership_id, now - NOTIFY_AFTER) pairs to write."""

def run_account(session: Session, *, now: datetime, email: EmailService
                ) -> list[tuple[uuid.UUID, dict]]:
    """D11, steps 1–5. Returns the pushes to send after the caller commits."""
    got = session.execute(select(func.pg_try_advisory_xact_lock(
        func.hashtext(f"chat-notify:{require_account()}")))).scalar()
    if not got:
        return []                                   # another run holds this account
    plan = plan_account(session, now=now)
    if plan.mention_ids:
        session.execute(update(ChatMention).where(ChatMention.id.in_(plan.mention_ids))
                        .values(notified_at=now))
    for (conv, member), seq in plan.dm_marks.items():
        session.execute(update(ChatParticipant)
                        .where(ChatParticipant.conversation_id == conv,
                               ChatParticipant.membership_id == member,
                               ChatParticipant.last_notified_seq < seq)
                        .values(last_notified_seq=seq))
    for member, through in plan.digest_marks:          # all writes here, before one commit
        session.execute(pg_insert(ChatNotifyPref)
                        .values(id=new_id(), account_id=require_account(),
                                membership_id=member, digest_through=through)
                        .on_conflict_do_update(index_elements=["membership_id"],
                                               set_={"digest_through": through}))
    for address, ctx in plan.digests:
        email.send_chat_digest(address, **ctx)          # enqueue only (§0.3)
    session.execute(delete(ChatEvent).where(ChatEvent.created_at < now - EVENTS_RETENTION))
    return plan.pushes

def run_chat_notify(session_factory, *, push_sender: "PushSender | None",
                    email_factory: Callable[[Session], EmailService], now: datetime
                    ) -> NotifyResult:
    """D10. Read the account ids with no context bound (`accounts` has no RLS,
    `cli/jobs.py:484-498`). Then, per account, **both phases stay inside one
    `account_context(id)`**:
        with account_context(id):
            with session_factory() as s:  pushes = run_account(s, ...); s.commit()
            with session_factory() as s:  send_pushes(s, pushes, push_sender); s.commit()
    A push session outside the context would find no subscriptions under RLS and send
    nothing, and superuser tests would stay green. Errors are recorded per account and the
    sweep continues (`outbox.py:290-296`)."""
```

### 5.2 `EmailService.send_chat_digest`

```python
def send_chat_digest(self, to: str, *, estate: str, conversations: list[dict],
                     chat_url: str, settings_url: str) -> None:
    """`conversations` = [{"title": str, "count": int}] — D3/O1: no names, no text.
    A DM's title is "Direct messages", aggregated. Lifecycle: suppressible, and it carries
    RFC 8058 headers at drain time."""
    self._send(to, "chat_digest", {...}, klass="lifecycle")
```

### 5.3 `services/chat/push.py`

```python
PUSH_HOSTS = frozenset({"fcm.googleapis.com", "updates.push.services.mozilla.com",
                        "web.push.apple.com"})
PUSH_HOST_SUFFIXES = (".notify.windows.com",)
PUSH_TTL_SECONDS = 6 * 3600       # a push older than this is noise; let the digest carry it
MAX_SUBSCRIPTIONS_PER_MEMBER = 10 # oldest evicted on subscribe

def endpoint_allowed(endpoint: str) -> bool:
    """D6. `urlsplit`: scheme == "https", no userinfo, no explicit port, and the host is
    exactly in PUSH_HOSTS or ends with a suffix. Re-checked at send time, so tightening the
    list takes effect without a migration."""

def build_payload(conversation: ChatConversation, reason: str) -> dict:
    """D7/O3. {"title": conv.title if kind != "dm" else "Direct message",
    "body": "You were mentioned" if reason == "mention" else "New message",
    "url": f"/chat/c/{conv.id}", "tag": f"chat-{conv.id}"}. Never reads ChatPost.body."""

def subscribe(session: Session, *, membership_id: uuid.UUID, endpoint: str, p256dh: str,
              auth: str, user_agent: str | None) -> None:
    """Upsert on (membership_id, endpoint) with `pg_insert(...).on_conflict_do_update`.
    Raises ValueError when `endpoint_allowed` is false or a key fails base64url-decoding to
    65 / 16 bytes."""

def unsubscribe(session: Session, *, membership_id: uuid.UUID, endpoint: str) -> int:
    """DELETE … WHERE membership_id = :caller AND endpoint = :e. It cannot reach another
    member's row: the predicate includes the caller."""

class PushSender(Protocol):
    def send(self, sub: PushSubscription, payload: dict) -> int: ...   # HTTP status, 0 = network

class WebPushSender:
    def __init__(self, *, private_key: str, subject: str, timeout: float = 10.0) -> None:
        # Parse once. A str key makes pywebpush call os.path.isfile and then Vapid.from_string
        # on every send (pywebpush 2.5.0 __init__.py:497-510).
        self._key = Vapid.from_string(private_key)
        self._subject, self._timeout = subject, timeout
    def send(self, sub: PushSubscription, payload: dict) -> int:
        try:
            webpush(subscription_info={"endpoint": sub.endpoint,
                                       "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                    data=json.dumps(payload), vapid_private_key=self._key,
                    # **A fresh dict per call.** pywebpush writes `aud` into the dict you pass
                    # (2.5.0 __init__.py:480-490) and only when it is absent, so a dict reused
                    # across services signs Mozilla's push with Google's audience: 403 everywhere
                    # but the first host.
                    vapid_claims={"sub": self._subject},
                    ttl=PUSH_TTL_SECONDS, timeout=self._timeout)
            return 201
        except WebPushException as exc:
            return exc.response.status_code if exc.response is not None else 0

def sender_from_env() -> WebPushSender | None:
    """None, logged once, when VAPID_PRIVATE_KEY or VAPID_SUBJECT is unset (D14)."""

def send_pushes(session: Session, pushes: list[tuple[uuid.UUID, dict]],
                sender: PushSender | None) -> PushResult:
    """For each payload, send to every subscription of that member whose endpoint is still
    allowed. 2xx → `last_success_at = now`. **404/410 → DELETE the row**, because the
    browser has revoked it and every later send would fail the same way. 429/5xx/0 → keep
    it; push is at-most-once (D11), so there is no retry."""

def generate_vapid_keys() -> tuple[str, str]:
    """(public, private) with `cryptography`: ec.generate_private_key(ec.SECP256R1()).
    public  = base64url(uncompressed X9.62 point, 65 bytes): the browser's applicationServerKey.
    private = base64url(raw 32-byte scalar). py-vapid 1.9.6 `Vapid.from_string` treats a
    32-byte decode as raw (py_vapid/__init__.py:144-147), so no PEM file is needed.
    Run: python -c "from mihomes.services.chat.push import generate_vapid_keys as g; print(*g(), sep='\\n')"
    """
```

Env: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` (a secret), and `VAPID_SUBJECT` (`mailto:` an
operator address). **Rotating the key pair invalidates every subscription**, because the browser
pinned the public key at subscribe time. Browsers then return 403, not 410, so the rows survive.
Rotation is a runbook step that also deletes the table's rows (`docs/deploy/CHAT-NOTIFICATIONS.md`).

### 5.4 Routes — all `@declares("chat.use", Access.ACCOUNT)` in `web/routes/chat.py`

```python
@router.get("/notifications", response_class=HTMLResponse)   # the D13 partial
@router.post("/notifications")       # email_digest, push, mute_hours ∈ {0,1,8,24} → upsert own row
@router.post("/push/subscribe")      # JSON PushSubscription.toJSON(); 204, or 422 on D6/key failure
@router.post("/push/unsubscribe")    # JSON {"endpoint"}; 204 whether or not a row matched
```

Every one of these reads `principal.membership_id` and accepts no membership id from the request.
That is the `gateway.link_self` mechanism (`routes/settings.py:44-58`) under the chat key.

### 5.5 `web/routes/pwa.py` — no declaration (D12)

```python
@router.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(PWA_DIR / "sw.js", media_type="text/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(PWA_DIR / "manifest.webmanifest", media_type="application/manifest+json",
                        headers={"Cache-Control": "public, max-age=3600"})
```

Undeclared routes are passed through by `enforce_declared_action` (`web/app.py:100-104`). The
allowlist entry gives the reason, and the mechanism is *"nothing — fixed bytes, no input, no
data"*, the same shape as `mihomes.web.errors`.

`sw.js` handles exactly two events. `push`: **always** `showNotification(title, {body, tag,
data: {url}})`, because Chrome and Safari require a visible notification for every push
(`userVisibleOnly`). `notificationclick`: focus an existing client and navigate it to `data.url`,
or `openWindow`, and **only for a same-origin URL**.

### 5.6 `services/chat/ratelimit.py` and `streams.py`

```python
class SlidingWindow:
    def __init__(self, limit: int, window_s: float, *, max_keys: int = 10_000,
                 clock=time.monotonic) -> None: ...
    def hit(self, key: str) -> float | None:
        """Record one event. None = allowed. Otherwise the seconds until a slot frees, for
        `Retry-After`. A refused hit is not recorded, so a client hammering the limit does not
        lock itself out longer. Keys are LRU-bounded (`landing/ratelimit.py:12-13` reasoning)."""
    def reset(self) -> None: ...                      # test support, like auth.ratelimit.reset_all

POSTS = SlidingWindow(20, 60.0)        # key = str(membership_id); post + edit share it
REACTIONS = SlidingWindow(10, 10.0)
# The 4000-char body limit is SPEC-012's posts.MAX_MEMBER_BODY (+ DB CHECK) — not redefined here.

MAX_STREAMS_PER_MEMBER = 6
MAX_STREAMS_PER_PROCESS = 200

@contextmanager
def stream_slots(membership_id: uuid.UUID) -> Iterator[bool]:
    """Yield False when over either cap; the route then returns 429 without opening the
    generator. The release is in `finally`, so a client disconnect frees the slot."""
```

The route returns `PlainTextResponse("You're sending messages quickly — wait a few seconds.",
status_code=429, headers={"Retry-After": str(ceil(wait))})`, which is all of D8.

---

## 6. Sequenced steps

Each step is one commit. Its verification runs on the private DB
(`TEST_DATABASE_URL=…/mihomes_test_rtb`) and compares against the 7 known failures.

**Step 1 — schema.** `chat_notify.py`, 0028, the `ChatParticipant` column, registry, entity
classes, count pins, and B3. *Verify:* `PYTHONPATH=src alembic upgrade head && alembic downgrade
-1 && alembic upgrade head` on the scratch DB, then `pytest tests/integration/test_migration_chat_notifications.py
tests/unit/test_tenancy_registry.py tests/integration/test_isolation.py tests/integration/test_pg_baseline.py
tests/unit/test_uuid_pks.py` → A1, A2.

**Step 2 — the rule.** `notify.due_items`, `_deliverable` and `plan_account`, with no sending.
*Verify:* `pytest tests/integration/test_chat_notify.py tests/integration/test_chat_notify_access.py` → A3–A15.

**Step 3 — digest email.** `send_chat_digest`, both templates, and the `chat-notify` command plus
its `SCHEDULE` entry. *Verify:* `pytest tests/integration/test_chat_digest_email.py
tests/unit/test_jobs_enumeration.py`, then `mihomes jobs chat-notify` run twice with no output
change (A16–A19).

**Step 4 — push.** `pyproject.toml` (D14) and `push.py`. *Verify:* `pip install -e ".[dev]" &&
pytest tests/unit/test_chat_push.py tests/integration/test_chat_push_send.py tests/unit/test_passwords.py`
(the cryptography bump),
→ A20–A23.

**Step 5 — PWA + subscribe routes + Settings.** `pwa.py`, `sw.js`, the manifest, icons, the four
chat routes, the partial, B1, the `base.html` links, `npm run build:css && npm run stamp`.
*Verify:* `pytest tests/integration/test_pwa_routes.py tests/integration/test_chat_push_routes.py
tests/integration/test_chat_notify_prefs.py tests/unit/test_route_declarations.py` → A24–A28.

**Step 6 — rate limits.** `ratelimit.py`, wired into post, edit and react; A31 exercises SPEC-012's `MAX_MEMBER_BODY`; B2.
*Verify:* `pytest tests/unit/test_chat_ratelimit.py tests/integration/test_chat_ratelimit.py` → A29–A31.

**Step 7 — Origin.** No code change is expected. *Verify:* `pytest tests/integration/test_chat_origin.py`
→ A32. If it fails, fix the request path, never the guard.

**Step 8 — stream caps.** `streams.py`, `stream_slots` in `/chat/stream`, and hidden-tab close in
`chat.js`. *Verify:* `pytest tests/integration/test_chat_stream_limits.py` → A33.

**Step 9 — evo-dev deploy + load check.** Requires the user's go-ahead. Back up first
(`TELEGRAM-WEBHOOK.md` §2 recipe), then add the VAPID env and `MIHOMES_BASE_URL` to `web.env`
and run `alembic upgrade head` and `boot.sh`. Install two crontab lines with the `mapfile` /
`env -i` pattern: `*/5 … mihomes jobs chat-notify` **and** `*/5 … mihomes jobs drain-outbox`.
Without the second, digests queue forever (§0.3, U1). Then, from a tailnet machine, run
`python scripts/chat_sse_load.py --base https://evo-dev.tail6765ef.ts.net --cookies cookies.txt
--clients 50 --minutes 10`. **Pass (A34), all of these:**

1. All 50 streams return `200 text/event-stream` within 10s.
2. A post made by the script reaches **every** stream, p95 ≤ 2s and max ≤ 5s.
3. Zero disconnects over 10 idle minutes. The heartbeat keeps the stream alive through Tailscale
   Serve's idle timeout (unknown, measured here).
4. While the streams are open, 20 `GET /chat/` requests have p95 ≤ 1.5s.
5. No `QueuePool limit … timed out` appears in the app log. `db.py:83` uses default pool sizing
   (5+10), so a stream that holds a connection fails this at 16 clients.
6. `pg_stat_activity` for the app role stays ≤ 15.
7. App RSS grows < 100 MB, and the stream counter reads 0 after the script exits.

The script also records the negotiated HTTP version (`httpx` with `http2=True`). That settles U5.
Then do the manual checklist rows: Android Chrome, desktop, and iPhone as a home-screen app.

---

## 7. Non-goals + deferred

**N1 — Do not add presence**, last-seen, or "is typing" persistence to decide "away". §0.1. The
user excluded it, and a notification rule built on it would need it to be correct.

**N2 — Do not put message text, sender names or attachment names in a push payload or a digest.**
D3/D7. O1 may widen the digest. Nothing widens the lock screen.

**N3 — Do not send push from the request path.** D4. A second sender would need its own
visibility check, and two checks drift.

**N4 — Do not give `sw.js` a `fetch` handler or Cache Storage.** Caching authenticated chat pages
on the device keeps tenant data there after sign-out or revocation, readable offline by whoever
holds the phone. Offline chat is not in scope.

**N5 — Do not make the digest `transactional`.** It would ignore unsubscribes, a CAN-SPAM problem
(`service.py:60-63`). SPEC-010 D8's reasoning for reset mail does not transfer, because nobody is
locked out of anything by missing a digest.

**N6 — Do not key rate limits on IP.** D8. On evo-dev every member shares Tailscale's address.

**N7 — Do not drain the outbox from chat-notify.** `drain-outbox` is the only provider path
(SPEC-005 D12/N2). Worst-case digest latency is 10 + 5 + 5 minutes, which suits a digest.

**N8 — Do not set `Referrer-Policy: no-referrer` app-wide.** Under it, browsers send `Origin: null`
on same-origin POSTs. `security.py:114-119` would then 403 every chat POST, because "null" is
not an allowed host. A32 pins the working state.

**Deferred:** per-conversation notification levels; chat quiet hours; a shared store for limits (U4).

---

## 8. Acceptance criteria

Criteria marked **[app role]** run on `app_engine` (`tests/conftest.py:212`), not as superuser.
**Every [app role] negative has a positive control in the same run**: an active member who can
see the conversation and **is** notified. If the job read nothing under RLS (context unbound, a
missing grant), "X was not notified" would pass anyway. The control is what makes it fail.

| # | Criterion | Test |
|---|---|---|
| A1 | 0028 upgrades, downgrades and upgrades again. Both tables have `<table>_tenant` RLS policies and the `membership_id` drift trigger | `test_migration_chat_notifications.py::test_up_down_up` / `::test_policies_and_guards` |
| A2 | Tenant count = SPEC-012's + 2; `pg_baseline` re-measured (B3) | `test_tenancy_registry.py::test_registry_size_is_asserted_explicitly`, `test_isolation.py::test_registry_covers_every_tenant_table`, `test_pg_baseline.py::test_upgrade_then_downgrade_is_clean` |
| A3 | **[app role]** Account B's session sees no row of A's `push_subscriptions` or `chat_notify_prefs` | `test_chat_notify_access.py::test_tables_isolated_by_account` |
| A4 | An unread DM older than 10 min yields one push payload and one digest entry for the recipient only | `test_chat_notify.py::test_unread_dm_notified` |
| A5 | A post younger than 10 min is not due. One older than 48h is never due | `test_chat_notify.py::test_window_10min_to_48h` |
| A6 | **A read post is not notified** (`last_read_seq ≥ seq`) | `test_chat_notify.py::test_read_post_not_notified` |
| A7 | **The author's own post is not notified to the author** | `test_chat_notify.py::test_own_post_not_notified` |
| A8 | **Muted is not notified**, both by `chat_participants.muted` and by `muted_until > now` | `test_chat_notify.py::test_muted_not_notified` |
| A9 | A deleted post is not notified | `test_chat_notify.py::test_deleted_post_not_notified` |
| A10 | **Notified once only.** A second run pushes nothing, and a second digest needs a new item **and** the interval | `test_chat_notify.py::test_notified_once_only` |
| A11 | An item that turns 10 min old just after a digest goes into the next digest and is not lost (D2) | `test_chat_notify.py::test_digest_through_is_a_cutoff_not_a_send_time` |
| A12 | Two concurrent runs on one account send each push once (advisory lock) | `test_chat_notify.py::test_concurrent_runs_do_not_double_notify` |
| A13 | **[app role]** **A revoked member is not notified**, whether the revocation came before or after the post | `test_chat_notify_access.py::test_revoked_member_not_notified` |
| A14 | **[app role]** **A mention of a member who cannot see the conversation is never notified.** The row is inserted directly, as if scope narrowed after SPEC-013 wrote it | `test_chat_notify_access.py::test_mention_of_non_visible_member_never_notified` |
| A15 | `chat_events` older than 7 days are pruned; newer ones are kept | `test_chat_notify.py::test_prunes_events_older_than_7_days` |
| A16 | **A suppressed address gets no digest**: no outbox row at enqueue, and a row suppressed later is skipped at drain | `test_chat_digest_email.py::test_suppressed_address_skipped` |
| A17 | The digest is lifecycle, drains with `List-Unsubscribe`, and links via `MIHOMES_BASE_URL` | `test_chat_digest_email.py::test_digest_is_lifecycle_with_unsubscribe` |
| A18 | **The rendered digest (.html and .txt) contains no post body, sender name or attachment name** | `test_chat_digest_email.py::test_digest_contains_no_message_text` |
| A19 | `chat-notify` is registered, in `SCHEDULE` at `*/5`, and idempotent across two invocations | `test_jobs_enumeration.py::test_all_workloads_scheduled`, `test_chat_digest_email.py::test_job_twice_is_noop` |
| A20 | **A push 410 or 404 deletes that subscription.** A 429 or 500 keeps it | `test_chat_push_send.py::test_gone_subscription_deleted` |
| A21 | **The push payload has no body text**: the post's body never appears in the JSON, and a DM title is "Direct message" | `test_chat_push.py::test_payload_has_no_body_text` |
| A22 | Each send gets a fresh `vapid_claims` dict, so the `aud` of one host never leaks to another | `test_chat_push.py::test_fresh_vapid_claims_per_send` |
| A23 | With no VAPID env, the job runs, sends digests, sends no push, and does not raise | `test_chat_push_send.py::test_no_vapid_disables_push_only` |
| A24 | **`/sw.js` is served at the root, unauthenticated**, as `text/javascript` with `Service-Worker-Allowed: /` and `Cache-Control: no-cache`. The manifest is `application/manifest+json` with `start_url` `/chat/` | `test_pwa_routes.py::test_sw_js_scope_header` / `::test_manifest_public` |
| A25 | `routes.pwa` is in `PERMANENT_ALLOWLIST` **and** `ALLOWLIST_MECHANISMS` | `test_route_declarations.py::test_every_allowlisted_module_names_its_mechanism` |
| A26 | **Subscribe refuses http, a non-allowlisted host, a port, or malformed keys** with 422 and no row (SSRF, D6) | `test_chat_push_routes.py::test_subscribe_rejects_unknown_push_host` |
| A27 | **[app role]** Unsubscribe removes only the caller's row, even when another member registered the same endpoint (D5) | `test_chat_push_routes.py::test_unsubscribe_only_own` |
| A28 | Staff can open and save their own preferences. The POST writes only `principal.membership_id` | `test_chat_notify_prefs.py::test_staff_can_save_own_prefs` |
| A29 | **The 21st post in 60s returns 429 with `Retry-After`, plain text and no `HX-Trigger`. No post row is written, and the response carries no partial, so htmx leaves the composer as typed** | `test_chat_ratelimit.py::test_post_over_limit_429_keeps_composer` |
| A30 | The 11th reaction in 10s returns 429. Limits are per membership, so another member is unaffected | `test_chat_ratelimit.py::test_reaction_limit_per_membership` |
| A31 | A 4001-character body is refused (422), and exactly 4000 is accepted | `test_chat_ratelimit.py::test_body_limit_4000` |
| A32 | A chat POST with a foreign `Origin` or `Sec-Fetch-Site: cross-site` gets 403. A same-origin HTMX POST (`HX-Request`, `Origin` = host) passes | `test_chat_origin.py::test_cross_origin_post_blocked` / `::test_htmx_post_with_origin_allowed` |
| A33 | A 7th concurrent stream for one membership gets 429, and closing one frees a slot | `test_chat_stream_limits.py::test_per_membership_stream_cap` |
| A34 | **The 50-client load check passes all seven Step 9 criteria on evo-dev** | manual: `scripts/chat_sse_load.py`, results pasted in the deploy commit message |
| A35 | **[app role]** `run_chat_notify` end to end, with `session_factory` on `app_engine`, delivers a push to `FakePushSender` and leaves one digest row in `email_outbox` | `test_chat_notify_access.py::test_end_to_end_under_app_role` |

**A14 is this stage's definition of done.** When it fails, someone gets a notification for a
conversation they can no longer see, and it looks exactly like a correct one. That is the chat
analogue of SPEC-010 A2.

**A29 is the one most likely to regress silently.** Its "keeps the composer" half lives in
SPEC-012's JavaScript (B2), so it also has a manual checklist row.

---

## 9. Test manifest

```
tests/integration/test_migration_chat_notifications.py   A1 — own engine, real Alembic, scratch DB
tests/integration/test_chat_notify.py                    A4–A12, A15 — job core, injected fakes
tests/integration/test_chat_notify_access.py             A3, A13, A14, A35 — app_engine ONLY, positive controls
tests/integration/test_chat_digest_email.py              A16–A19 — console provider, real outbox
tests/unit/test_chat_push.py                             A21, A22 — payload + claims; `webpush` patched at push.py's import
tests/integration/test_chat_push_send.py                 A20, A23 — send_pushes deletes rows; job with no VAPID
tests/integration/test_chat_push_routes.py               A26, A27
tests/integration/test_chat_notify_prefs.py              A28
tests/integration/test_pwa_routes.py                     A24
tests/unit/test_chat_ratelimit.py                        SlidingWindow with an injected clock — no sleeps
tests/integration/test_chat_ratelimit.py                 A29–A31
tests/integration/test_chat_origin.py                    A32
tests/integration/test_chat_stream_limits.py             A33
(modified) test_route_declarations, test_tenancy_registry, test_isolation, test_pg_baseline, test_jobs_enumeration   A2, A19, A25
```

Conventions:

- **Inject `now`; no test sleeps** (`outbox.py:136-137`). The 10-minute and 48-hour edges are
  tested at ±1s.
- **Fake at the seam** (`PushSender`). Seed with Core inserts (`test_auth.py:132` `_grant`).
  Clean up committed rows by account, including both new tables (`test_gateway_webhook.py:285-310`).
- **A18 and A21 grep for a sentinel.** The seeded body is a unique token, and the test asserts
  it is absent. Checking only what is present would pass output that also leaked the body.

---

## 10. What this stage does not make safe

- **Sent email cannot be recalled.** A digest sent before a revocation stays in that inbox. D3
  limits what it contains; nothing limits how long it is kept.
- **A shared or lost device keeps receiving push titles** until its subscription is removed.
  Sign-out does not unsubscribe a browser the server cannot reach. "Sign out everywhere" should
  delete the user's subscriptions; that is a follow-up, not built here.
- **Unsubscribing from a digest unsubscribes the address from all lifecycle mail.** Suppression is
  per address and global (§0.3). The in-app toggle is the precise control, and the email says so.
- **Rate limits and stream caps are per process (U4).** With N instances the effective limit is N
  times the configured one, and a restart clears the counters. That is the same caveat as
  SPEC-010 U5.
- **Push delivery is best-effort.** It is at-most-once (D11), with no retry and no delivery
  receipt. The digest is the channel that is guaranteed.
- **iPhone push works only from a home-screen web app on iOS/iPadOS 16.4+**, and on evo-dev only
  with the phone on the tailnet. The Settings note says both (U2).

**Unverified gates carried forward:**
**U1** — nothing in `scripts/` or `docs/deploy/` schedules cron on evo-dev. `start-mihomes.sh`
is the old Docker demo entry, and `watchdog.py` supervises only the messaging monitors. Until
Step 9's crontab lines survive a rebuild, neither chat-notify nor drain-outbox runs.
**U2** — iPhone PWA push has been verified only by the manual checklist.
**U3** — `PUSH_HOSTS` reflects current browsers. A refused host is logged with its 422.
**U4** — limits are in-process. **U5** — whether Tailscale Serve uses HTTP/2 is unknown until Step 9.
