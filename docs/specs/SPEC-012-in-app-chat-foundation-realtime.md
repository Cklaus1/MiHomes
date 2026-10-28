# SPEC-012 — In-app chat: foundation + realtime

**Phase:** 4+ — the first of three chat specs (SPEC-013: AI, attachments, mentions, search; SPEC-014: notifications + hardening)
**Status:** Ready to build — **2 open decisions** (O1: the reaction emoji set; O2: an edit time window). Neither blocks a step
**Written:** 2026-10-28
**Verified against:** `worktree-chat-specs` @ **`9c7d595`** (= the `worktree-spec-build-harness` tip at branch time). FastAPI **0.159.1**, Starlette **1.9.0**, SQLAlchemy **2.1.6**, measured in the harness venv. **Not `origin/main`** (SPEC-010 §0.5 still holds)
**Source:** the approved plan `plan-the-native-in-app-validated-dolphin.md`, Phases 0–3. There is no chat PRD. The plan is the product intent, and §1 records the user's scope decisions as locked
**Depends on:** SPEC-002 (tenancy, RLS, drift guard), SPEC-003 (memberships, `scoped_property_ids`, the matrix), SPEC-009 (compiled CSS, the manual checklist)

**Goal.** Members message each other inside MiHomes: one estate-wide group ("General"), one group per
property, and two-person DMs, live, with typing, reactions, unread counts, edit/delete and replies.

**Exit criterion:** owner and a staff member scoped to one property, two browsers. The owner posts in that
property's chat; the staff member sees it in under two seconds without reloading, then reacts, replies and
edits, and the owner sees each change live. The staff member can never open, list, stream or count a
conversation outside their visible set — proven under the **non-superuser** app role.

**The stake.** Every other page answers "may this role see this row?" with a role and a property. Chat needs a
third input — **"is this person a participant?"** — that no existing layer can express (§0.1). Skip the one
function that answers it on a single read path and a housekeeper reads the owner's DM with the estate
manager. Nothing errors; nobody notices.

---

## 0. Seven things a reader must know before trusting this spec

### 0.1 — The query layer will not filter chat rows, so one service function must

`chat.use` is `ALLOW` for every role (D2), and `enforce_declared_action` binds a property scope **only** for a
`SCOPED` grant (`web/deps.py:348-351`). So on chat requests `current_property_scope` is `None` even for staff,
and `_apply_property_scope` applies only the `PERSONNEL` filter (`authz/query_scope.py:424-425`). **On a chat
route a staff member's `select(Property)` returns every property in the account.** That is why D3 exists:
visibility is `services/chat/access.py::visible_conversation_ids` and nothing else. The query layer filters by
column and could not express "participant of this DM" anyway.

`scoped_property_ids` lives in **`authz/scope.py:102`** (not `query_scope.py`): `frozenset()` for a non-active
membership (`:121-122`) or unscoped staff (`:134-140`), every property for owner/admin (`:124-132`). `access.py`
calls it; it never re-derives it ("one implementation, four consumers", `scope.py:3`).

### 0.2 — The app-wide gate holds a DB connection for the whole life of a stream

`create_app` installs `Depends(enforce_declared_action)` app-wide (`web/app.py:109`); it takes
`Depends(get_db)` (`deps.py:285-286`) and opens a transaction (`bind_account_guc`, `:337`). In FastAPI 0.159.1 a
yield-dependency without `scope="function"` exits on the **request** stack, after the body is sent
(`fastapi/dependencies/utils.py:791-793`). A `StreamingResponse` body never finishes, so that pooled connection
stays checked out, **idle in transaction**, for as long as the tab is open. `db.py:83` passes no pool
arguments: SQLAlchemy's 5 + 10 overflow (`pool/impl.py:75-76`). **The sixteenth open chat tab stalls every
request in the app**, and an `idle in transaction` connection with everything queued behind it is exactly the
session-touch deadlock's shape (`auth/sessions.py:72-104`). `/ai/ask` already streams this way (`ai.py:676`),
for seconds, not hours. **So the stream handler releases the injected session before returning** (D9, A16);
every later DB touch is a short-lived session in a worker thread.

### 0.3 — Why SSE and not WebSockets

`HostAndOriginGuardMiddleware` is a `BaseHTTPMiddleware` (`web/security.py:62`), which handles `http` scopes
only: a WebSocket would skip both the Host (DNS-rebinding) and Origin (CSRF) checks. And
`enforce_declared_action` takes a `Request` (`deps.py:286`), which FastAPI does not inject into WebSocket
routes, so the one place route declarations are enforced would not run. SSE is a plain `GET` that passes the
guard, carries `@declares`, and already streams through both middlewares on `/ai/ask` (`ai.py:676-680`, same
headers). Client→server stays HTMX POSTs, which the Origin guard covers.

### 0.4 — `ACCOUNT_SHARED` is the right class, and two texts that describe it become false

`ACCOUNT_SHARED` (`authz/actions.py:265`) means "account-wide, staff use it, **no row filter by design**",
declared in `UNFILTERED_CLASSES` (`query_scope.py:224-243`). That is chat exactly. `ACCOUNT_LEVEL` means "✗ for
staff" (`actions.py:238`), would be a lie, and would trip
`test_leak_matrix.py::test_no_account_level_model_is_read_by_a_staff_allowed_route` on every chat route.

But both texts describing the class are **template-only** — `UNFILTERED_CLASSES[ACCOUNT_SHARED]`
(`query_scope.py:233-242`) and `CLASS_MECHANISM[ACCOUNT_SHARED]` (`test_leak_matrix.py:150-153`) say "enforced by
`task.manage`/`automation.manage`". Left alone they repeat the false enforcement claim `query_scope.py:44-48`
calls U7's most instructive finding; B1/B2 fix them. **Nothing static guards chat reads** (that scan reads only
`ACCOUNT_LEVEL`); A27's route-derived probe is the backstop.

### 0.5 — Which pinned numbers move, and three the contract expected to move that do not

Measured at `9c7d595`:

| Pin | Now | After | Note |
|---|---|---|---|
| `test_matrix.py:77` split rows | `{5:3, 7:2, 8:2, 10:2}` | `{5:3, 7:2, 8:2, 10:2, 20:2}` | Step 1 |
| `test_matrix.py:81` `len(MATRIX)` | 25 | **26** | Step 1 |
| `test_isolation.py:90` `EXPECTED_TENANT_TABLE_COUNT` | 55 | **62** | Step 2, +7 tables |
| `test_tenancy_registry.py:276` | 55 | **62** | Step 2 |
| `test_pg_baseline.py:154` table count | literal **57** | **measured head + 7** | **Already stale.** The comment trail stops at 0017. Five tenant tables have landed since (0020, 0022, 0023, 0025, 0026). From the registry: 55 tenant + 6 migrated GLOBAL (`waitlist` excluded, `:196`) + `alembic_version` = **62 today → 69**. Re-measure; do not copy |

**Unchanged, contrary to the cross-spec contract:** `test_u7_enforcement.py:453`'s `no_linkage` set is derived
from `_models_in_class(ACCOUNT_LEVEL)` (`:444-451`) and chat is `ACCOUNT_SHARED`. `test_leak_matrix.py:490`
checks **key names** (`"own" in k or "self" in k`), not rule text — `chat.use` passes as is. `test_permissions.py`
iterates `MATRIX` generically (`:155-160`); nothing to edit.

### 0.6 — The A21 seeder will reject CHECK-constrained strings unless the model gives them a default

`test_isolation.py::_value_for` (`:132-176`) fills every required `String` with `"{tag}-{col}"` and skips
columns with a `default`/`server_default` (`:220-224`). `kind='a-kind'` would violate the CHECK and A21 would
`pytest.fail("could not seed")`. So: `kind` defaults to `"estate"`; `author_kind` defaults to `"bot"` —
**fail-loud**, since a member post that omits it still has an author and `ck_chat_posts_author` rejects it.
Emoji and event type are validated in the service, not by CHECK, so O1 or a SPEC-013 event type needs no
migration. `Identity()`/`Computed()` set `server_default` (measured), so the seeder skips `cursor` and
`search_tsv`. **No native enums** (`test_pg_baseline.py:155` pins 22).

### 0.7 — Templates: partials live under `partials/chat/`, and every template edit rebuilds CSS

`test_ui_responsive.py::navigable_templates` (`:414-424`) exempts only paths under `partials/`; a
`chat/_post.html` would be a "page" owed a checklist row. So the partials live in **`templates/partials/chat/`**
and only `chat/index.html` and `chat/conversation.html` get `UI_MANUAL_CHECKLIST.md` rows.
`test_ui_build.py::_expected_stamp` (`:119-133`) hashes every template, so every template-touching commit —
Step 1's `base.html` edit included — runs `npm run build:css && npm run stamp` (`package.json:7,9`). `chat.js`
adds **no Tailwind classes** (it swaps server-rendered HTML), so the `content` globs need no change.

---

## 1. Decisions

### 1.1 Locked

| # | Decision | Rationale |
|---|---|---|
| **D1** | **New tables. `chat_messages` is untouched** | That table is the Telegram log: account-level, staff-denied, keyed on gateway ids (`models/chat_message.py`). Native chat needs authors, edits, replies, reactions and membership |
| **D2** | **One key: `chat.use` = row 20 split, `_A, _A, _A, _ACCT`** | Every logged-in role chats. Row 20 is the communication row (`gateway.link_self`). `ACCOUNT` because a conversation is not a property operation, and `SCOPED` on `ACCOUNT` is forbidden (`test_matrix.py:104-116`). The narrowing happens in the mechanism, not the grant, which is the `staff.view_own`/`playbook.manage` shape (`actions.py:131, 155`) |
| **D3** | **Visibility = `access.visible_conversation_ids`, and only that** | §0.1. The set is: the estate chat, plus property chats whose `property_id` is in `scoped_property_ids(m)`, plus **DM** conversations where `m` has a participant row. An inactive membership gets `set()`. **The participant join is filtered to `kind = 'dm'`.** Group read cursors also live in `chat_participants`, so an unfiltered join would let a staff member keep a property chat after losing that property (A6) |
| **D4** | **Owners and admins do not see other people's DMs** | User decision. Under D3 nobody's visibility depends on role except through property scope |
| **D5** | **All chat models are `ACCOUNT_SHARED`** | §0.4. RLS still isolates accounts. Every table is in `TENANT_TABLES` with a policy and drift triggers |
| **D6** | **Group conversations are created lazily; a DM is created on first open** | Opening `/chat/` ensures the estate chat plus one chat per visible property, using `INSERT … ON CONFLICT DO NOTHING` against the partial unique index. The race-free form; an `IntegrityError` retry loop is the alternative and is worse. A DM is created when one member opens it with another |
| **D7** | **Per-conversation `seq` via `UPDATE … SET next_seq = next_seq + 1 RETURNING`** | Gap-free ordering under concurrency without a sequence per conversation. The row lock lives until commit, so **mutating routes commit explicitly before rendering** (D10) |
| **D8** | **SSE + HTMX POSTs; the payload carries ids only** | §0.3. The client re-fetches `GET /chat/posts/{id}`, which re-runs `require_visible` under RLS. A leaked event therefore reveals an id and never content |
| **D9** | **The stream releases the request session before streaming.** Every later read uses `open_session()` inside `account_context`, in a worker thread | §0.2. A16 measures it |
| **D10** | **Mutating chat routes call `db.commit()` before rendering** | The `after_commit` publish and the release of the D7 row lock must both happen before the response. Otherwise they happen after the body is sent, on the request stack. Existing precedent: `routes/auth.py:230` |
| **D11** | **Reactions: a fixed allowlist, enforced in `reactions.py`** | O1 sets the contents. Not a DB CHECK (§0.6) |
| **D12** | **Edit: the author only, never a bot post, never a deleted post. Delete: the author, or owner/admin in group chats. Soft delete sets `body=''` and `deleted_at`, and drops the reactions** | User decision. `ck_chat_posts_deleted_blank` makes "deleted but still has text" impossible. An owner cannot delete inside someone else's DM, because they cannot see it (D4) |
| **D13** | **Unread is per viewer. Read receipts ("Seen") show in DMs only** | In a group, "seen by 7 of 9" is a presence signal by another name, and the user excluded presence. For a group with no participant row yet, unread counts posts created after `memberships.created_at`, so a new hire does not open the app to 4,000 unread. Your own posts never count |
| **D14** | **The stream runs on chat pages only. The sidebar badge polls every 60 s elsewhere** | Browsers allow six HTTP/1.1 connections per host. A stream on every page would eat one per tab across the whole app. The `alert_badge.html` pattern is proven |
| **D15** | **Typing is memory-only: never stored, never given a cursor, throttled to 1 per 3 s per (member, conversation) on the server** | It has no value after three seconds. Storing it would make the catch-up replay noise. **The one non-id payload field:** the typist's display name, sent only to streams that can see the conversation, i.e. people who already see that person's posts |
| **D16** | **The bus keeps `(loop, queue)` per subscriber; `publish` uses `sub.loop.call_soon_threadsafe`** | `create_app` has no lifespan hook (`app.py:96-110`). Tests also build several apps, each in its own TestClient loop (`tests/integration/conftest.py:163-166`), so one loop captured at startup would silently drop events across them. With no subscribers there is nothing to deliver, so no loop is needed. Equivalent to the contract's "loop captured at startup" from a caller's point of view: `emit`'s signature is unchanged |
| **D17** | **Optional `LISTEN/NOTIFY` when `REALTIME_DATABASE_URL` is set.** `emit` then runs `pg_notify` inside the transaction, and a listener task fans the notification out locally | NOTIFY is transactional, so Postgres delivers it only on commit, which is exactly `after_commit`'s semantics with no double delivery. NOTIFY passes through PgBouncer and LISTEN does not (`MULTITENANCY.md:655-670`), hence a direct URL. evo-dev runs one uvicorn process (`web/server.py:136`), so in-process mode is correct there |
| **D18** | **Body text is autoescaped and linkified by a server tokenizer. `|safe` is never applied to user text** | Links are emitted only for `http(s)://` tokens, with `rel="noopener noreferrer nofollow"`. SPEC-013 adds a `mention` token to the same tokenizer |
| **D19** | **A member body is ≤ 4000 chars (DB CHECK). Bot bodies are exempt** | Cheap to add while the table is being created. SPEC-014's rate limits need not re-add it. AI replies can be long |
| **D20** | **The Telegram Conversations item leaves the sidebar; the route stays** | User decision. The dead `key != 'conversations'` clause at `base.html:106` goes with it |

### 1.2 `OPEN — needs decision: founder`

| # | Question | Blocks |
|---|---|---|
| **O1** | **The reaction set.** Proposed default: `👍 ❤️ 😂 😮 😢 🙏 ✅ 👀` (8). `✅` doubles as "done", which estate staff will use as an acknowledgement | **Nothing.** Build with the default. Changing it is one tuple, `reactions.EMOJI_ALLOWLIST`. Stored rows that hold a retired emoji still render |
| **O2** | **An edit time window.** Unlimited (Slack-style, with an "edited" label), or N minutes? | **Nothing.** Build unlimited, with `EDIT_WINDOW: timedelta \| None = None` in `posts.py`. A window is a one-line change plus A12's second case |

---

## 2. Doc-fix prerequisites

| # | Doc + location | Fix |
|---|---|---|
| **B1** | `authz/query_scope.py:233-242` `UNFILTERED_CLASSES[ACCOUNT_SHARED]` | Generalise it: "staff use these rows. The rule is a verb distinction (templates: `task.manage` vs `automation.manage`) **or a participant rule enforced in one service (chat: `services.chat.access`)**." Keep it longer than 80 characters (`test_u7_enforcement.py:275`) |
| **B2** | `tests/integration/test_leak_matrix.py:150-153` `CLASS_MECHANISM[ACCOUNT_SHARED]` | Same addition. It is a census table, and leaving it template-only states a mechanism that does not cover chat |
| **B3** | `web/templates/base.html:56-66` (the nav comments) | Record that Conversations left the sidebar on 2026-10-28 (D20) and that `/conversations/` is still reachable by URL |

---

## 3. File manifest

### New

```
src/mihomes/models/chat_native.py             7 models (§4.1)
alembic/versions/0027_native_chat.py          7 tables, CHECKs, partial uniques, GIN, RLS + drift (§4.2)
src/mihomes/services/chat/{__init__,access,conversations,posts,reactions,reads,events,bus,stream,render}.py
                                              §5; __init__ imports .events so the after_commit listener installs;
                                              render.py = tokenize_body (D18)
src/mihomes/web/routes/chat.py                13 routes (§5.5)
src/mihomes/web/templates/chat/{index,conversation}.html
src/mihomes/web/templates/partials/chat/{_post,_composer,_sidebar_list,_unread_badge}.html
src/mihomes/web/static/chat.js                EventSource, dedupe, partial re-fetch, typing throttle
```

### Modified

| Path | Change |
|---|---|
| `src/mihomes/authz/actions.py` | `chat.use` in `MATRIX` (D2). The 7 models in `_entity_classes` as `ACCOUNT_SHARED` |
| `src/mihomes/authz/query_scope.py` | B1. Text only; no behaviour change |
| `src/mihomes/models/__init__.py` | Import the 7 models (after `chat_message`, `:92`) |
| `src/mihomes/tenancy/registry.py` | The 7 table names in `TENANT_TABLES`, after `chat_messages` (`:177`) |
| `src/mihomes/web/app.py` | `include_router(chat.router, prefix="/chat")`. Exception handlers for `ChatNotFound`→404, `ChatForbidden`→403, `ChatInvalid`→400. `start_listener` on startup **only** when `REALTIME_DATABASE_URL` is set |
| `src/mihomes/web/templates/base.html` | Remove the Conversations item + clause (D20). Add a `('/chat/', 'chat', …, 'Chat')` item in the first group with `{% if key == 'chat' %}` badge markup (`hx-get="/chat/unread-badge"`, `hx-trigger="load, every 60s, chat-read from:body"`) |
| `src/mihomes/cli/jobs.py` | `chat-prune-events`: deletes `chat_events` older than 7 days, using `_all_accounts()`/`_account_session` (`:484, :541`) as `weekly-digest` does |
| `src/mihomes/web/static/app.css` + `app.css.stamp` | Rebuilt (§0.7) |
| `docs/UI_MANUAL_CHECKLIST.md` | Rows for `` `chat/index.html` `` and `` `chat/conversation.html` `` |
| `test_matrix.py:77,81`, `test_tenancy_registry.py:276`, `test_isolation.py:90`, `test_pg_baseline.py:154` | The §0.5 pins, each with a comment line saying why (`test_matrix`'s docstring split list gains row 20) |
| `tests/integration/test_leak_matrix.py:150` | B2 |

---

## 4. Schemas as code

### 4.1 `src/mihomes/models/chat_native.py`

```python
"""Native in-app chat (SPEC-012). Seven tenant tables, all `ACCOUNT_SHARED`: RLS isolates accounts,
and `services.chat.access` alone decides which conversations a member sees (§0.1)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Computed, DateTime, ForeignKey,
                        Identity, Index, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned

KIND_ESTATE, KIND_PROPERTY, KIND_DM = "estate", "property", "dm"
AUTHOR_MEMBER, AUTHOR_BOT = "member", "bot"
MAX_MEMBER_BODY = 4000

def _uuid_pk():
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
def _fk(target: str, *, nullable: bool, ondelete: str | None = "CASCADE"):
    return mapped_column(PGUUID(as_uuid=True), ForeignKey(target, ondelete=ondelete),
                         nullable=nullable, index=True)
def _created():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

class ChatConversation(Base, TenantOwned):
    __tablename__ = "chat_conversations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    # default satisfies ck_..._kind for the A21 seeder (§0.6); services always pass kind explicitly.
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default=KIND_ESTATE)
    # CASCADE, not SET NULL (the CHECK forbids a property chat with no property) and not RESTRICT
    # (property.delete would 500 for every property whose chat was ever opened).
    property_id: Mapped[uuid.UUID | None] = _fk("properties.id", nullable=True)
    # str(uuid_a) + ":" + str(uuid_b), sorted lexicographically: 36 + 1 + 36.
    dm_key: Mapped[str | None] = mapped_column(String(73), nullable=True)
    # Display override only. NULL = derived at render ("General", Property.name, the other member).
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    next_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("kind IN ('estate', 'property', 'dm')", name="ck_chat_conversations_kind"),
        CheckConstraint("(kind = 'property') = (property_id IS NOT NULL)",
                        name="ck_chat_conversations_property"),
        CheckConstraint("(kind = 'dm') = (dm_key IS NOT NULL)", name="ck_chat_conversations_dm_key"),
        Index("uq_chat_conversations_estate", "account_id", unique=True,
              postgresql_where=text("kind = 'estate'")),
        Index("uq_chat_conversations_property", "account_id", "property_id", unique=True,
              postgresql_where=text("kind = 'property'")),
        Index("uq_chat_conversations_dm", "account_id", "dm_key", unique=True,
              postgresql_where=text("kind = 'dm'")),
    )

class ChatParticipant(Base, TenantOwned):
    """DM membership (two rows per DM) **and** the read cursor for every kind (lazy for groups).
    Visibility reads it for `kind='dm'` only (D3). Composite PK of two FKs, which test_uuid_pks skips."""
    __tablename__ = "chat_participants"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True,
        index=True)
    last_read_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    joined_at: Mapped[datetime] = _created()

class ChatPost(Base, TenantOwned):
    __tablename__ = "chat_posts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = _fk("chat_conversations.id", nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # NULL means the bot. NO ACTION on delete: SET NULL would silently turn a person into the bot.
    author_membership_id: Mapped[uuid.UUID | None] = _fk("memberships.id", nullable=True, ondelete=None)
    # Fail-loud default (§0.6): a member post that omits author_kind violates ck_chat_posts_author.
    author_kind: Mapped[str] = mapped_column(String(10), nullable=False, default=AUTHOR_BOT)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reply_to_id: Mapped[uuid.UUID | None] = _fk("chat_posts.id", nullable=True, ondelete="SET NULL")
    # Composer idempotency key (SPEC-013 P4): a double-submit carries the same token, so it returns
    # the first post instead of creating a second. NULL for bot posts and older clients.
    client_token: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    # SPEC-013 search. Deferred, so no ordinary load pays for it.
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', coalesce(body, ''))", persisted=True), deferred=True)
    __table_args__ = (
        CheckConstraint("author_kind IN ('member', 'bot')", name="ck_chat_posts_author_kind"),
        CheckConstraint("(author_kind = 'bot') = (author_membership_id IS NULL)",
                        name="ck_chat_posts_author"),
        CheckConstraint(f"author_kind = 'bot' OR char_length(body) <= {MAX_MEMBER_BODY}",
                        name="ck_chat_posts_body_len"),
        CheckConstraint("deleted_at IS NULL OR body = ''", name="ck_chat_posts_deleted_blank"),
        UniqueConstraint("conversation_id", "seq", name="uq_chat_posts_seq"),  # also the history index
        Index("ix_chat_posts_search_tsv", "search_tsv", postgresql_using="gin"),
        Index("uq_chat_posts_client_token", "conversation_id", "client_token", unique=True,
              postgresql_where=text("client_token IS NOT NULL")),
    )

class ChatReaction(Base, TenantOwned):
    __tablename__ = "chat_reactions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    post_id: Mapped[uuid.UUID] = _fk("chat_posts.id", nullable=False)
    membership_id: Mapped[uuid.UUID] = _fk("memberships.id", nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)   # allowlist in the service (D11)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("post_id", "membership_id", "emoji", name="uq_chat_reactions"),)

class ChatAttachment(Base, TenantOwned):
    """Created here, written by SPEC-013. A row exists only with its post (uploaded in the same POST)."""
    __tablename__ = "chat_attachments"
    id: Mapped[uuid.UUID] = _uuid_pk()
    post_id: Mapped[uuid.UUID] = _fk("chat_posts.id", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (CheckConstraint("size_bytes >= 0", name="ck_chat_attachments_size"),)

class ChatMention(Base, TenantOwned):
    """Created here, written by SPEC-013, read by SPEC-014 (`notified_at`)."""
    __tablename__ = "chat_mentions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    post_id: Mapped[uuid.UUID] = _fk("chat_posts.id", nullable=False)
    membership_id: Mapped[uuid.UUID] = _fk("memberships.id", nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("post_id", "membership_id", name="uq_chat_mentions"),)

class ChatEvent(Base, TenantOwned):
    """The SSE catch-up log; `cursor` is the SSE `id:`. UUID PK (SPEC-002 D2) plus an identity column,
    because Last-Event-ID needs a total order a UUIDv7 does not promise within one millisecond."""
    __tablename__ = "chat_events"
    id: Mapped[uuid.UUID] = _uuid_pk()
    cursor: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    conversation_id: Mapped[uuid.UUID] = _fk("chat_conversations.id", nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)    # events.EVENT_TYPES, not a CHECK
    post_id: Mapped[uuid.UUID | None] = _fk("chat_posts.id", nullable=True)
    actor_membership_id: Mapped[uuid.UUID | None] = _fk("memberships.id", nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), index=True)
```

**Drift guard: no hand-listing.** `parent_links` (`tenancy/drift_guard.py:118-137`) derives **14** links from
these FKs — the contract's set plus the membership links it left implicit — and
`test_pg_baseline.py:203` counts them.

### 4.2 `alembic/versions/0027_native_chat.py`

```python
"""native in-app chat (SPEC-012) — seven tenant tables, RLS + drift guard per table (0026 pattern)."""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
revision: str = "0027_native_chat"
down_revision: Union[str, None] = "0026_chat_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
TABLES = ("chat_conversations", "chat_participants", "chat_posts", "chat_reactions",
          "chat_attachments", "chat_mentions", "chat_events")      # creation order = FK order

def _tenant():
    return [sa.Column("account_id", UUID, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)]
def _now():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

def upgrade() -> None:
    op.create_table(
        "chat_conversations", sa.Column("id", UUID, primary_key=True), *_tenant(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("property_id", UUID, sa.ForeignKey("properties.id", ondelete="CASCADE")),
        sa.Column("dm_key", sa.String(73)), sa.Column("title", sa.String(200)),
        sa.Column("next_seq", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("last_message_at", sa.DateTime(timezone=True)), _now(),
        sa.CheckConstraint("kind IN ('estate', 'property', 'dm')", name="ck_chat_conversations_kind"),
        sa.CheckConstraint("(kind = 'property') = (property_id IS NOT NULL)",
                           name="ck_chat_conversations_property"),
        sa.CheckConstraint("(kind = 'dm') = (dm_key IS NOT NULL)", name="ck_chat_conversations_dm_key"),
    )
    for kind, cols in (("estate", []), ("property", ["property_id"]), ("dm", ["dm_key"])):
        op.create_index(f"uq_chat_conversations_{kind}", "chat_conversations", ["account_id", *cols],
                        unique=True, postgresql_where=sa.text(f"kind = '{kind}'"))

    op.create_table(
        "chat_posts", sa.Column("id", UUID, primary_key=True), *_tenant(),
        sa.Column("conversation_id", UUID, sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("author_membership_id", UUID, sa.ForeignKey("memberships.id")),
        sa.Column("author_kind", sa.String(10), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("reply_to_id", UUID, sa.ForeignKey("chat_posts.id", ondelete="SET NULL")),
        sa.Column("client_token", UUID),   # SPEC-013 P4; partial unique index below
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)), _now(),
        sa.Column("search_tsv", postgresql.TSVECTOR,
                  sa.Computed("to_tsvector('simple', coalesce(body, ''))", persisted=True)),
        sa.CheckConstraint("author_kind IN ('member', 'bot')", name="ck_chat_posts_author_kind"),
        sa.CheckConstraint("(author_kind = 'bot') = (author_membership_id IS NULL)",
                           name="ck_chat_posts_author"),
        sa.CheckConstraint("author_kind = 'bot' OR char_length(body) <= 4000",
                           name="ck_chat_posts_body_len"),
        sa.CheckConstraint("deleted_at IS NULL OR body = ''", name="ck_chat_posts_deleted_blank"),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_chat_posts_seq"),
    )
    op.create_index("ix_chat_posts_search_tsv", "chat_posts", ["search_tsv"], postgresql_using="gin")
    op.create_index("uq_chat_posts_client_token", "chat_posts", ["conversation_id", "client_token"],
                    unique=True, postgresql_where=sa.text("client_token IS NOT NULL"))

    # chat_participants (between conversations and posts) and chat_reactions / chat_attachments /
    # chat_mentions / chat_events: column-for-column from §4.1, constraints inline, in TABLES order.
    # chat_events.cursor is sa.Column("cursor", sa.BigInteger, sa.Identity(always=True), unique=True).
    ...

    # One index per `index=True` column in §4.1, named op.f("ix_<table>_<column>"), as 0026 does.
    for table, cols in {
        "chat_conversations": ["account_id", "property_id"],
        "chat_participants": ["account_id", "membership_id"],
        "chat_posts": ["account_id", "conversation_id", "author_membership_id", "reply_to_id"],
        "chat_reactions": ["account_id", "post_id", "membership_id"],
        "chat_attachments": ["account_id", "post_id"],
        "chat_mentions": ["account_id", "post_id", "membership_id"],
        "chat_events": ["account_id", "conversation_id", "post_id", "actor_membership_id", "created_at"],
    }.items():
        for col in cols:
            op.create_index(op.f(f"ix_{table}_{col}"), table, [col])

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for stmt in [s for t in TABLES for s in policy_statements(t)] + trigger_ddl_statements(
                Base.metadata, only_tables=set(TABLES)):
            op.execute(stmt)

def downgrade() -> None:
    for table in reversed(TABLES):        # indexes, policies and triggers drop with their table
        op.drop_table(table)
```

The `...` is five `op.create_table` calls mirroring §4.1; `test_baseline_matches_metadata`
(`test_pg_baseline.py:169`) fails on any drift between them. **Check first in Step 2:** if autogenerate reports
the partial-index predicates as a diff (Postgres normalises to `(kind)::text = 'estate'::text`), write the
model's `postgresql_where` in the normalised form. Never exclude the index from the comparison.

---

## 5. Function signatures

### 5.1 `services/chat/access.py` and `conversations.py`

```python
class ChatNotFound(LookupError): ...      # -> 404. Not visible and does not exist are the same answer
class ChatForbidden(PermissionError): ... # -> 403. Visible, but this verb is refused (edit another's post)
class ChatInvalid(ValueError): ...        # -> 400. Empty body, unknown emoji, cross-conversation reply
def visible_conversation_ids(session: Session, membership: Membership) -> set[uuid.UUID]:
    """D3 — THE chat authorization boundary. set() for a non-active membership; else estate ∪
    {property chats with property_id ∈ scoped_property_ids(session, membership)} ∪ {kind='dm'
    conversations with a participant row for membership.id}. The DM arm MUST filter kind='dm' (A6)."""
def require_visible(session: Session, membership: Membership,
                    conversation_id: uuid.UUID) -> ChatConversation:
    """The conversation or ChatNotFound. Every route taking a conversation/post id calls this first."""
def post_for(session: Session, membership: Membership, post_id: uuid.UUID) -> ChatPost:
    """Load the post, then require_visible on its conversation. ChatNotFound either way."""
# --- conversations.py ---
def get_or_create_estate(session: Session) -> ChatConversation: ...
def get_or_create_property(session: Session, property_id: uuid.UUID) -> ChatConversation: ...
def get_or_create_dm(session: Session, me: Membership, other_membership_id: uuid.UUID) -> ChatConversation:
    """ChatInvalid for yourself or a non-active membership. dm_key = ':'.join(sorted([str(a), str(b)])),
    both participant rows. All three: pg insert().on_conflict_do_nothing(index_elements=[...],
    index_where=text("kind = '…'")) then SELECT; emit 'conversation.created' only if rowcount == 1."""
def ensure_group_conversations(session: Session, membership: Membership) -> None:  # D6, GET /chat/
@dataclass(frozen=True)
class ConversationRow:
    id: uuid.UUID; kind: str; title: str; unread: int; last_message_at: datetime | None
def list_for(session: Session, membership: Membership) -> list[ConversationRow]:
    """Visible only: General, properties A–Z, DMs by recency. Titles derived (Property.name / the other
    member's User.name) for visible ids only — a staff select(Property) is unfiltered here (§0.1)."""
def dm_candidates(session: Session, membership: Membership) -> list[MemberView]:
    """list_members(session, account_id) minus me (`membership_service.py:215`); render `name` only."""
```

### 5.2 `services/chat/posts.py`

```python
EDIT_WINDOW: timedelta | None = None   # O2
PAGE_SIZE = 50
def create_post(session: Session, conversation: ChatConversation, *, author: Membership | None,
                body: str, reply_to_id: uuid.UUID | None = None, allow_empty: bool = False,
                client_token: uuid.UUID | None = None) -> ChatPost:
    """author=None is the bot. Stripped empty body → ChatInvalid unless allow_empty (a photo-only
    post, SPEC-013 P2). client_token (SPEC-013 P4): if a post with this token already exists in the
    conversation, return it unchanged — no new seq, no event — so a double-submit is one post.
    A member author must see the
    conversation (defence in depth). DM whose other side is inactive → ChatInvalid. reply_to_id must be
    in the SAME conversation. seq = UPDATE chat_conversations SET next_seq = next_seq + 1,
    last_message_at = now() WHERE id = :id RETURNING next_seq - 1 (D7). Advances the author's read
    cursor. Emits 'post.created'. Never commits."""
def create_bot_post(session: Session, conversation_id: uuid.UUID, body: str) -> ChatPost:
    """SPEC-013's seam. ChatInvalid for a DM — DMs are people-only."""
def edit_post(session: Session, post: ChatPost, *, actor: Membership, body: str) -> ChatPost:
    """D12: author only (ChatForbidden); not bot, not deleted; O2. Sets edited_at; 'post.edited'."""
def delete_post(session: Session, post: ChatPost, *, actor: Membership) -> ChatPost:
    """D12: author, or owner/admin when kind != 'dm'. body='', deleted_at=now(), reactions deleted.
    Idempotent. 'post.deleted'."""
def history(session: Session, conversation_id: uuid.UUID, *, before_seq: int | None = None,
            limit: int = PAGE_SIZE) -> list[ChatPost]:   # ascending; caller already holds require_visible
```

### 5.3 `services/chat/reactions.py`, `reads.py`, `events.py`

```python
EMOJI_ALLOWLIST: tuple[str, ...] = ("👍", "❤️", "😂", "😮", "😢", "🙏", "✅", "👀")   # O1
def toggle_reaction(session: Session, post: ChatPost, *, actor: Membership, emoji: str) -> bool:
    """True if now present. Unknown emoji / deleted post → ChatInvalid. 'reaction.changed'."""
def mark_read(session: Session, membership: Membership, conversation_id: uuid.UUID, seq: int) -> None:
    """Upsert; last_read_seq = GREATEST(old, seq). 'read.changed' only if it advanced."""
def unread_counts(session: Session, membership: Membership,
                  conversation_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
    """D13: non-deleted, seq > last_read_seq, author IS DISTINCT FROM me; no participant row → posts
    after membership.created_at. One grouped query."""
def total_unread(session: Session, membership: Membership) -> int:  # over visible_conversation_ids
EVENT_TYPES = frozenset({"post.created", "post.edited", "post.deleted", "reaction.changed",
                         "read.changed", "conversation.created"})   # typing never (D15)
RETENTION = timedelta(days=7)
def emit(session: Session, *, conversation_id: uuid.UUID, type: str,
         post_id: uuid.UUID | None = None, actor_membership_id: uuid.UUID | None = None) -> ChatEvent:
    """Unknown type → ValueError. Inserts + flushes a ChatEvent (for `cursor`), appends
    (event.account_id, {"id": cursor, "type", "c", "p", "m"}) — ids only (D8) — to
    session.info["chat_events"]. The account travels WITH the event (from the row, never re-read from
    a ContextVar at commit time — SPEC-013's background dispatch and the NOTIFY listener have none).
    NOTIFY mode: also SELECT pg_notify('mihomes_chat', :json) with "a": account_id in the JSON (D17)."""
def _after_commit(session: Session) -> None:
    """Pops the queue → BUS.publish(account_id, event), unless NOTIFY mode. Never runs SQL. Installed
    on the Session class idempotently, as tenancy/connection.py:201-209 does. Every mutating path must
    commit (routes: D10; SPEC-013's background task: inside account_context) or nothing is announced."""
def _after_rollback(session: Session) -> None:   # discards the queue: a rolled-back post is never announced
def prune_events(session: Session, *, now: datetime | None = None) -> int: ...
```

### 5.4 `services/chat/bus.py` and `stream.py`

```python
@dataclass(eq=False)
class Subscription:
    account_id: uuid.UUID
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict]     # maxsize=256; overflow → one {"type": "resync"}, then drop
class ChatBus:
    def subscribe(self, account_id: uuid.UUID) -> Subscription:  # on the loop; captures it (D16)
    def unsubscribe(self, sub: Subscription) -> None: ...
    def publish(self, account_id: uuid.UUID, event: dict) -> None:
        """Any thread: sub.loop.call_soon_threadsafe(_offer, sub, event) per subscriber. None → no-op."""
BUS = ChatBus()
async def start_listener(dsn: str) -> None:
    """D17: psycopg AsyncConnection, LISTEN mihomes_chat → BUS.publish. Backoff 1 s → 30 s; warns and
    stays in-process if it cannot connect."""
HEARTBEAT_S, REFRESH_S, MAX_LIFETIME_S = 20, 60, 15 * 60   # lifetime end → EventSource re-runs the gate
CATCHUP_LIMIT, CATCHUP_OVERLAP = 500, timedelta(seconds=5)
async def stream_events(*, account_id: uuid.UUID, user_id: uuid.UUID, membership_id: uuid.UUID,
                        session_cookie: str, after_cursor: int | None,
                        open_session: Callable[[], ContextManager[Session]] = get_session,
                        bus: ChatBus = BUS) -> AsyncIterator[str]:
    """1. bus.subscribe FIRST, so nothing committed between catch-up and subscription is lost.
    2. Worker thread, open_session() inside account_context(account_id, user_id): the visible set,
       then replay chat_events WHERE conversation_id IN visible AND (cursor > after OR created_at >=
       ts(after) - CATCHUP_OVERLAP) ORDER BY cursor. Identity values commit out of order; the overlap
       re-sends a few and the client dedupes by id (A19). > CATCHUP_LIMIT or `after` pruned → resync.
    3. Loop: next event or ': hb' after HEARTBEAT_S. Drop events whose "c" is not visible. On
       'conversation.created' refresh now; every REFRESH_S refresh in a thread — lookup_session(cookie)
       None or membership inactive → 'event: revoked' and return.
    4. Return at MAX_LIFETIME_S; finally unsubscribe.
    Frames: 'id: {cursor}\\nevent: {type}\\ndata: {json}\\n\\n'. Typing frames have no `id:`."""
```

### 5.5 `web/routes/chat.py` — every route `@declares("chat.use", Access.ACCOUNT)`, prefix `/chat`

```python
@router.get("/")                              # ensure_group_conversations, list_for, DM picker
@router.get("/c/{conversation_id}")           # page: sidebar + last PAGE_SIZE posts + data-last-event
@router.get("/c/{conversation_id}/posts")     # ?before=<seq>: older page (partial)
@router.get("/posts/{post_id}")               # the _post.html partial chat.js re-fetches (D8)
@router.post("/c/{conversation_id}/posts")    # body, reply_to_id → commit (D10) → _post.html
@router.post("/posts/{post_id}/edit")         # → commit → _post.html
@router.post("/posts/{post_id}/delete")       # → commit → _post.html ("deleted" state)
@router.post("/posts/{post_id}/react")        # emoji → commit → _post.html
@router.post("/c/{conversation_id}/read")     # seq → commit → 204 + HX-Trigger: chat-read
@router.post("/c/{conversation_id}/typing")   # require_visible, throttle, BUS.publish; no DB write
@router.post("/dm")                           # membership_id → get_or_create_dm → HX-Redirect
@router.get("/unread-badge")                  # partials/chat/_unread_badge.html
@router.get("/stream")
@declares("chat.use", Access.ACCOUNT)
def chat_stream(request: Request, after: int | None = None,
                principal: RequestPrincipal = require_authenticated(),
                db: Session = Depends(get_db)) -> StreamingResponse:
    """Sync `def` → threadpool, so no DB work on the loop. Last-Event-ID header wins over ?after=.
    Then db.commit(); db.close() — the pooled connection goes back (D9, A16); get_db's later commit on
    a closed Session acquires nothing. Returns StreamingResponse(stream_events(...),
    media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})."""
```

`require_authenticated()` shares the cached `get_db` session (`deps.py:425-432`). Routes load the actor with
`db.get(Membership, principal.membership_id)` — `Membership` is exempt from the query filter
(`query_scope.py:168-174`). **`chat.js`:** one `EventSource('/chat/stream?after=' + data-last-event)` per chat tab
— the page embeds the max cursor so events between render and connect are not lost. Seen cursors go in a `Set`
(A19). `post.*`/`reaction.changed` → `htmx.ajax('GET', '/chat/posts/' + p, …)` if on screen, else bump the
sidebar row; `read.changed` → the DM "Seen" marker; `resync` → reload; `revoked` → close and go to `/`.
`keydown` POSTs `/typing` at most every 3 s; a received `typing` shows "Name is typing…" for 4 s.

---

## 6. Sequenced steps

**Step 1 — key + sidebar.** `chat.use` (D2), rule text *"2026-10-28: which conversations a member sees is
decided in services.chat.access, never by this grant"*; `test_matrix.py`; remove Conversations (D20, B3);
CSS rebuild. *Verify:* `pytest tests/unit/test_matrix.py tests/unit/test_ui_build.py
tests/unit/test_ui_responsive.py tests/integration/test_leak_matrix.py` (A1). Phases 0–1 intentionally move
pinned tests — land them as separate merges.

**Step 2 — models + migration.** §4, registry, `_entity_classes`, B1/B2, the four pins with comment lines.
*Verify:* upgrade → downgrade → upgrade on a scratch DB (`PYTHONPATH=src alembic …`), then `pytest
tests/integration/test_pg_baseline.py tests/integration/test_isolation.py tests/unit/test_tenancy_registry.py
tests/unit/test_uuid_pks.py tests/integration/test_u7_enforcement.py tests/integration/test_chat_schema.py` (A3–A5).

**Step 3 — access + conversations**, no routes. *Verify:* `pytest tests/integration/test_chat_access.py` (A6–A9).

**Step 4 — posts, reactions, reads, events**; `_after_commit` publishes to a subscriber-less `BUS`. *Verify:*
`pytest tests/integration/test_chat_service.py` (A10–A15).

**Step 5 — pages.** Routes minus `/stream`, templates, nav item + badge, exception handlers, checklist rows,
CSS rebuild. *Verify:* `pytest tests/integration/test_chat_routes.py tests/unit/test_route_declarations.py
tests/unit/test_ui_*.py` (A2, A20–A27); manual: owner + staff in two browsers, post and reload.

**Step 6 — bus + stream + `chat.js`.** *Verify:* `pytest tests/unit/test_chat_bus.py
tests/integration/test_chat_stream.py` (A16–A19, A28).

**Step 7 — LISTEN/NOTIFY + `chat-prune-events`.** *Verify:* `pytest tests/integration/test_chat_notify.py` (A29, A30).

**Exit check.** All green (A31), then the exit criterion walked on evo-dev in two browsers, plus one network
drop to watch catch-up. Each step is one commit, merged `--no-ff`; verify runs on `mihomes_test_rtb` against
the known-failure baseline.

---

## 7. Non-goals + deferred scope

**N1 — Do not filter chat rows in `query_scope`.** A second visibility implementation drifts, and drift is a
leak (`scope.py:5`). **N2 — Do not touch `chat_messages`** (D1). **N3 — No WebSocket route** (§0.3), not even
"just for typing". **N4 — No content in event payloads** (D8): it would turn a future visible-set bug from an
id leak into a message leak. **N5 — No DB reads on the event loop** in the stream — `run_in_threadpool` only;
the session-touch deadlock was exactly sync DB work on the loop (`sessions.py:75-83`). **N6 — No presence**
(no online dot, last-seen, or group "seen by"; D13). **N7 — No hard delete** from the UI; SPEC-013 search
relies on `deleted_at IS NULL`.

**DEFERRED (SPEC-013):** AI in group chats (`create_bot_post` is the seam). Attachments upload/download
(`chat_attachments` exists). @mentions (`chat_mentions` exists; `render.py` gains a `mention` token). Search
(`search_tsv` + GIN exist). `review_common._with_conversation_log` skipping `channel == "mihomes"`.

**DEFERRED (SPEC-014):** email digest and web push (0028 adds `push_subscriptions`, `chat_notify_prefs`;
`chat_mentions.notified_at` is their cursor). PWA. Rate limits (20 posts/min, 10 reactions/10 s). The 50-client
SSE load check. Confirming HTMX POSTs send `Origin`.

---

## 8. Acceptance criteria

App-role (non-superuser, RLS live) criteria are marked **[app]**.

| # | Criterion | Test |
|---|---|---|
| A1 | `chat.use` is `ALLOW` for all three roles, `ACCOUNT`, row 20, and splits are `{5:3,7:2,8:2,10:2,20:2}`, 26 keys | `test_matrix.py::test_the_key_count_is_what_the_splits_imply` (amended) |
| A2 | The sidebar has a Chat item with a badge and no Conversations item. `/conversations/` still answers 200 for an owner | `test_chat_routes.py::test_sidebar_has_chat_not_conversations` |
| A3 | 0027 upgrades, downgrades and re-upgrades cleanly. Metadata diff is empty | `test_pg_baseline.py::test_upgrade_then_downgrade_is_clean`, `::test_baseline_matches_metadata` |
| A4 | **The CHECKs bite:** a property chat with no property, a DM with no key, a bot post with an author, a member post over 4000 chars, and a deleted post with text are each refused | `test_chat_schema.py::test_checks_refuse_impossible_rows` |
| A5 | A second estate chat, a duplicate property chat or a duplicate `dm_key` in one account is refused. The same in two accounts is accepted | `test_chat_schema.py::test_partial_uniques_are_per_account` |
| A6 | **[app]** A staff member scoped to Belle sees General + Belle's chat + their own DMs, and **not** Blue's chat, even with a leftover participant row in Blue's chat | `test_chat_access.py::test_staff_visible_set` |
| A7 | **[app]** An owner does not see a staff-to-staff DM, and gets 404 at `/chat/c/{id}` for it | `test_chat_access.py::test_owner_cannot_read_others_dm` |
| A8 | **[app]** A revoked membership's visible set is empty | `test_chat_access.py::test_revoked_sees_nothing` |
| A9 | **[app]** Account B's conversation ids are absent from A's visible set, and `require_visible` raises for them | `test_chat_access.py::test_cross_account_invisible` |
| A10 | Concurrent posts in one conversation get gap-free unique `seq` values | `test_chat_service.py::test_seq_is_gap_free_under_concurrency` |
| A11 | A reply to a post in another conversation is refused | `test_chat_service.py::test_reply_must_share_conversation` |
| A12 | Edit: author OK. Another member, a bot post and a deleted post are each refused. `edited_at` is set | `test_chat_service.py::test_edit_rules` |
| A13 | Delete: author OK. Owner/admin OK in a group, refused in a DM they are not part of. Staff refused on others' posts. Body is blanked and reactions are gone | `test_chat_service.py::test_delete_rules` |
| A14 | Reaction toggles on and off. An emoji outside the allowlist is refused | `test_chat_service.py::test_reaction_toggle_and_allowlist` |
| A15 | Unread excludes your own and deleted posts, never goes backwards, and a new member's group unread starts at their join | `test_chat_service.py::test_unread_counts` |
| A16 | **Through `create_app()` with the real `get_db` and both middlewares: `engine.pool.checkedout()` rose while the gate ran and is back at baseline while the stream is open; and the first heartbeat frame arrives before the stream closes (no middleware buffers it)** | `test_chat_stream.py::test_stream_holds_no_connection_and_is_not_buffered` |
| A17 | A post committed in account A reaches A's stream subscriber and never B's. A rolled-back post is never published | `test_chat_stream.py::test_publish_after_commit_only` |
| A18 | **A staff stream never yields an event for a non-visible conversation.** After revocation it yields `revoked` and ends within one refresh | `test_chat_stream.py::test_stream_filters_and_revokes` |
| A19 | Catch-up from `after` replays missed events. An out-of-order commit inside the overlap is still delivered, and duplicates share an `id:` | `test_chat_stream.py::test_catchup_overlap` |
| A20 | Every chat endpoint declares `chat.use`/`ACCOUNT` | `test_route_declarations.py::test_no_undeclared_routes` + `test_chat_routes.py::test_every_chat_route_declares_chat_use` |
| A21 | Opening `/chat/` twice creates exactly one General and one chat per visible property | `test_chat_routes.py::test_lazy_creation_is_idempotent` |
| A22 | A DM with yourself or with a revoked member is refused. Opening an existing DM from either side returns the same conversation | `test_chat_routes.py::test_dm_rules` |
| A23 | **Hostile text:** `<script>`, `"><img onerror>` and `javascript:` links render escaped and unlinked; `https://` links render with `rel="noopener noreferrer nofollow"` | `test_chat_routes.py::test_body_is_escaped_and_safely_linked` |
| A24 | The badge shows the total unread for the viewer's visible set only | `test_chat_routes.py::test_unread_badge` |
| A25 | Posting returns the rendered post, and the event row exists before the response (D10) | `test_chat_routes.py::test_post_commits_before_response` |
| A26 | Templates: both pages in the checklist, partials under `partials/chat/`, CSS stamp current | `test_ui_responsive.py::test_checklist_is_complete`, `test_ui_build.py::test_css_is_current` |
| A27 | **Derived probe [app]:** for every route in `create_app().routes` under `/chat` with a `conversation_id` or `post_id` parameter, a staff request with an id they cannot see — sent with a **valid** payload from a per-route table whose keys must equal the derived route set, so a 422 cannot pass for a 404 — returns **404** and no body content | `test_chat_routes.py::test_every_id_route_enforces_visibility` |
| A28 | The bus delivers to subscribers on two different event loops, and drops to `resync` on overflow | `test_chat_bus.py::test_cross_loop_publish_and_overflow` |
| A29 | With `REALTIME_DATABASE_URL` set, one commit yields exactly one delivery, and none on rollback | `test_chat_notify.py::test_notify_delivers_once_on_commit` |
| A30 | `chat-prune-events` deletes only rows older than 7 days, per account | `test_chat_notify.py::test_prune_events` |
| A31 | **Exit:** owner posts, staff stream receives `post.created`, staff fetches the partial, reacts, and the owner stream receives `reaction.changed`. Run on the app role | `test_chat_e2e.py::test_exit_criterion` |

**A18 and A27 are the definition of done.** Every other criterion can pass while one route forgets
`require_visible`; those two are derived from the live router table and the live stream, so later routes and
event types are covered without editing a list. **A16 is the one most likely to be "fixed" by deleting it** —
a connection-holding stream works for fifteen tabs, and the sixteenth stalls a different page from its cause.

---

## 9. Test manifest

```
tests/unit/test_matrix.py                    A1 (amended pins)
tests/unit/test_chat_bus.py                  A28: two loops in two threads, a bounded queue
tests/integration/test_chat_schema.py        A4, A5: raw inserts against the migrated schema
tests/integration/test_chat_access.py        A6-A9: app role
tests/integration/test_chat_service.py       A10-A15
tests/integration/test_chat_routes.py        A2, A20-A25, A27
tests/integration/test_chat_stream.py        A16-A19: stream_events driven directly + one HTTP stream
tests/integration/test_chat_notify.py        A29, A30: skipped unless REALTIME_DATABASE_URL is set,
                                              and the skip message says A29 was NOT verified
tests/integration/test_chat_e2e.py           A31
```

- **App role.** Seed via `_pg_engine` and **commit** (as `test_gateway_webhook.py::_issue_code`, `:661-683`),
  delete the seeded account on teardown (tenant FKs cascade from `accounts`), then point the app at the role
  with `db._engine, db._SessionLocal = app_role, None` + `web_client_factory` (`test_gateway_webhook.py:698`),
  or use `Session(app_engine)` inside `account_context`. `web_client_as` connects as `postgres` and bypasses
  RLS (`conftest.py:212-230`): **an [app] criterion run through it is a false green.**
- **Streams:** call `stream_events` directly, `open_session` bound to the test's factory and
  `MAX_LIFETIME_S`/`REFRESH_S` monkeypatched small — `web_client_as` puts every request on one savepointed
  connection (`tests/integration/conftest.py:124-140`). **A16 must not use `web_client_as`**: that connection
  stays checked out all test, so "back at baseline" passes trivially. It uses `web_client_factory` with
  `db._engine` pinned and `get_db` un-overridden, `HEARTBEAT_S` small, iterating
  `client.stream("GET", "/chat/stream")` while reading `db._engine.pool.checkedout()`.
- **A10:** two real connections and a barrier. **A27:** assert ≥ 9 id-bearing routes were found (guard on the
  guard, `test_route_declarations.py:311-321`). **Never** leave an [app] assertion behind a skip without a
  message naming the unverified criterion (`test_isolation.py:266`).

---

## 10. What this stage does not make safe

- **Flooding.** No rate limits until SPEC-014.
- **Multi-process without `REALTIME_DATABASE_URL`.** In-process fan-out reaches only same-process streams; with
  two workers or Fly machines, recipients miss live events until reconnect. Correct today only because
  evo-dev runs one process.
- **Visibility lag.** Streams refresh every 60 s: staff removed from Blue can receive Blue's **event ids** (never
  content — the re-fetch 404s) for up to a minute; a revoked membership's stream ends within one refresh.
- **Six-connection limit.** One stream per chat tab on HTTP/1.1 (D14): six chat tabs stall the seventh request.
- **Not end-to-end encrypted.** Owners cannot read others' DMs *through the app* (D4); anyone with database
  access can. Say so in any copy that mentions DMs.
- **`/ai/ask` still holds its connection while streaming** (§0.2) — bounded by answer length, not fixed here.
