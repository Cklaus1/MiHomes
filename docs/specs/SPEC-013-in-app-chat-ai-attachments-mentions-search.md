# SPEC-013 — In-app chat: AI, attachments, mentions, search

**Phase:** Native chat, Phases 4–5 (plan `plan-the-native-in-app-validated-dolphin.md`)
**Status:** Ready to build once SPEC-012 lands. **2 open decisions** (O1 attachment bytes after a post is deleted; O2 a per-account switch for AI in chats). Both have defaults, so neither blocks the build
**Written:** 2026-10-28
**Verified against:** `worktree-chat-specs` @ **`9c7d595`**, which is the `worktree-spec-build-harness` tip. Every `file:line` below was read at that ref. **SPEC-012 exists only as an uncommitted draft, written in parallel, and none of it is built.** Its models, `services/chat/*` and `routes/chat.py` are cited by the cross-spec contract, not by line (§0.6)
**Depends on:** SPEC-012 (migration `0027_native_chat`, `models/chat_native.py`, `services/chat/{access,posts,events}.py`, `routes/chat.py`, key `chat.use`), SPEC-006 (the `GatewayAdapter` seam, D11 account-at-the-edge, `AccountMismatch`), SPEC-003 (`scoped_property_ids`, `authz_context`)

**Goal.** Group chats get the same AI as the Telegram group: a message can become an issue or task,
and the bot answers in the chat. Members can attach photos and PDFs, @mention people who can see
the conversation, and search everything they are allowed to read.

**Exit criterion:** an owner posts "the pool pump is leaking" with a photo in a property chat. A
staff member in that chat sees "MiHomes is thinking…", then a bot post reading `"Pool pump
leaking" logged ✓`. The issue exists with the photo attached as a private document. The same
staff member can download the photo, @mention the owner and find the post by searching "pump".
A staff member scoped to a different property gets a 404 on that photo, cannot mention into the
chat, and gets no search results from it.

**The stake.** Native chat moves the AI pipeline from rooms whose membership we could only guess
at (Telegram groups) to rooms whose membership **we compute**. That is a chance to close a leak
the Telegram path still has (§0.1). The risk is copying that path as-is, because three things in
it are unsafe for a room with staff in it: finance answers, photo storage, and property choice.
Each one is wrong in a way that looks like the feature working.

---

## 0. Six things a reader must know before trusting this spec

### 0.1 — "Same as Telegram" would leak finances and other properties into staff-readable rooms

The bot's Q&A runs in a `BackgroundTasks` thread with only `account_context` bound
(`web/routes/gateways.py:296-297`). **No role and no property scope are bound.** Measured:

- `services/ai/context.py:21-44` (`_denied_categories`): *"Empty when no role is bound."*
- `dispatch_items` answers questions as `role="estate_manager"` (`review_common.py:1057`). That
  role's `data_categories` include `budgets`, `contracts`, `insurance` and `staff`
  (`services/ai/roles.py:65`).
- With `current_property_scope` = `None`, the query-scope listener returns early, so every row
  in the account is visible (`authz/query_scope.py:407-420`).

So a question in a group puts the whole account's "Budget Status (YTD)" into the prompt, and the
answer goes to everyone in the room. **`telegram/financial_guard.py` was written for exactly this
case, and nothing calls it:** `grep -rn screen_financial_answer src/` finds only its definition.
This spec does not fix Telegram (§10). It adds a hook, `answer_scope`, that the native channel
uses (D6–D8), and leaves Telegram free to adopt it later.

### 0.2 — Passing a photo to the AI as-is would publish it at an unauthenticated URL

`_create_issue_with_photos` copies each `mediaPath` into `uploads_dir()` and records the file as
`/static/uploads/<hex>` (`review_common.py:1443-1460`). `uploads_dir()` is
`src/mihomes/web/static/uploads` (`:260-271`), which sits inside `STATIC_DIR` (`web/app.py:54`).
`STATIC_DIR` is mounted as a plain `StaticFiles` with no authentication (`web/app.py:123`). A chat photo that `/chat/file/{id}` protects with a visibility check would become a public URL
the moment it was filed on an issue. **D10 moves that copy into tenant-keyed storage.** That also
closes the same exposure for Telegram photos, which is the only Telegram behaviour this spec
changes.

### 0.3 — `read_document_upload` cannot be reused without changing it

- The category is hardcoded: `build_key(require_account(), "documents", filename)` (`web/forms.py:35`).
- It returns only the key (`:85`). There is no size, content type or original filename, and
  `ChatAttachment` needs all three.
- **Its content-type check never fires.** At `:69`, `not ext_ok or (content_type and not type_ok
  and suffix not in allowed_ext)`: the second clause needs `suffix not in allowed_ext`, which
  means `not ext_ok`. So the client's `Content-Type` is never checked, and storing it would let
  the client decide how a download is served.

D11 adds `store_validated_upload(..., category=)`. It returns metadata and derives the content
type from the validated extension. `read_document_upload` keeps its signature.

### 0.4 — Dedup protects against re-dispatch, not against double-submit

`ProcessedIdStore` (`services/gateways/dedup.py:32-74`) is a JSON list in one `configurations`
row. `contains` and `add` each open their own `get_session()` and do read-modify-write, so
**it is not atomic**: two threads dispatching the *same* id at the same moment can both pass
`contains`. And **a double-submitted composer creates two posts with two different ids**, which
no id store can catch. So the criterion is narrowed honestly. **The same post is analysed at most once** (A9) because
the id is claimed before processing (the `gateways.py:315-321` pattern). The route schedules
dispatch only for a post it has just created (A10). Collapsing double-submits is SPEC-012's job
(§2 P4). The store also needs `account_context`: `Configuration` is a tenant table
(`gateways.py:26-29`).

### 0.5 — `handle_inbound` drops a message with no property, and does it silently

`groups = group_by_target([m for m in messages if m.get("propertySlug") or property_slug])`
(`review_common.py:927`). With no property, the only result is an `errors` entry,
`"No linked chat found"`, and nobody is told. `resolve_default_property` (`:356-366`) returns a
slug only when the account has exactly one property. **The General chat therefore has to resolve
its property before calling `handle_inbound`** (D4). Doing it afterwards would mean
reimplementing the filter.

### 0.6 — What this spec assumes from SPEC-012 (the contract), and must not redefine

Migration `0027_native_chat` creates `chat_attachments`, `chat_mentions` and the generated
`chat_posts.search_tsv` ('simple', GIN), so **this spec adds no migration**. It calls
`visible_conversation_ids`, `require_visible`, `create_post`, `create_bot_post` and
`events.emit` (after commit), plus `ChatNotFound`, `BUS.publish` and `render.tokenize_body`
from SPEC-012's draft (now in this worktree, unbuilt). It adds routes under `/chat` that carry
`@declares("chat.use", Access.ACCOUNT)`.

Where the contract falls short, §2 asks SPEC-012 for the change. This spec does not patch around it.

---

## 1. Decisions

### 1.1 Locked

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Only `estate` and `property` conversations dispatch; a `dm` never does.** The route checks this, and `dispatch` checks it again | User decision: DMs are people-only. The second check means a future caller that forgets the route check still cannot send a DM to the model |
| **D2** | **New channel `"mihomes"` plugs into `handle_inbound` through a `GatewayAdapter`. Its `send` calls `create_bot_post`, and only into the source conversation** | SPEC-006 D2: the adapter is the extension point, so all 14 dispatch branches, trust and `AccountMismatch` come for free. `send` raises for any other target, so a reply cannot be posted into a different room |
| **D3** | **The `InternalMessage` fields map as follows: `channel="mihomes"`, `jid=str(conversation_id)`, `isGroup=True`, `sender=str(membership_id)`, `id=str(post_id)`, `timestamp` = `created_at` in ISO UTC, and `propertySlug` = the resolved slug.** Each extra image is one more envelope with id `"{post_id}:{n}"` | The envelope has all 12 keys (`gateways/message.py:18-30`, `ENVELOPE_KEYS`). It carries only one `mediaPath`, and `_load_image_attachments` (`review_common.py:506-544`) already handles text-less media messages |
| **D4** | **The property is chosen before `handle_inbound`.** A property chat uses its own property. In General the candidates are the **author's** `scoped_property_ids`: if there is exactly one, use it. Otherwise use the single candidate whose name or slug appears in the text as a whole word. If none or several match, nothing is logged, and the bot asks only when analysis found something actionable (D5) | §0.5. The candidates are limited to the author's scope, so a staff member cannot file an issue into a property they cannot see by naming it. For a single-property account this is the contract's "sole property" rule |
| **D5** | **When General cannot resolve a property, run the analysis once with no property. If any item is not `informational`, the bot posts `ASK_WHICH_PROPERTY`. Otherwise it stays silent.** The question never names any property | Asking "which property?" in reply to "good morning" would make General unusable. This costs the same one AI call a resolved message costs. Listing property names would show a staff member properties outside their scope |
| **D6** | **`GatewayAdapter` gains an optional `answer_scope(session, question, property_slug)` context manager.** `dispatch_items` enters it only around `ai_response` (`:1057`) and `issue_expert_reply` (`:1403`). It yields replacement text, or `None` to go ahead | Wrapping all of `handle_inbound` in a staff role would break the write path: with staff bound and no `current_user`, PERSONNEL criteria hide every `Staff` row (`query_scope.py:368-380`, `:443-449`), so reporter, assignee and PTO lookups would all fail. **Writes run as the system; only answers are narrowed to the audience.** The default `None` leaves Telegram byte-for-byte unchanged |
| **D7** | **Native audience rule.** Audience = `members_who_can_see(conversation)`. If any member of the audience is staff: (a) a money question (`is_financial_question`) is refused, and a staff asker gets `STAFF_REFUSAL`; (b) the property must be in **every** audience member's scope, otherwise `OUTSIDE_AUDIENCE`; (c) the answer runs under `authz_context("staff", frozenset({property_id}))` | This is `financial_guard`'s asker/audience split (`telegram/financial_guard.py:107-136`), applied to a membership we can actually enumerate. (c) brings in the existing `_denied_categories` and the row scope, so an unlisted money keyword still finds no budget data. **One consequence is intended:** where staff are split across properties, or a staff member has no scope, the intersection for General is empty, so every General question gets `OUTSIDE_AUDIENCE`. That is fail-closed, not a bug (§10) |
| **D8** | **A rule (b) refusal on an issue's expert note is silent.** The issue is still logged and confirmed, just without the note | The note is advisory. A refusal sentence attached to a confirmation reads as an error |
| **D9** | **Trust comes from the membership row only.** `is_trusted_sender` gets a `mihomes` branch *before* the allowlist read (`:757`): trusted means active, in the bound account, and role owner or admin. Staff are untrusted. `is_approver` uses the same predicate | The sender id is a membership UUID that the server wrote. An allowlist key would be a second way to grant trust, and it could drift. Staff are untrusted for the same reason unlinked Telegram members are (`gateways.py:142-145`) |
| **D10** | **`_create_issue_with_photos` writes through storage.** It copies the bytes to `build_key(account, "documents", name)` and passes `file_path=<key>` to `create_document` | §0.2. `is_storage_key` (`storage/__init__.py:146`) already treats keys as first-class document paths, and `/documents/file/{key}` serves them with an account check. The issue page already renders keys correctly: `partials/docs_section.html:10` pipes through `doc_url` (`web/deps.py:123-137`), which turns a key into `/documents/file/<key>`. The issue keeps its own copy, so deleting the chat post does not break it |
| **D11** | **`store_validated_upload(file, *, category)` → `StoredUpload(key, filename, content_type, size)`.** The content type comes from the validated extension. Allowed types are jpg, png, gif, webp and pdf, up to 25 MB each (`forms.py:14-22`). At most 5 files per post | §0.3. A single validator, used with a category argument. SVG and HTML stay rejected (`forms.py:11-13`) |
| **D12** | **`GET /chat/file/{attachment_id}`**: attachment → post → conversation must be in `visible_conversation_ids`, the post must not be deleted, and the key prefix must match the caller's account. Every failure returns **404**. The route always proxies the bytes and never redirects to a presigned URL | `/documents/file/{key}` checks only the key's account prefix (`documents_download.py:58-62`) and requires `inventory.manage` (`:48`), so it is wrong in both directions. A presigned S3 redirect (`:69-71`) would drop our `Content-Disposition` and `nosniff` headers. 404 instead of 403 for the same reason as `:59-61` |
| **D13** | **Only images are served inline; PDFs are downloaded as attachments.** Responses carry `nosniff`, `private, no-store` and `CSP: sandbox` | Contract. A PDF shared in chat is a download. Inline PDF rendering is where most viewer exploits live |
| **D14** | **The mention body stays plain text (`@Maria Lopez`).** The server re-resolves mentions against `members_who_can_see`, using the autocomplete's `mention_ids` only as a hint to break ties | Stored tokens such as `<@uuid>` would pollute `search_tsv` and the edit textarea. The hint is checked again on the server, so a forged id for a member who cannot see the conversation is dropped |
| **D15** | **All user text is rendered from server-built `Segment`s with autoescape. `|safe` is never applied to user text.** Links are `http(s)` only. Search highlights use sentinel characters, not HTML | `ts_headline`'s default `<b>` markup would need `|safe`, and `|safe` on a column holding user text is stored XSS |
| **D16** | **Search uses `websearch_to_tsquery('simple', q)` against `search_tsv`.** It is restricted to the visible set and to `deleted_at IS NULL`, ordered by `(created_at, id)` descending with keyset pagination, 20 per page, and the query is capped at 200 characters | Plan §5. `websearch_to_tsquery` cannot fail on user syntax. Chronological order beats rank for chat, and a keyset cursor stays stable while new posts arrive |
| **D17** | **"MiHomes is thinking…" is driven by transient `ai.thinking` and `ai.done` events.** `ai.done` is emitted in a `finally` | Without a guaranteed terminal event the indicator would stay up forever after an informational post or a failed analysis. AI on evo-dev takes 70–110 s (NIM) |

### 1.2 `OPEN — needs decision: founder`

| # | Question | Default if undecided |
|---|---|---|
| **O1** | When a post is soft-deleted, should its attachment bytes be purged from storage, or kept but unreachable? | **Kept, and the download returns 404.** Purging cannot be undone, and SPEC-012's soft delete exists so the record can be recovered. Real erasure is a privacy-deletion job (§10) |
| **O2** | Should an account be able to turn off the AI in chats, or per conversation? | **Always on**, for parity with Telegram. If the answer is yes, `should_dispatch` is where the switch belongs |

---

## 2. Doc-fix prerequisites

| # | Fix | Why |
|---|---|---|
| **P1** | Confirm against SPEC-012's draft. Transient events go through `BUS.publish(account_id, {"type", "c", "p"})`, which skips `chat_events` exactly as the typing route does. `BUS.publish` is thread-safe (SPEC-012 D16, `call_soon_threadsafe`). The stream forwards any `type` whose `"c"` is in the visible set, so SPEC-012 must not filter by an allowlist of types, and its `chat.js` gets the `ai.*` handlers | D17. "Thinking" is ephemeral; replaying it on catch-up would show a stale spinner. Nothing is added to `EVENT_TYPES` |
| **P2** | **Adopted into SPEC-012 §5.** `create_post(..., allow_empty: bool = False)` for a photo-only post (it otherwise rejects an empty body with `ChatInvalid`) | A photo with no caption is the most common chat attachment |
| **P3** | Nothing to request. SPEC-012's `require_visible` / `post_for` raise `ChatNotFound` → 404, which is what D12 needs. SPEC-012 creates no `mentions.py`, although the plan's Phase 2 lists one, so this spec owns it | Recorded so the plan's Phase 2 list is not read as a conflict |
| **P4** | **Adopted into SPEC-012 §4/§5 (0027 + `create_post(client_token=)`).** A composer idempotency key, `chat_posts.client_token UUID NULL` with a partial unique index on `(conversation_id, client_token)`, added in `0027`. `create_post` returns the existing row on a repeat | §0.4. Double-submit is a posting problem, and the column belongs to SPEC-012's migration. Without it, `hx-disabled-elt` on the send button is the only guard |
| **P5** | The plan's `ProcessedIdStore("mihomes.processed")` → **`"mihomes.processed_ids"`** | Every other store uses `<channel>.processed_ids` (`telegram/extractor.py:14`, `whatsapp/extractor.py:19`) |
| **P6** | Reuse SPEC-012's `web_client_as(membership)` with a seeded estate: owner, admin, staff scoped to property A, staff scoped to property B, properties A and B. `web_client_as` connects as `postgres`, so the **[app]** criteria below also swap `db._engine` to `app_engine` | Every access criterion below needs these roles |

---

## 3. File manifest

### New
| Path | What |
|---|---|
| `src/mihomes/services/chat/ai_bridge.py` | `should_dispatch`, `resolve_chat_property`, `build_messages`, `mihomes_adapter`, `run`, `dispatch` |
| `src/mihomes/services/chat/audience.py` | `members_who_can_see`, `audience`, `display_name`. The **inverse** of `visible_conversation_ids` |
| `src/mihomes/services/chat/attachments.py` | `attach`, `load_for_download`, `download_headers` |
| `src/mihomes/services/chat/mentions.py` | `mentionable`, `resolve_mentions`, `record_mentions`, `mention_names_for` |
| `src/mihomes/services/chat/search.py` | `search_posts`, `SearchHit`, `SearchPage` |
| `src/mihomes/web/templates/chat/_segments.html` | the `render_segments` macro |
| `src/mihomes/web/templates/chat/search.html`, `_search_results.html`, `_mentionable.html`, `_attachment.html` | partials |
| tests | §9 |

### Modified
| Path | Change |
|---|---|
| `src/mihomes/services/gateways/review_common.py` | (1) `ChannelAdapter`/`GatewayAdapter` gain `answer_scope` (`:51-96`). (2) `dispatch_items` enters it at `:1055-1064` and `:1402-1405`. (3) `is_trusted_sender` gains a `mihomes` branch before `:757`, plus a `trusted_membership` helper. (4) `_with_conversation_log` returns early for `channel == "mihomes"` (`:856-888`). (5) `_create_issue_with_photos` writes through storage (`:1443-1464`) |
| `src/mihomes/services/chat/render.py` (SPEC-012 `tokenize_body`, D18) | gains `mention` and `hit` segment kinds (§5.6); stays the only path from user text to HTML |
| `src/mihomes/web/forms.py` | `_store_bytes(..., category="documents")`; new `StoredUpload` and `store_validated_upload`; `read_document_upload` unchanged on the outside |
| `src/mihomes/web/routes/chat.py` (SPEC-012) | the post route accepts `files` and `mention_ids`, commits, then schedules dispatch; adds `GET /chat/file/{id}`, `GET /chat/c/{id}/mentionable`, `GET /chat/search`; the conversation page accepts `?at=<seq>` |
| `templates/chat/_post.html`, `_composer.html` (SPEC-012) | segments macro, attachment thumbnails, file input, @-autocomplete, thinking row |
| `src/mihomes/web/static/chat.js` (SPEC-012) | `ai.thinking`/`ai.done` handlers; mention picker (`hx-get` on `@`) |
| `docs/UI_MANUAL_CHECKLIST.md` | rows for upload, thinking, mention, search; then `npm run build:css && npm run stamp` |

---

## 4. Schemas as code

**No migration.** These are the SPEC-012 columns this spec writes, quoted from the contract so
that a mismatch shows up at review:

```python
# models/chat_native.py (SPEC-012) — the slice SPEC-013 depends on
ChatAttachment: id (UUID PK), post_id (FK, drift-guarded),
    storage_key  # "{account}/chat/{hex}{ext}" from build_key
    filename     # display only, never a path
    content_type # server-derived (D11), never the client header
    size_bytes   # SPEC-012's name; filled from StoredUpload.size
ChatMention: post_id, membership_id, notified_at  # SPEC-013 inserts NULL; SPEC-014 sets it
ChatPost: id, conversation_id, seq, author_membership_id, author_kind ("member"|"bot"),
    body, created_at, deleted_at, search_tsv  # generated to_tsvector('simple', body) + GIN
```

Value types introduced by this spec:

```python
# web/forms.py
@dataclass(frozen=True)
class StoredUpload:
    key: str            # tenant-prefixed storage key
    filename: str       # basename of the client's name, <=255 chars, display only
    content_type: str   # EXTENSION_TYPES[suffix]
    size: int

EXTENSION_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                   ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf"}

# services/chat/render.py — SPEC-012's token type; if it already has one, add the two kinds to it
@dataclass(frozen=True)
class Segment:
    kind: Literal["text", "mention", "link", "hit"]
    text: str
    href: str | None = None   # only for "link"; always http(s)
```

---

## 5. Function signatures

### 5.1 `services/gateways/review_common.py` — the five edits

```python
class ChannelAdapter(Protocol):
    ...
    #: D6. Wraps AI *answers* only (question replies, issue expert notes), never writes.
    #: Yields text to send instead of answering, or None to answer under whatever it binds.
    answer_scope: Callable[[Session, str, str | None], AbstractContextManager[str | None]] | None

@dataclass
class GatewayAdapter:
    ...
    answer_scope: Callable[[Session, str, str | None], AbstractContextManager[str | None]] | None = None

def _answer_context(adapter, session: Session, question: str, property_slug: str | None):
    guard = getattr(adapter, "answer_scope", None)
    return guard(session, question, property_slug) if guard else nullcontext(None)

# dispatch_items, question branch (replaces :1057):
with _answer_context(adapter, session, question_text, prop or property_slug) as refusal:
    answer = refusal if refusal is not None else ai_response(
        session, question_text, role="estate_manager", property_slug=prop or property_slug)

# dispatch_items, issue branch (replaces :1403):
with _answer_context(adapter, session, title, prop) as refusal:
    expert_note = None if refusal is not None else issue_expert_reply(
        session, title, item.get("description"), prop)

def trusted_membership(session: Session, sender: str, account) -> bool:
    """D9. Active owner/admin membership of `account`; anything unparsable or unbound is False."""
    try:
        membership_id = uuid.UUID(str(sender))
    except ValueError:
        return False
    account_id = getattr(account, "id", account)
    if account_id is None:
        return False                       # fail closed: no tenant, no trust
    m = Membership.__table__.c
    role = session.execute(
        select(m.role).where(m.id == membership_id, m.account_id == account_id, m.status == "active")
    ).scalar_one_or_none()
    return role in _PRIVILEGED_ROLES       # authz/scope.py:99

# is_trusted_sender — inserted after the empty-sender check (:753-755), BEFORE the allowlist (:757):
    if gateway == "mihomes":
        return trusted_membership(session, raw_sender, account if account is not None else bound_account())

# _with_conversation_log — first statement (:865):
    if getattr(adapter, "channel", "") == "mihomes":
        return adapter    # native posts are their own record; chat_messages is the Telegram log

# _create_issue_with_photos — replaces the uploads_dir() copy (:1443-1460), D10:
    data = Path(media_path).read_bytes()
    key = build_key(require_account(), "documents", media_path)
    get_storage().put(key, data, content_type=mime)
    create_document(session, title=f"Photo {idx + 1} — {title}", file_path=key, ...)
```

### 5.2 `services/chat/ai_bridge.py`

```python
CHANNEL = "mihomes"
PROCESSED_IDS_KEY = "mihomes.processed_ids"         # P5
MAX_PROCESSED_IDS = 5000
AI_KINDS = frozenset({"estate", "property"})        # D1: never "dm"
#: UUID-shaped ids only. Every owner/admin is an approver here (on Telegram there is one approver
#: id), so `handle_approval_messages`' loose `APPROVE\s+(\S+)` (review_common.py:662) would turn an
#: owner's "Approve the paint quote" into approve_pto("the") followed by "Could not approve #the".
APPROVAL_COMMAND = re.compile(
    r"^\s*(APPROVE|DENY)\s+[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
ASK_WHICH_PROPERTY = ("Which property is this about? Post it in that property's chat, "
                      "or include the property's name.")
FINANCE_IN_SHARED_ROOM = ("That's a financial question, and this chat includes people who "
                          "can't see finances. Ask it on the Ask AI page instead.")
OUTSIDE_AUDIENCE = ("I can't answer that here: not everyone in this chat can see that "
                    "property. Ask in that property's chat.")

@dataclass(frozen=True)
class PropertyRoute:
    slug: str | None
    property_id: uuid.UUID | None

def should_dispatch(conversation: ChatConversation, post: ChatPost) -> bool:
    return (conversation.kind in AI_KINDS and post.author_kind == "member"
            and post.deleted_at is None)

def resolve_chat_property(session: Session, conversation: ChatConversation,
                          author: Membership, text: str) -> PropertyRoute:
    """D4. Property chat → its property. General → the author's sole scoped property, else the
    single scoped property named in `text` (whole word, name or slug), else PropertyRoute(None, None)."""
    if conversation.kind == "property":
        prop = session.get(Property, conversation.property_id)
        return PropertyRoute(prop.slug, prop.id)
    allowed = scoped_property_ids(session, author)
    props = (session.execute(select(Property).where(Property.id.in_(allowed))).scalars().all()
             if allowed else [])
    if len(props) == 1:
        return PropertyRoute(props[0].slug, props[0].id)
    lowered = (text or "").lower()
    hits = [p for p in props if any(
        re.search(rf"(?<!\w){re.escape(t.lower())}(?!\w)", lowered)
        for t in {p.name, p.slug, p.slug.replace("-", " ")} if t)]
    return PropertyRoute(hits[0].slug, hits[0].id) if len(hits) == 1 else PropertyRoute(None, None)

def build_messages(session: Session, post: ChatPost, *, conversation: ChatConversation,
                   author: Membership, property_slug: str | None,
                   media_dir: Path) -> list[InternalMessage]:
    """D3. One envelope for the text + first image; one more per extra image. PDFs become a
    ' [file: name]' suffix (no vision). Images are materialised into `media_dir` (a
    TemporaryDirectory owned by `dispatch`) because `_load_image_attachments` needs a real path
    (`review_common.py:519`): media_dir / f"{uuid4().hex}{suffix of storage_key}", whose bytes
    come from get_storage().get(key). The suffix matters, because mimetypes.guess_type decides
    whether the file is an image."""

def mihomes_adapter(session: Session, conversation: ChatConversation, *,
                    author: Membership, route: PropertyRoute) -> GatewayAdapter:
    """send → create_bot_post into `conversation` only (raises on any other target);
    is_approver → trusted_membership; reporter_for → the Staff row whose user_id is the
    sender membership's user_id, else name match; analyze → analyze_messages with every
    item's property_slug pinned to route.slug (no cross-property writes from a chat);
    answer_scope → _answer_scope(conversation, author)."""
    def send(target: str, text: str) -> None:
        if target != str(conversation.id):
            raise ValueError("mihomes adapter replies only into the source conversation")
        create_bot_post(session, conversation.id, text)

    def analyze(s, convo, *, property_name=None, property_slug=None):
        result = review_common.analyze_messages(s, convo, property_name=property_name,
                                                property_slug=property_slug)
        for item in result.get("items", []):
            item["property_slug"] = route.slug
        return result

    return GatewayAdapter(
        label="MiHomes chat", channel=CHANNEL, send=send,
        is_approver=lambda m: bool(APPROVAL_COMMAND.match(m.get("text") or "")) and
                              trusted_membership(session, m.get("sender"), author.account_id),
        reporter_for=_reporter_for, analyze=analyze,
        answer_scope=_answer_scope(conversation, author),
    )

def _answer_scope(conversation: ChatConversation, author: Membership):
    """D7/D8. Bound per conversation; computed once per dispatch."""
    @contextmanager
    def scope(session: Session, question: str, property_slug: str | None):
        aud = audience(session, conversation)
        if aud.has_staff and is_financial_question(question):
            yield STAFF_REFUSAL if author.role not in _PRIVILEGED_ROLES else FINANCE_IN_SHARED_ROOM
            return
        prop_id = _property_id(session, property_slug)
        if aud.has_staff and (prop_id is None or prop_id not in aud.scope):
            yield OUTSIDE_AUDIENCE
            return
        ctx = authz_context("staff", frozenset({prop_id})) if aud.has_staff else nullcontext()
        with ctx:
            yield None
    return scope

def run(session: Session, post: ChatPost, *, conversation: ChatConversation,
        author: Membership, media_dir: Path) -> dict:
    route = resolve_chat_property(session, conversation, author, post.body)
    adapter = mihomes_adapter(session, conversation, author=author, route=route)
    messages = build_messages(session, post, conversation=conversation, author=author,
                              property_slug=route.slug, media_dir=media_dir)
    if route.slug is None and not APPROVAL_COMMAND.match(post.body or ""):
        return _ask_if_actionable(session, messages, adapter)          # D5
    return review_common.handle_inbound(session, messages, adapter=adapter,
                                        property_slug=route.slug)

def dispatch(account_id: uuid.UUID, conversation_id: uuid.UUID, post_id: uuid.UUID) -> None:
    """BackgroundTasks entry point (AnyIO worker thread). Mirrors gateways.py:_dispatch.
    `conversation_id` is passed in, not looked up, so that `ai.done` fires on EVERY return path:
    already claimed, post gone, author revoked, or a crash. The route has already rendered
    the thinking row."""
    try:
        with account_context(account_id):
            store = ProcessedIdStore(PROCESSED_IDS_KEY, cap=MAX_PROCESSED_IDS)
            if store.contains(str(post_id)):
                return
            store.add([str(post_id)])        # claim BEFORE processing (gateways.py:315-321)
            with get_session() as session, tempfile.TemporaryDirectory(prefix="mihomes-chat-") as tmp:
                post = session.get(ChatPost, post_id)
                conversation = post and session.get(ChatConversation, post.conversation_id)
                if (conversation is None or conversation.id != conversation_id
                        or not should_dispatch(conversation, post)):
                    return
                author = session.get(Membership, post.author_membership_id)
                if author is None or author.status != "active":
                    return
                _signal(account_id, conversation_id, post_id, "ai.thinking")
                run(session, post, conversation=conversation, author=author, media_dir=Path(tmp))
    except Exception:
        logger.exception("chat ai: post %s failed", post_id)   # no retry: the id is claimed
    finally:
        _signal(account_id, conversation_id, post_id, "ai.done")

def _signal(account_id, conversation_id, post_id, type_: str) -> None:
    """P1: transient, like typing. Never a chat_events row, so never replayed."""
    BUS.publish(account_id, {"type": type_, "c": str(conversation_id), "p": str(post_id)})

def _ask_if_actionable(session: Session, messages: list[InternalMessage],
                       adapter: GatewayAdapter) -> dict:
    """D5. review_common.analyze_messages(session, messages) with no property. If any item is
    not "informational", adapter.send(jid, ASK_WHICH_PROPERTY). Never dispatch_items. If the
    analysis raises, send adapter.error_text, as handle_inbound does at :949-958."""

def _reporter_for(session: Session, convo: list[dict]) -> Callable[[dict], int | None]:
    """Staff.id where Staff.user_id == the sender membership's user_id (models/staff.py:110),
    else review_common.resolve_reporter_by_name(item["reported_by"])."""

def _property_id(session: Session, property_slug: str | None) -> uuid.UUID | None:
    """Property.id for the slug, or None (unknown or missing → the answer is refused)."""
```

The client-side thinking row also expires after 180 s. Transient events are not replayed, so a
stream that reconnects during the wait would otherwise miss `ai.done`.

The bot posts commit when `get_session` exits, and their after-commit `post.created` events fire
before `ai.done`. **A bot post never schedules a dispatch:** `create_bot_post` is not the route,
and `should_dispatch` rejects `author_kind == "bot"`. That rules out a loop of the bot replying
to itself.

### 5.3 `services/chat/audience.py`

```python
@dataclass(frozen=True)
class Audience:
    has_staff: bool
    scope: frozenset[uuid.UUID] | None   # intersection of every member's scope; None if no staff

def members_who_can_see(session: Session, conversation: ChatConversation) -> list[Membership]:
    """Active memberships for which conversation.id ∈ visible_conversation_ids(m).
    estate → all active; property → active owner/admin + active staff with a
    MembershipPropertyScope row for conversation.property_id; dm → active participants.
    Must agree with access.visible_conversation_ids — A17 checks every (member, conversation) pair."""

def audience(session: Session, conversation: ChatConversation) -> Audience:
    members = members_who_can_see(session, conversation)
    if all(m.role in _PRIVILEGED_ROLES for m in members):
        return Audience(False, None)
    scopes = [scoped_property_ids(session, m) for m in members]
    return Audience(True, frozenset.intersection(*scopes))

def display_name(session: Session, membership: Membership) -> str:
    """users.name, else the email's local part. Escaped wherever rendered."""
```

### 5.4 Attachments: `web/forms.py`, `services/chat/attachments.py`, routes

```python
# web/forms.py
async def store_validated_upload(file, *, category: str,
                                 max_bytes: int = MAX_DOCUMENT_BYTES) -> StoredUpload: ...
def _store_bytes(data: bytes, filename: str, *, content_type: str | None = None,
                 category: str = "documents") -> str: ...

# services/chat/attachments.py
MAX_FILES_PER_POST = 5
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
class AttachmentNotFound(LookupError): ...

def attach(session: Session, post: ChatPost, uploads: list[StoredUpload]) -> list[ChatAttachment]:
    """One ChatAttachment per upload: storage_key=u.key, filename=u.filename,
    content_type=u.content_type, size_bytes=u.size (SPEC-012's column name)."""

def load_for_download(session: Session, membership: Membership,
                      attachment_id: uuid.UUID) -> ChatAttachment:
    row = session.execute(
        select(ChatAttachment, ChatPost.conversation_id, ChatPost.deleted_at)
        .join(ChatPost, ChatPost.id == ChatAttachment.post_id)
        .where(ChatAttachment.id == attachment_id)
    ).one_or_none()
    if (row is None or row.deleted_at is not None
            or row.conversation_id not in visible_conversation_ids(session, membership)
            or key_account(row.ChatAttachment.storage_key) != str(membership.account_id)):
        raise AttachmentNotFound(attachment_id)
    return row.ChatAttachment

def download_headers(att: ChatAttachment) -> dict[str, str]:
    disposition = "inline" if att.content_type in IMAGE_TYPES else "attachment"
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", att.filename)[:120] or "file"
    return {
        "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; "
                               f"filename*=UTF-8''{quote(att.filename[:200], safe='')}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "sandbox; default-src 'none'",
    }

# web/routes/chat.py
@router.get("/file/{attachment_id}")
@declares("chat.use", Access.ACCOUNT)
def chat_file(attachment_id: uuid.UUID, principal: RequestPrincipal = require_authenticated(),
              db: Session = Depends(get_db)) -> Response:
    """404 for every refusal (D12). Never storage.url(): always proxied."""

@router.post("/c/{conversation_id}/posts", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
async def send_post(request: Request, conversation_id: uuid.UUID, background: BackgroundTasks,
                    body: str = Form(""), reply_to_id: uuid.UUID | None = Form(None),
                    mention_ids: list[uuid.UUID] = Form(default=[]),
                    files: list[UploadFile] = File(default=[]),
                    principal: RequestPrincipal = require_authenticated(),
                    db: Session = Depends(get_db)) -> HTMLResponse:
    """Order is the design:
    1. require_visible (threadpool) → 404. Nothing is stored for a non-member.
    2. at most MAX_FILES_PER_POST; store_validated_upload(f, category="chat") each → 422 on ValueError.
    3. threadpool: create_post(allow_empty=bool(uploads)) + attach + resolve/record_mentions,
       then db.commit() EXPLICITLY. The background thread re-reads the post by id in a fresh
       session, so it must not depend on when get_db's teardown runs.
    4. on any exception after step 2, delete the stored keys (no orphan objects), re-raise.
    5. if should_dispatch(conversation, post): background.add_task(ai_bridge.dispatch,
       principal.account_id, conversation.id, post.id). Return the _post.html partial plus a thinking row."""
```

### 5.5 `services/chat/mentions.py`

```python
MENTIONABLE_LIMIT = 8

@dataclass(frozen=True)
class Mentionable:
    membership_id: uuid.UUID
    name: str

def mentionable(session: Session, conversation: ChatConversation, *, q: str,
                exclude: uuid.UUID) -> list[Mentionable]:
    """members_who_can_see(conversation) minus `exclude`, filtered by the name prefix
    (case-insensitive, escape_like), first MENTIONABLE_LIMIT by name."""

def resolve_mentions(session: Session, conversation: ChatConversation, body: str,
                     hinted_ids: Iterable[uuid.UUID]) -> list[Membership]:
    """D14. candidates = members_who_can_see. A candidate is mentioned iff `@<name>` occurs in
    `body` at a word boundary AND (it was hinted OR its name is unique among candidates).
    A hinted id not among the candidates is dropped silently."""

def record_mentions(session: Session, post: ChatPost, members: list[Membership]) -> list[ChatMention]:
    """One row per distinct membership, author excluded, notified_at=NULL (SPEC-014 reads it)."""

def mention_names_for(session: Session, post_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """Batch lookup for rendering a page of posts (one query)."""

# web/routes/chat.py — GET /c/{conversation_id}/mentionable?q=  (@declares chat.use ACCOUNT)
# require_visible → 404; renders chat/_mentionable.html from mentionable(...)
```

### 5.6 `services/chat/render.py`

```python
HIT_START, HIT_STOP = "\x02", "\x03"
_URL = r"https?://[^\s<>\"'\x02\x03]+"
_TRAILING = ".,;:!?)]}'\""

def tokenize_body(body: str, *, mention_names: Iterable[str] = ()) -> list[Segment]:
    """SPEC-012's tokenizer (D18), extended with two keyword args and two kinds; the default
    arguments keep every SPEC-012 call site unchanged. Split user text into Segments: URLs (http/https only, trailing punctuation trimmed) →
    "link"; `@<name>` for a name in mention_names (longest first, case-insensitive, followed by a
    non-word char) → "mention"; HIT_START…HIT_STOP → "hit"; everything else → "text".
    Never returns markup. Templates render Segments with autoescape on."""
```

```jinja
{# chat/_segments.html — the only renderer of user text #}
{% macro render_segments(segments) %}{% for s in segments %}
  {%- if s.kind == "mention" %}<span class="chat-mention">{{ s.text }}</span>
  {%- elif s.kind == "link" %}<a href="{{ s.href }}" rel="noopener noreferrer nofollow ugc" target="_blank">{{ s.text }}</a>
  {%- elif s.kind == "hit" %}<mark>{{ s.text }}</mark>
  {%- else %}{{ s.text }}{% endif %}
{%- endfor %}{% endmacro %}
```

### 5.7 `services/chat/search.py`

```python
PAGE_SIZE = 20
MAX_QUERY_CHARS = 200
_SIMPLE = literal_column("'simple'::regconfig")

@dataclass(frozen=True)
class SearchHit:
    post_id: uuid.UUID; conversation_id: uuid.UUID; seq: int
    created_at: datetime; author_membership_id: uuid.UUID | None; snippet: str

@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    next_cursor: str | None          # "<created_at iso>|<post uuid>"

def search_posts(session: Session, membership: Membership, q: str, *,
                 cursor: str | None = None, limit: int = PAGE_SIZE) -> SearchPage:
    q = (q or "").strip()[:MAX_QUERY_CHARS]
    visible = visible_conversation_ids(session, membership)
    if not q or not visible:
        return SearchPage([], None)          # never an IN () and never an unfiltered query
    tsq = func.websearch_to_tsquery(_SIMPLE, q)
    snippet = func.ts_headline(
        _SIMPLE, ChatPost.body, tsq,
        f"StartSel={HIT_START}, StopSel={HIT_STOP}, MaxWords=30, MinWords=10, MaxFragments=2")
    stmt = (select(ChatPost.id, ChatPost.conversation_id, ChatPost.seq, ChatPost.created_at,
                   ChatPost.author_membership_id, snippet.label("snippet"))
            .where(ChatPost.conversation_id.in_(list(visible)),
                   ChatPost.deleted_at.is_(None),
                   ChatPost.search_tsv.op("@@")(tsq))
            .order_by(ChatPost.created_at.desc(), ChatPost.id.desc())
            .limit(limit + 1))
    if cursor:
        at, pid = _decode(cursor)               # ValueError → treated as no cursor
        stmt = stmt.where(tuple_(ChatPost.created_at, ChatPost.id) < tuple_(at, pid))
    ...

# web/routes/chat.py
@router.get("/search", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_search(request: Request, q: str = "", cursor: str | None = None, ...) -> HTMLResponse:
    """Full page, or _search_results.html for HTMX. Each hit links to
    /chat/c/{conversation_id}?at={seq}#p-{seq}. The conversation page loads seq ± 25 and
    re-checks visibility like any other load."""
```

---

## 6. Sequenced steps

**Step 1 — `review_common` seams, no channel yet.** Edits (1)–(4) from §5.1. *Verify:* with
`answer_scope=None` Telegram behaves as before: the existing `test_gateway_webhook.py` and
`test_review_common*.py` stay green. A stub adapter whose `answer_scope` yields a string sends
that string instead of calling `ai_response` (A8). `trusted_membership` truth table (A4, A5).
`pytest tests/unit/test_review_common_answer_scope.py tests/integration/test_gateway_webhook.py -q`.

**Step 2 — photo copy into storage (D10).** *Verify:* a Telegram issue with a photo gets a
`Document.file_path` for which `is_storage_key` is true, and nothing new appears under
`web/static/uploads` (A20). `pytest tests/integration/test_issue_photos_storage.py -q`.

Steps 3–7 are each verified with `pytest <the named files> -q`:

- **Step 3 — `audience.py`.** *Verify:* A17 (`test_chat_audience.py`).
- **Step 4 — `ai_bridge.py` and the route hook**, after Steps 1 and 3. *Verify:* A1–A3, A6, A7, A9–A14, A29 (`test_chat_ai.py`).
- **Step 5 — attachments.** *Verify:* A15, A16, A18, A19, A21 (`test_chat_attachments.py`, `test_forms_upload.py`).
- **Step 6 — mentions and the renderer.** *Verify:* A22–A25 (`test_chat_render.py`, `test_chat_mentions.py`).
- **Step 7 — search and `?at=`.** *Verify:* A26–A28 (`test_chat_search.py`).

**Step 8 — UI.** Thinking row, picker, file input, search page; `npm run build:css && npm run
stamp`; checklist rows. *Verify:* the full suite on `mihomes_test_rtb` against the 7 known
failures, then the exit criterion walked by hand on evo-dev with two browsers (owner + staff A).

Each step is a separate commit. Steps 1–2 change Telegram-reachable code, so they merge before
anything native.

---

## 7. Non-goals and deferred scope

### Do NOT do these
- **Do not wrap `handle_inbound` in `authz_context`** (D6). The write path would stop finding staff.
- **Do not reuse `/documents/file/{key}` for chat files**, and do not hand out presigned URLs (D12).
- **Do not store or trust the client's `Content-Type`** (§0.3).
- **Do not render `ts_headline` output, a mention or a link with `|safe`** (D15).
- **Do not write native posts into `chat_messages`**, and do not make the Conversations page
  read native chat. Native chat is its own record.
- **Do not let General fall back to `resolve_default_property` for a multi-property author**, and
  do not name properties in the "which property?" reply (D4, D5).
- **Do not re-dispatch edited posts.** An edit changes text that has already been actioned;
  re-running would duplicate the issue.
- **Do not call `screen_financial_answer` from the native path.** Its audience check is keyed on
  Telegram ids (`financial_guard.py:66-104`). Reuse `is_financial_question` and `STAFF_REFUSAL`
  only.

### Deferred
- **Notifications for mentions** → SPEC-014. It reads `chat_mentions.notified_at`.
- **Rate limits** on posts, uploads and AI dispatches → SPEC-014 hardening.
- **Vision for PDFs.** PDFs reach the AI as a `[file: name]` label only.
- **Multi-message context.** Each post is analysed alone, the same as each Telegram webhook update.
- **Applying `answer_scope` to Telegram groups** → its own change. This needs the Telegram
  audience, which `group_contains_staff` approximates.

---

## 8. Acceptance criteria

All criteria touching access run with the app's `db._engine` swapped to the **`app_engine`**
(non-superuser) role, using the `test_gateway_webhook.py:634-642, :693` pattern. As `postgres`,
RLS is inert (`tests/conftest.py:212-230`).

| # | Criterion | Test |
|---|---|---|
| A1 | **A post in a DM never reaches `dispatch`, and `dispatch(post_in_dm)` called directly analyses nothing** | `test_chat_ai.py::test_dm_never_dispatched` |
| A2 | An owner's "the boiler is leaking" in property chat A creates one issue in A **and a bot post (`author_kind="bot"`, author NULL) in the same conversation** | `test_chat_ai.py::test_issue_logged_and_bot_replies_in_same_conversation` |
| A3 | **That run writes no row to `chat_messages`** | `test_chat_ai.py::test_no_conversation_log_rows` |
| A4 | **Staff sender + an `expense_log` item → no expense row** (untrusted) | `test_chat_ai.py::test_staff_sender_untrusted_no_expense` |
| A5 | The same item from an owner logs the expense. A revoked owner, or an owner of another account, is untrusted | `test_review_common_answer_scope.py::test_trusted_membership_truth_table` |
| A6 | `APPROVE <id>` from an admin in General with two properties is consumed as a PTO approval, not answered with "which property?" | `test_chat_ai.py::test_approval_bypasses_property_question` |
| A7 | **General, two properties, no property named, actionable text → bot asks `ASK_WHICH_PROPERTY`, and no issue or task is logged.** Informational text → silence | `test_chat_ai.py::test_general_ambiguous_property_asks_and_logs_nothing` |
| A8 | `answer_scope` yielding text replaces `ai_response`. With `None`, Telegram's question path is unchanged | `test_review_common_answer_scope.py::test_refusal_replaces_answer` |
| A9 | **The same post dispatched twice (e.g. a retried task) is analysed once**, even if the first attempt raised | `test_chat_ai.py::test_redispatch_processed_once` |
| A10 | The route schedules exactly one dispatch per created post, none for a bot post, and the post is committed before the task runs | `test_chat_ai.py::test_route_schedules_once_after_commit` |
| A11 | **A money question in a chat with staff → `FINANCE_IN_SHARED_ROOM` (owner asker) or `STAFF_REFUSAL` (staff asker); `ai_response` is not called** | `test_chat_ai.py::test_finance_question_refused_in_staff_room` |
| A12 | **In General, a question about property B when a staff reader is scoped only to A → `OUTSIDE_AUDIENCE`.** In property chat A, a **fake provider** is called (asserted), and its `context_data` contains property A's open issue and no "Budget Status" and no property-B rows. Patching the provider rather than the context makes sure the test fails if `ask()` never ran | `test_chat_ai.py::test_answer_bound_to_audience` |
| A13 | An item that the AI tags with another `property_slug` is still filed in the chat's property | `test_chat_ai.py::test_item_property_pinned_to_chat` |
| A14 | `ai.thinking` then `ai.done` are published for a dispatched post. **`ai.done` is still published when analysis raises, when the id was already claimed, and when the author was revoked** | `test_chat_ai.py::test_thinking_always_cleared` |
| A15 | **Attachment download is 404 for a staff member not scoped to the conversation's property, on the app role**, and 200 for a scoped one | `test_chat_attachments.py::test_download_404_for_non_visible_member_app_role` |
| A16 | Download is 404 after the post is soft-deleted, and 404 for another account's attachment id | `test_chat_attachments.py::test_download_404_deleted_and_cross_account` |
| A17 | **For every seeded membership and conversation, m ∈ `members_who_can_see(c)` ⇔ c.id ∈ `visible_conversation_ids(m)`** | `test_chat_audience.py::test_inverse_of_visible_conversation_ids` |
| A18 | Images download `inline` and PDFs as `attachment`, always with `nosniff` and `private, no-store`. A hostile filename (quotes, CR/LF, `../`) cannot break out of the header | `test_chat_attachments.py::test_download_headers` |
| A19 | `.svg`/`.html` are rejected, over 25 MB is rejected, more than 5 files are rejected. **A `.png` declared as `text/html` is stored as `image/png`.** Nothing is stored for a non-visible conversation | `test_forms_upload.py::test_store_validated_upload_rules` |
| A20 | An issue photo, from Telegram or chat, is saved under a storage key, and nothing is written to `web/static/uploads`. **The issue's Photos & Docs tab renders `<img src="/documents/file/<key>">`, and that URL returns 200 to the owner** | `test_issue_photos_storage.py::test_issue_photo_goes_to_storage` |
| A21 | A chat photo reaches the provider as an image `Attachment`, and the temp directory is gone after dispatch | `test_chat_ai.py::test_photo_passed_to_ai_and_temp_cleaned` |
| A22 | **Mentioning a member who cannot see the conversation, by name or by a forged `mention_ids` value, stores no `ChatMention`** | `test_chat_mentions.py::test_non_visible_member_not_mentioned` |
| A23 | `/chat/c/{id}/mentionable` lists only members who can see the conversation, and returns 404 to a non-member | `test_chat_mentions.py::test_mentionable_scoped` |
| A24 | Two visible "Maria"s: no hint → neither mentioned; with a hint → only the hinted one | `test_chat_mentions.py::test_ambiguous_name_needs_hint` |
| A25 | **Hostile text (`<script>`, `"><img onerror>`, `javascript:` URL, `@<b>` names) renders escaped. Only `http(s)` hrefs are produced** | `test_chat_render.py::test_hostile_text_escaped` |
| A26 | **Search never returns a post from a conversation the searcher cannot see, or a deleted post (app role)** | `test_chat_search.py::test_search_scope_and_deleted_app_role` |
| A27 | Malformed queries (`"`, `-`, `OR OR`, 10 kB) return a page without a 500. Pagination is stable while new posts arrive | `test_chat_search.py::test_query_robustness_and_keyset` |
| A28 | Snippet highlights render as `<mark>` around escaped text, and a body containing `<b>` stays literal | `test_chat_search.py::test_snippet_escaped` |
| A29 | **An owner's "Approve the paint quote" in a property chat is analysed normally.** `approve_pto` is not called and no "Could not approve" post appears. `APPROVE <uuid>` still approves | `test_chat_ai.py::test_loose_approve_text_not_hijacked` |

---

## 9. Test manifest

| File | Asserts |
|---|---|
| `tests/unit/test_review_common_answer_scope.py` | A5, A8; Telegram adapter unchanged (no `answer_scope`) |
| `tests/unit/test_forms_upload.py` | A19; `read_document_upload` still returns a `documents/` key |
| `tests/unit/test_chat_render.py` | A25; URL trailing punctuation; longest-name mention wins |
| `tests/integration/test_chat_ai.py` | A1–A4, A6, A7, A9–A14, A21, A29. `analyze_messages` / `ai_response` patched on `review_common` by attribute (as `telegram.responder` tests do). Uses SPEC-012 `web_client_as` (P6) and a `wired` fixture that points `mihomes.db` at the test DB so the background thread's `get_session()` sees committed rows |
| `tests/integration/test_chat_audience.py` | A17, over owner, admin, staff-A, staff-B, a revoked staff member, and a staff member with zero scope |
| `tests/integration/test_chat_attachments.py` | A15, A16, A18 (app role via `app_engine`) |
| `tests/integration/test_chat_mentions.py` | A22–A24 |
| `tests/integration/test_chat_search.py` | A26–A28 (app role) |
| `tests/integration/test_issue_photos_storage.py` | A20 |

---

## 10. What this stage does not make safe

- **Telegram groups still leak.** Questions answered there run with no role or scope bound
  (§0.1), and `financial_guard` is still uncalled. This spec builds the hook (D6) but does not
  turn it on for Telegram. That should be the next gateway change.
- **Photos Telegram already filed** sit in `web/static/uploads` and remain publicly reachable at
  `/static/uploads/<hex>`. D10 stops new ones being written there; moving the existing files is a
  separate migration task.
- **`ProcessedIdStore` is a read-modify-write on a single JSON row** (§0.4). Concurrent dispatches
  of *different* posts can overwrite each other's claims. That is harmless here, because a post
  is dispatched only once, but the store is not a lock.
- **Each group post holds an AnyIO worker thread for 70–110 s.** Sync routes share that pool
  (default 40). A burst of chat posts on NIM can slow every sync page. SPEC-014's rate limit is
  the mitigation, and switching evo-dev to Claude is the better one.
- **Chat attachments are outside privacy export and deletion.** `services/privacy/export.py:178`
  and `deletion.py:243` iterate `Document` keys only. Add chat keys before GA (and see O1).
- **`store_validated_upload` reads the whole file into memory** (`forms.py:75` pattern) before
  checking its size. 5 × 25 MB per request is bounded but large; a streaming size check belongs
  in SPEC-014.
- **General Q&A is often refused, by design.** In an estate where staff are scoped to different
  properties, or where any staff member has no scope, D7's intersection is empty. Every General
  question then gets `OUTSIDE_AUDIENCE`, and the answer is to ask in the property chat. Issue and
  task creation in General is unaffected.
- **The audience is computed at answer time.** A member added after the bot answers can read the
  answer. That matches how every other post in the room behaves, and it is the reason the
  answer is narrowed to the *current* audience at all.
