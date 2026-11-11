# Smart Devices & Cameras — Product Requirements Document

**Status:** Draft — requirements only, nothing built · **Written:** 2026-11-11
**Phase:** 4+ growth bet (post-GA; gates nothing in SPEC-001…014)
**Verified against:** `worktree-spec-build-harness` @ `4503d5d` (the deployed evo-dev code). Every
`path:line` below was read at that ref. Ring facts were read from Amazon's Ring developer docs on
2026-11-11. Facts marked **[unverified]** came from a summary of a page section, not from its text,
and must be re-read before a SPEC builds on them.
**Next step:** once the open decisions in §14 are closed, cut an executable SPEC from this document (SPEC-015).

**Goal.** An owner opens the MiHomes dashboard and sees, for every home, what the devices in it are
saying. Which doors are open, whether the leak sensor is dry, the thermostat setting, the battery
levels. Also the last doorbell press, with a snapshot from the camera that saw it. Anything that
needs attention becomes an Alert or an Issue, and those already have owners, SPACE priority and a
Today list. Home Assistant gives you a device panel. MiHomes adds the estate around it: who handles
the problem, which vendor, which asset, and what it cost last time.

**The stake.** This is the first MiHomes feature that **looks inside a customer's home**. Camera
images are the most sensitive data the product would hold. Ring's own policy forbids any access to
a customer's devices "without the customer's explicit knowledge and consent." It also requires
that the customer can see an access log. MiHomes is used by staff working in someone else's home,
so the whole design rests on one property: a staff member sees a camera only if the owner allowed
it, and the owner can always see that they did.

---

## 1. Current state (grounded in the code)

**Nothing device-related is live.** Some Home Assistant code is in the tree, but none of it is
reachable from the deployed app:

| Component | What it is | Status |
|---|---|---|
| `src/mihomes/ha/` (`bridge.py`, `client.py`, `config.py`, `rules.py`) | WebSocket subscriber to one HA instance. Regex rules on `entity_id` turn sensor states into Issues/Alerts (`rules.py:46-160`) | **Dead.** Not imported by `src/mihomes/web`, `cli/jobs.py`, `cli/cron.py`, `scripts/` or tests |
| `src/mihomes/cli/ha.py` | `mihomes ha setup/test/status/run/rules` | **Unreachable.** There is no `add_typer` for it in `cli/__init__.py:240-335` |
| `src/mihomes/services/ha_sync.py` | REST client for the HA **Supervisor** (`SUPERVISOR_TOKEN`, `:18`), for when MiHomes runs *as* an HA add-on | **Dead.** Imported nowhere. The add-on model does not apply to multi-tenant SaaS |
| `src/mihomes/api/templates/{ha,entities,integrations}.html` | Legacy pages for HA status and an entity list | **Not mounted.** The live app (`web/app.py:177-248`) has no route for them |
| `custom_components/mihomes/`, `addon/`, `ha-config/` | An HA custom integration that pulls MiHomes tasks *into* HA (`coordinator.py:55`, 5-min polling, `iot_class: local_polling`) | Calls the legacy JSON API **with no authentication**. That is the opposite data direction from this PRD |

**Why the existing bridge cannot be revived:**
- It reads one global `ha.url`/`ha.token` (`ha/config.py:18-20`) and opens `get_session()` with no
  `account_context` (`ha/bridge.py:126,150`). Under tenancy, the first query raises `LookupError`
  (`tenancy/session.py:95`), and so does the first insert (`:115`).
- Bound correctly, one process still serves one account.
- Its alerts carry no `property_id` (`bridge.py:104-108`).
- Its rules file smoke/CO as `CRITICAL` Issues, which is a life-safety claim (see §8.4).
- **This PRD retires `src/mihomes/ha/`, `services/ha_sync.py`, `cli/ha.py` and the three legacy
  templates. It does not revive them** (§12, Phase A).

**What the live code does provide, and this feature reuses:**

| Need | Existing pattern | Where |
|---|---|---|
| Tenant-owned tables + RLS | `TenantOwned` mixin, `TENANT_TABLES` registry, `policy_statements()`, drift triggers | `models/__init__.py:30`, `tenancy/registry.py:96`, `tenancy/rls.py:123`, example `alembic/versions/0029_calendar_connections.py:24-77` |
| Encrypted third-party credentials | Fernet `encrypt()`/`decrypt()` under `MIHOMES_SECRET_KEY`, stored as `enc:v1:` | `crypto.py:49,130,140` |
| Per-user OAuth with refresh, reconnect state, lease | `CalendarConnection` (`refresh_token` encrypted, `status` active/needs_reconnect, `syncing_until` lease) | `models/calendar_connection.py:30-45`, `personal_calendar/sync.py:330-383` |
| Webhook ingress on a tailnet-only host | Tailscale **Funnel** publishes one path on :8443 | `docs/deploy/TELEGRAM-WEBHOOK.md:7-75` |
| Finding the account **before** any account is bound (webhooks) | "Gateway bootstrap" SELECT-only RLS policies keyed on a transaction-local GUC holding one external id, set by `bind_gateway_guc` | `alembic/versions/0024_gateway_bootstrap_policies.py`, `tenancy/rls.py` `GATEWAY_BOOTSTRAP_POLICIES`, `tenancy/connection.py` |
| Alerts | `Alert(alert_type, source_entity_type, source_entity_id, property_id, severity, message, status)` + `_already_alerted` dedupe | `models/alert.py:29`, `services/alerts.py:34,162-171` |
| Issues | `create_issue(session, title, property_id_or_slug, *, severity, description, space_id_or_slug, photos, …)` (sync) | `services/issue.py:15` |
| Plan gating | `PLAN_LIMITS` + `_BOOLEAN_ACTIONS` → `can()` | `entitlements/limits.py:50-103`, `entitlements/service.py:95,111` |
| Role gating | `@declares("<action>", Access.X)`; `MATRIX` with ALLOW/DENY/SCOPED; roles owner/admin/staff | `authz/declare.py:49`, `authz/actions.py:61`, `models/membership.py:49` |
| Realtime to the browser | In-process `BUS` per account, Postgres LISTEN/NOTIFY across processes, SSE `frame()` | `services/chat/bus.py:49-119`, `services/chat/stream.py:63` |
| Push notifications | `send_pushes(session, [(membership_id, payload)], sender)` | `services/chat/push.py:147,166` |
| Private file storage + authenticated serving | `StorageProvider`, `build_key(account_id, category, name)`, `/documents/file/{key}` (checks the account prefix, `private, no-store`) | `storage/__init__.py:61,106`, `web/routes/documents_download.py` |
| Audit | `audit_write(session, *, entity_type, entity_id, action, actor, changes)` | `authz/audit.py:38` |
| Scheduled work | `mihomes jobs <name>` from cron, with `SCHEDULE` and a per-account loop | `cli/jobs.py:70,415-444`, `docs/deploy/OPS-SCHEDULER-BACKUPS.md` |
| Account deletion | The purge enumerates every tenant table and fails a test if one is unclassified | `services/privacy/deletion.py:1-66` |

**Facts that differ from what one might assume:**
- The DB engine is **sync** (`db.py:83`). Webhook handlers and jobs use `get_session()`, not `AsyncSession`.
- There is **no alert-creation service**. Alerts are built inline.
- There is **no dashboard widget registry**. `dashboard.html` has hard-coded sections (`:101-249`), and `today.py:93` builds the Today list.
- evo-dev is a **Docker container in WSL2 with no systemd** (`OPS-SCHEDULER-BACKUPS.md:11`).
  `keepalive.sh` supervises only the web app and the Stripe listener. **Nothing would supervise a
  long-running per-account listener**, which rules out the old bridge's design outright.

---

## 2. Gap analysis

| The user wants (vs Home Assistant) | MiHomes today | Gap |
|---|---|---|
| See every device, by home and room | No device model | `devices` table, mapped to Property → Space and optionally → Asset |
| Camera snapshots, doorbell presses, motion | Nothing | Ring adapter: webhooks, snapshot fetch, event timeline |
| Live camera view | Nothing | WHEP proxy (Phase C) |
| Sensor state (leak, door, temperature, battery) | Dead regex rules | Device-class-based rules → Alerts/Issues, per account, editable |
| Turn things on and off (lights, locks, thermostat) | Nothing | Control through Home Assistant only. Ring's partner API is read-only (§3.1) |
| One dashboard across devices | Hard-coded dashboard | A "Homes at a glance" device card + a Devices page. A customizable card grid is Phase D |
| Automations | HA has them | **Non-goal.** MiHomes routes devices into its estate workflow and does not compete with HA's automation engine (§13) |

---

## 3. Integration strategy

### 3.1 The three routes to Ring, and why one is chosen

| Route | How | Verdict |
|---|---|---|
| **A. Ring Partner API** (official; developer portal GA in 2026) | OAuth 2.0 per customer, REST at `api.amazonvision.com`, signed webhooks, snapshot/clip download, WHEP live view, RTSP bridge | **Chosen for Ring.** It is the only route a SaaS can defend in a privacy review or to Amazon |
| B. Unofficial Ring API (`ring_doorbell` / `ring-client-api`, which Home Assistant's own Ring integration uses) | Store the customer's Ring password + 2FA refresh token server-side | **Rejected for direct use.** It means holding a customer's Ring credentials, tokens break on 2FA re-prompts, and it violates Ring's terms. A customer who already runs HA with its Ring integration will see those entities through route C, which is their choice made on their own hardware |
| C. Customer's own Home Assistant | MiHomes reads the customer's HA, which already integrates hundreds of brands | **Chosen for everything that isn't Ring.** It also covers Ring Alarm and lights, which route A cannot do |

**What the Ring Partner API can and cannot do** (developer docs, read 2026-11-11):

| Capability | Supported | Notes |
|---|---|---|
| Device list, online/offline, battery, capabilities | Yes | `GET /v1/devices?include=status,capabilities,location,configurations` |
| Doorbell press, motion (+ person/vehicle/package with Smart Alerts on) | Yes | Webhooks; history at `GET /v1/history/devices/{id}/events` (metadata only, events after consent only) |
| Snapshot (JPEG/PNG), clip (MP4) | Yes | `POST /v1/devices/{id}/media/image/download`, `…/video/download`. **A mandatory watermark** (Ring logo, device ID, app name, timestamp) is burned in server-side |
| Live view | Yes | WHEP (`…/media/streaming/whep/sessions`) and an RTSPS bridge. Caps of 30 s on battery and 60 s on wired cameras are **[unverified]** |
| Ring sensors (contact, flood/freeze, temperature, air quality) | Early access | Added 2026-08-24 |
| **Ring Alarm arm/disarm, lights, locks** | **No** | Not in the API. Available only through route C |
| **Two-way audio** | **No** | Bidirectional media "not supported". Chimes support playback only |
| **End-to-end encrypted devices** | **No** | E2EE devices are not returned by any partner API. Check `configurations.e2e_encryption.enabled` |

**Ring program constraints. Each one is a gate, not a footnote:**
1. **United States only** unless Ring approves otherwise (Program Req 6). → §14 O1.
2. **A Ring subscription (Ring Protect) is required** on the customer's Ring account to use any developer app.
3. **Distribution.** The *private track* skips certification and supports **up to 5 allowlisted Ring
   accounts**, but still appears in the Ring App Store with a "private" badge. Any wider use needs
   certification (90% of reviews take under 48 h). The default *Ring-driven* link starts in Ring's
   app. *Partner-initiated* OAuth (starting from MiHomes, PKCE S256) is **by invitation**, and
   reportedly free apps only **[unverified]**. → §14 O2.
4. **Billing.** Paid apps must bill through Ring with a 10% revenue share and monthly
   subscriptions (Req 2.15/2.17). Whether MiHomes' own Stripe subscription counts as a paid Ring
   app is unknown. → §14 O3.
5. **No life-safety or alarm-monitoring use** (Req 2.8, 2.19). → §8.4.
6. **Branding.** "Ring" or "Amazon" may not appear in the app name, domain or logo (Req 3).
7. **Datacenter egress IPs may receive HTTP 406** until a static IP is registered with Ring support,
   and every call needs `User-Agent` and `Hardware_Id` headers.
8. **The webhook URL cannot be changed through the API**, so the production hostname must be chosen
   before registration.

### 3.2 Home Assistant connection model: the customer's HA pushes out

MiHomes runs in the cloud and the customer's HA runs on their LAN. The table weighs three ways to connect them:

| Option | Inbound exposure of customer HA | Runs on MiHomes | Verdict |
|---|---|---|---|
| MiHomes holds a WebSocket per account to the HA URL (the old bridge) | Required (Nabu Casa / port-forward) | A long-running listener per account, with **no supervisor on evo-dev** | Rejected |
| MiHomes polls HA REST from a cron job with a long-lived token | Required | `*/N` cron, minutes of lag, no control path | Fallback only |
| **The HA custom integration (`custom_components/mihomes`, rewritten) pushes state changes out to MiHomes over HTTPS and long-polls MiHomes for commands** | **None.** Outbound only | Plain web requests. Nothing long-running | **Chosen** |

This turns the existing custom integration around. It is installed through HACS, and its config flow
pairs with a one-time code shown in MiHomes Settings, which gives it a per-connection bearer token.
It then sends the entities the customer chose to share. It pushes `state_changed` for those
entities in batches, sends a heartbeat every 60 s, and holds one long-poll to `/devices/ha/commands`
to receive control requests. This works behind NAT and needs no Nabu Casa. MiHomes never sees
anything the customer did not tick in the config flow.

### 3.3 One seam, two adapters

```
DeviceProvider (Protocol)        services/devices/providers/base.py
├── RingProvider                 services/devices/providers/ring.py   — Partner API client + webhook parser
└── HomeAssistantProvider        services/devices/providers/ha.py     — ingest from the HA integration + command queue
```
Each adapter maps its native data onto one internal model (§6): `Device`, `DeviceState` and
`DeviceEvent` with a normalized `device_class` (`camera`, `doorbell`, `contact`, `motion`, `leak`,
`smoke`, `co`, `temperature`, `humidity`, `lock`, `light`, `switch`, `climate`, `alarm_panel`,
`battery`, `power`, `other`). Everything above the adapters, meaning rules, alerts, dashboard, AI
and audit, depends only on the internal model. A third provider such as SmartThings, Matter or Nest
would be one new adapter and nothing else.

---

## 4. Users and stories

**Owner**
1. Connect my Ring account and my Home Assistant once, then assign each device to a home and a room.
2. See every home on one screen: online/offline, open doors, leaks, temperatures, low batteries, latest doorbell snapshot.
3. Be told about a leak, a camera that went offline, or a door left open at a vacant home, and see who is handling it.
4. Decide exactly which staff can see which cameras. See who viewed what, and when.

**Admin**
5. Manage device mapping and rules for all homes. Turn devices into Assets with warranty and vendor.

**Staff**
6. See the sensors and (if permitted) cameras for **my** properties only.
7. Flip a light, adjust a thermostat or lock a door at a home I look after, if the owner granted control.
8. When a device raises an Issue, open it already linked to the device, room and snapshot.

**Everyone**
9. Ask the AI "is everything OK at Belle?" and get an answer that includes device status.

---

## 5. Functional requirements

IDs are stable so that the SPEC and its tests can cite them. **P** is the phase (§12).

### 5.1 Connections
| ID | Requirement | P |
|---|---|---|
| FR-C1 | Settings → **Devices & cameras** lists connections per account: provider, linked identity, status (`active` / `needs_reconnect` / `revoked` / `error`), last event time, device count | A |
| FR-C2 | **Ring connect.** Two flows. **(a) Partner-initiated OAuth (PKCE)**, preferred: the owner clicks *Connect Ring* in MiHomes, so the link carries a MiHomes `state` and binds to that account directly. It needs Ring's invitation (O2). **(b) Ring-driven link** (the default Ring offers): Ring POSTs `code` + `client_id` server-to-server to the token-exchange URL. The code lives 60 s, and the link fails unless MiHomes returns 200 *after* storing tokens. MiHomes never issued anything in this flow, so it has **no way to know the MiHomes account**. Whether Ring then redirects the user to a partner URL, and with what, is **[unverified]**. Requirement: exchange the code immediately and store the connection as **`unclaimed`** (a global routing row, no account, no devices pulled). Within 24 h the owner claims it in Settings by entering a short code MiHomes shows on the post-link page, or by matching the Ring account email. Unclaimed connections are unlinked and deleted after 24 h. Tokens are stored encrypted. The access token (about 4 h) is never persisted | A |
| FR-C3 | **Ring token rotation.** Each refresh invalidates the previous refresh token, so refresh runs under a DB lease (`refreshing_until`, as in `CalendarConnection.syncing_until`). Exactly one refresher wins and the others re-read. `invalid_grant` sets `needs_reconnect` and alerts the owner | A |
| FR-C4 | **Ring disconnect** calls the unlink API (`DELETE /v1/accounts/me/app-integrations`) where available. In every case it handles the `app_integration_removed` webhook by marking the connection `revoked` and stopping all calls | A |
| FR-C5 | **HA connect.** Settings shows a 10-minute single-use pairing code. The HA integration's config flow exchanges it for a per-connection token (stored hashed), then posts the entity list the customer selected | B |
| FR-C6 | **HA health.** A connection with no heartbeat for 5 min shows `offline` on the dashboard and raises one Alert per outage | B |
| FR-C7 | Only **owner** (and admin, if O6 says so) can create or remove connections | A |

### 5.2 Device inventory and mapping
| ID | Requirement | P |
|---|---|---|
| FR-D1 | Devices are discovered from each connection and upserted by `(connection_id, external_id)`. Devices that disappear are marked `removed`, never hard-deleted while history references them | A |
| FR-D2 | Each device can be mapped to **Property** (required to appear anywhere outside Settings), **Space** (optional) and **Asset** (optional, which links make, model, warranty and vendor) | A |
| FR-D3 | Unmapped devices appear in a "Needs a home" tray on the Devices page and never leak into any property view | A |
| FR-D4 | Name, `device_class` and icon can be overridden. The provider's name is kept as `external_name` | A |
| FR-D5 | "Create asset from device" pre-fills an Asset (`asset_type="equipment"`, make/model from the provider) | B |
| FR-D6 | A Ring device whose `e2e_encryption.enabled` is true shows a "Video unavailable — end-to-end encryption is on in the Ring app" notice and gets status only, if Ring returns the device at all | A |

### 5.3 Dashboard and Devices page
| ID | Requirement | P |
|---|---|---|
| FR-U1 | **Dashboard card "Homes at a glance".** One row per property shows: devices online/total, open entry points, active leak/smoke/CO, out-of-range temperatures, low batteries, and the most recent doorbell/motion event with a thumbnail. It is hidden when the account has no connections | A |
| FR-U2 | **Devices page** (`/devices`), filterable by property, room, class and status. Each tile shows state, last change and battery. Camera tiles show the latest snapshot and its time | A |
| FR-U3 | **Device detail** shows a state/event timeline (30 days default, §8.3), mapped room/asset, linked Alerts/Issues, and a "Create issue from this" button that attaches the snapshot | A |
| FR-U4 | **Live updates.** Tiles update without reload over SSE (`/devices/stream`). Each event is filtered to the viewer's visible properties before it is sent (§7.4) | A |
| FR-U5 | **Controls** (HA devices only): toggle light/switch, lock/unlock, set climate target, arm/disarm alarm panel. Lock and alarm require a confirm step. The tile stays "pending…" until HA confirms the new state, and times out after 15 s | B |
| FR-U6 | **Live view** button on Ring cameras opens a server-proxied stream (§7.3), showing a countdown to Ring's session cap | C |
| FR-U7 | Customizable dashboard grid: pin, reorder and resize device cards per user, the HA "Lovelace" experience | D |
| FR-U8 | Mobile layout follows SPEC-009 (no horizontal scroll at 360 px). Camera tiles go to one column | A |

### 5.4 Events, rules, alerts and issues
| ID | Requirement | P |
|---|---|---|
| FR-E1 | Every provider event is stored as a `DeviceEvent` (class, type, payload summary, occurred_at, provider request id) and **deduplicated** on `(connection_id, provider_event_id)`. Ring webhooks dedupe on `meta.request_id` | A |
| FR-E2 | **Rules** map `(device_class, condition)` → action ∈ {none, timeline only, Alert, Issue}, with severity and cooldown. They are seeded with defaults (below) and editable per account by owner/admin. Rules match on **device_class**, never on name regexes | A |
| FR-E3 | Defaults: leak → Issue HIGH; device offline > 30 min → Alert MEDIUM; battery ≤ 20% → Alert LOW (one per device per 7 days); temperature outside the property's band → Alert MEDIUM; contact open > 30 min while the property is `occupied=false` → Alert MEDIUM; doorbell press / person at a vacant property → Alert LOW with snapshot; motion → timeline only; smoke/CO → Alert HIGH, **worded as informational** (§8.4) | A |
| FR-E4 | Alerts use `alert_type="device"`, `source_entity_type="device"`, `source_entity_id=<device.id>` and `property_id` from the mapping. Dedupe uses the existing `_already_alerted`. Introduce one small `create_alert()` service rather than a fourth inline copy | A |
| FR-E5 | Issues created from a device carry `space_id` and are linked to the device. When the device returns to normal the issue gets a comment. **It is never auto-resolved**: a human closes it. A camera snapshot is **not** copied into `Issue.photos`, because that would show it to everyone who can see the Issue, including staff without a camera grant. The Issue holds a *reference* to the `device_event`, and the image renders through the camera media route (FR-M1). It is shown only to viewers with `can_view_media`, and each view is logged. Others see "Camera image — owner access only". The same rule applies to snapshots on doorbell/person Alerts (FR-E3). Copying a snapshot into `Issue.photos` (for example to send to a vendor) is an explicit, logged owner/admin action, and the copy then leaves camera scope | A |
| FR-E6 | Push notification (`send_pushes`) to the members who can see the property, for Alerts at or above a per-user threshold. The payload names the property and device class, never shows an image, and follows SPEC-014 O3 on lock-screen property names | B |
| FR-E7 | Events from unmapped devices are stored but raise nothing | A |

### 5.5 Cameras and media
| ID | Requirement | P |
|---|---|---|
| FR-M1 | On a doorbell/person event at a mapped camera, fetch **one** snapshot and store it via `StorageProvider` under `build_key(account_id, "device-snapshots", …)`. It is served only through a **new** route, `/devices/media/{event_id}`, which checks the account, the viewer's property scope **and** `camera_grants.can_view_media`, writes the access-log row, and responds `private, no-store`. It is **not** served by `/documents/file/{key}`, which checks only the account prefix | A |
| FR-M2 | "Refresh snapshot" on a tile fetches on demand, rate-limited per device (default once per 60 s) | A |
| FR-M3 | Clips (MP4) are **fetched on demand and streamed through**, never stored, unless a user attaches one to an Issue. Storing is then an explicit action, logged | B |
| FR-M4 | Ring's watermark is never cropped, blurred or removed | A |
| FR-M5 | **No face recognition, licence-plate reading or AI identification of people** in camera media (Ring Content Policy 4.2). AI may describe a snapshot (for example "a parcel at the door") only if O7 allows it | — |

### 5.6 AI
| ID | Requirement | P |
|---|---|---|
| FR-A1 | The Situation Report and Estate Digest include a device summary per property (offline devices, open alerts, last events), computed from `DeviceState`, without media | B |
| FR-A2 | Device-raised Issues enter SPACE prioritization like any other. Leak and smoke/CO score under **Safety / Asset Protection** | A |
| FR-A3 | Chat/Telegram questions such as "is anything open at Belle?" answer from device state, scoped by the asker's visible properties | B |

### 5.7 Consent, access log and deletion
| ID | Requirement | P |
|---|---|---|
| FR-P1 | **Camera access is opt-in per camera and per role.** By default only owners see camera media. Owner grants admin/staff viewing per camera, or per property. Sensor state follows normal property scoping | A |
| FR-P2 | **Access log.** Every snapshot view, refresh, clip view and live-view session writes `audit_write(entity_type="device", action="camera.view"…)` with the viewer. Owners see a "Camera access" log per camera in Settings (Ring Content Policy 1.2.2) | A |
| FR-P3 | Disconnecting a provider offers "also delete history": events, states and stored snapshots for its devices. The default is to delete media and keep the event metadata | A |
| FR-P4 | All new tables are classified in the account-deletion purge (`services/privacy/deletion.py`). Snapshots in object storage are deleted with the account | A |
| FR-P5 | Retention: snapshots 30 days, events 90 days, state history 30 days (O5), enforced by the nightly `cleanup` job. A snapshot referenced by an **open** Issue is kept until 30 days after the Issue resolves. A copy made into `Issue.photos` (FR-E5) follows Issue retention | A |

---

## 6. Data model (new tables, all `TenantOwned`)

Every table goes in `TENANT_TABLES`, gets RLS through `policy_statements()` and drift-guard
triggers, and gets `mihomes_assert_account_matches_parent` on tenant-to-tenant FKs, as in
`0029_calendar_connections.py`. One migration: `0031_smart_devices`.

```
device_connections
  id, account_id, provider ('ring'|'home_assistant'), created_by_membership_id,
  display_name, external_account_id,            -- Ring account id / HA instance uuid
  status ('active'|'needs_reconnect'|'revoked'|'error'|'offline'),
  refresh_token        TEXT  encrypted (ring)    -- crypto.encrypt, 'enc:v1:'
  ingest_token_hash    TEXT  (home_assistant)    -- token itself shown once, never stored
  refreshing_until, last_event_at, last_heartbeat_at, last_error, created_at, updated_at
  UNIQUE (provider, external_account_id)        -- one Ring account → one MiHomes account

devices
  id, account_id, connection_id → device_connections,
  external_id, external_name, name, device_class, manufacturer, model,
  property_id → properties NULL, space_id → spaces NULL, asset_id → assets NULL,
  capabilities JSON, is_camera BOOL, e2ee BOOL, status ('active'|'removed'),
  last_state JSON, last_state_at, online BOOL, battery_pct SMALLINT NULL
  UNIQUE (connection_id, external_id)

device_events
  id, account_id, device_id → devices, connection_id,
  provider_event_id, kind ('state'|'motion'|'doorbell'|'person'|'offline'|'online'|…),
  summary JSON, snapshot_key TEXT NULL, occurred_at, received_at
  UNIQUE (connection_id, provider_event_id)  INDEX (device_id, occurred_at DESC)

device_rules
  id, account_id, device_class, condition JSON, action ('none'|'timeline'|'alert'|'issue'),
  severity, cooldown_minutes, enabled, property_id NULL   -- NULL = all properties

camera_grants
  id, account_id, device_id NULL, property_id NULL, membership_id NULL, role NULL,
  can_view_media BOOL, can_control BOOL, granted_by_membership_id, created_at

device_commands                                          -- HA control queue (Phase B)
  id, account_id, device_id, requested_by_membership_id, command JSON,
  status ('queued'|'delivered'|'confirmed'|'failed'|'expired'), created_at, delivered_at, resolved_at
```

**Finding the account before one is bound.** The Ring webhook and the HA ingest endpoints know
only an external id (the Ring account id, or a bearer token) when they start. Under FORCE RLS with
no account GUC, a lookup on `device_connections` sees **zero rows** in production. It passes in
tests only because fixtures run as a superuser, which is exactly the RLS read bug of 2026-09. The
fix is the Telegram one (`0024_gateway_bootstrap_policies`): two SELECT-only policies on
`device_connections`, one matching `external_account_id = current_setting('app.gateway_ring_account')`
and one matching `ingest_token_hash = current_setting('app.gateway_device_ingest_hash')`. They are
set through `bind_gateway_guc` (`tenancy/connection.py:153`) and added to
`rls.GATEWAY_BOOTSTRAP_POLICIES` (`rls.py:100`). That dict holds **one policy per table**, so the SPEC
either widens it to a list or moves the ingest hash into its own small table. Each policy matches exactly one row,
after which the handler binds `account_context` from it. `unclaimed` Ring links (FR-C2b) have no
account yet, so they live in a small **global** table, `ring_pending_links` (in `GLOBAL_TABLES`,
24 h TTL, encrypted tokens), until claimed. The HA pairing code reuses `gateway_link_tokens`
(hash lookup, single use).

**Deliberately not a table:** a high-frequency state time series. `devices.last_state` holds the
current state, and `device_events` holds *meaningful* transitions (the HA integration sends only
selected entities, and numeric sensors are filtered by a change threshold). If history charts are
wanted later, they get their own decision.

---

## 7. Architecture

### 7.1 Ring event flow
```
Ring cloud ──POST (X-Signature: sha256=HMAC(body))──► /webhooks/ring   (Funnel-published path only)
  1. verify HMAC over raw body BEFORE parsing; reject → 401
  2. bind_gateway_guc(ring account id) → bootstrap policy finds the one connection
     → bind account_context from it  (§6 "Finding the account"; unknown id → 2xx + drop, logged)
  3. insert device_event (dedupe on meta.request_id) and COMMIT
  4. return 2xx  (Ring requires < 5 s; retries 1s…1h; 4xx is not retried)
  5. after-commit: enqueue snapshot fetch + rule evaluation (in-process executor,
     as personal_calendar/trigger.py), publish to BUS for SSE
```
- The handler never calls Ring synchronously. A slow snapshot must not cost the 5 s budget.
- A token-exchange POST (Ring-driven linking, FR-C2b) cannot be signature-verified and carries no
  MiHomes nonce. What makes it safe is that the code is redeemed against Ring's token endpoint with
  MiHomes' `client_secret`, so a forged POST gets nothing, and the resulting link stays `unclaimed`,
  with no account and no device access, until an owner claims it.
- `mihomes jobs devices-reconcile` (`*/15`) re-lists devices and statuses per active Ring
  connection, catching anything missed while a webhook was dead-lettered (Ring keeps those 72 h).

### 7.2 HA ingest and command flow
```
Customer HA (custom_components/mihomes v2)
   POST /devices/ha/ingest        Authorization: Bearer <ingest token>   batched state changes
   POST /devices/ha/heartbeat     every 60 s
   GET  /devices/ha/commands      long-poll ≤ 25 s → returns queued device_commands
   POST /devices/ha/commands/{id} ack + resulting state
```
- The ingest token maps to exactly one `device_connections` row, found through the
  `app.gateway_device_ingest_hash` bootstrap policy (§6), and that row binds the account.
  The token never authorizes anything except these four endpoints.
- Payload caps: 500 entities per batch, 256 KB per request, 10 requests/s per connection (429 above that).
- Commands are only ever created by an authenticated MiHomes user who passed `device.control`
  (§9). The HA side executes only service calls on entities it shared, never arbitrary service names.
- The long-poll is an ordinary request and needs no worker. At 25 s per poll it costs one held
  request per connected HA. The SPEC must size the web server's worker/connection limits for it (O8).

### 7.3 Live view (Phase C)
Ring calls are server-to-server only (browsers are blocked by CORS), so MiHomes proxies the WHEP
exchange. The browser POSTs its SDP offer to MiHomes, MiHomes forwards it to Ring with the
customer's token, and the answer comes back. The media path is
**[unverified — must be confirmed in the SPEC]**: the media may flow browser↔Ring directly once the
SDP exchange completes, or it may need a relay. If it needs a relay, a TURN/media server is a new
piece of infrastructure and its own decision.

### 7.4 Realtime visibility
The chat `BUS` delivers to a whole account. `/devices/stream` must therefore filter every event
against the viewer's visible property set **and** camera grants before writing a frame. Staff get
SCOPED property visibility, and media URLs only with `can_view_media`. The SPEC must include a
test where a staff member on property A receives nothing from property B.

---

## 8. Security, privacy and policy

1. **Credentials.** Ring refresh tokens are encrypted with `crypto.encrypt`. HA ingest tokens are
   stored only as a hash. No provider secret is ever rendered back to the browser.
2. **Webhook authenticity.** Ring uses HMAC-SHA256 with a constant-time compare on the raw body, and
   the HMAC key lives in env (`RING_WEBHOOK_HMAC_KEY`). For HA the bearer token is per connection,
   and a revoked connection gets 401 immediately.
3. **Media handling.** Snapshots are stored only for the triggering events in FR-M1. Clips are
   never stored by default. Every view is logged (FR-P2). Retention follows FR-P5. Media is never
   sent to the AI provider unless O7 allows it, and never to any other third party.
4. **Not a security or life-safety system.** Ring's terms forbid life-safety and unlicensed
   alarm-monitoring use, and MiHomes cannot guarantee delivery anyway. Every smoke/CO/leak surface
   carries "MiHomes is not an alarm monitoring service. Rely on your alarm provider and local
   detectors." Severity labels are informational. Nothing promises a response time. The old
   `rules.py` "Evacuate and investigate immediately" wording is removed with that file.
5. **Consent for staff viewing.** This is §5.7, and it is the reason camera media has its own
   grant, separate from property access.
6. **Branding.** The Ring-registered app name and logo must not contain "Ring" or "Amazon". The
   MiHomes UI may say "Connect Ring" descriptively.
7. **Rate limits.** Ring allows 100 TPS per client_id, shared by all customers, and returns 429 with
   `Retry-After`. The Ring client keeps one shared token bucket and honours `Retry-After`.

---

## 9. Entitlements and RBAC

**Entitlements** (`entitlements/limits.py`). New keys go in **all three** plans, or
`ENTITLEMENT_KEYS` breaks (`:103`). Boolean features go in `_BOOLEAN_ACTIONS` (`service.py:95`) and
`max_devices` goes in the counted-actions map (`:104`). An action in neither map is silently allowed by `can()`.

| Key | Free | Pro | Estate |
|---|---|---|---|
| `smart_devices` (sensors, Devices page, rules) | False | True | True |
| `camera_media` (snapshots, clips) | False | True | True |
| `camera_live_view` (Phase C) | False | False | True |
| `max_devices` (counted) | 0 | 25 | 250 |

These numbers are a proposal. Pricing owns them (O4).

**RBAC** (`authz/actions.py` `MATRIX`):

| Action | owner | admin | staff |
|---|---|---|---|
| `device.connection.manage` | ALLOW | O6 | DENY |
| `device.view` (state, events) | ALLOW | ALLOW | SCOPED (own properties) |
| `device.manage` (mapping, rules) | ALLOW | ALLOW | DENY |
| `device.control` (HA commands) | ALLOW | ALLOW | SCOPED + `camera_grants.can_control` |
| `camera.view_media` | ALLOW | grant required | SCOPED + grant required |
| `camera.access_log.view` | ALLOW | ALLOW | DENY |

---

## 10. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Webhook `/webhooks/ring` answers 2xx within 1 s at p95 (Ring allows 5 s) |
| NFR-2 | Event to tile update on the dashboard ≤ 3 s p95 (HA) / ≤ 5 s p95 (Ring, excluding Ring's own latency) |
| NFR-3 | HA command round-trip (click → confirmed state) ≤ 5 s p95 when HA is online |
| NFR-4 | A provider outage never breaks the dashboard. Device widgets degrade to "last known at …" |
| NFR-5 | No provider network call on the dashboard render path. Everything renders from the DB |
| NFR-6 | Zero cross-tenant reads, proven by the existing RLS test harness running as `mihomes_dev_app`, not the superuser (see the RLS read bug lesson). That includes a test that `/webhooks/ring` and `/devices/ha/ingest` **do** resolve their account under that role, and resolve only one |
| NFR-7 | Duplicate webhook deliveries create no duplicate events, alerts or snapshots |

---

## 11. Operations (evo-dev and production)

- **Webhook ingress:** add a Funnel path for `/webhooks/ring` only, on the same :8443 used for Telegram
  (`TELEGRAM-WEBHOOK.md` §4). The Ring webhook URL **cannot be changed via API**. The developer
  portal has separate **Staging** and **Production** settings tabs, and the assumption that each
  holds its own webhook URL is **[unverified]**. If it does, the evo-dev Funnel URL goes on Staging
  for Phase A and the production hostname goes on Production later (O9). If it does not, O9 must be
  decided **before** Phase A can register any webhook.
- **Static egress IP for Ring** (to avoid HTTP 406): find evo-dev's egress IP, register it with Ring
  support, and document it in a new `docs/deploy/RING.md`.
- **HA ingest** (`/devices/ha/*`) must be reachable from customers' homes, so it is a second Funnel
  path, or it waits for production hosting.
- **New env:** `RING_CLIENT_ID`, `RING_CLIENT_SECRET`, `RING_WEBHOOK_HMAC_KEY`, `RING_HARDWARE_ID` (a
  UUID, fixed per deployment).
- **New cron** (`cli/jobs.py SCHEDULE`): `devices-reconcile */15`, and device retention added to
  the existing nightly `cleanup`.
- **No new long-running process.** This is a hard requirement, given that `keepalive.sh` supervises only web + Stripe.
- **Distribution of the HA integration:** a public HACS repository (O10).

---

## 12. Phasing

| Phase | Scope | Exit criterion |
|---|---|---|
| **A — Foundation + Ring (private track)** | Retire the dead HA code. Migration 0031. `DeviceProvider` seam. Ring connect/refresh/unlink, webhooks, reconcile job. Devices page, dashboard card, device detail. Rules → Alerts/Issues. Snapshots + access log + camera grants. Entitlements/RBAC. Purge + retention | On evo-dev, a doorbell press at a mapped Belle camera shows on the dashboard within 5 s with a watermarked snapshot. A staff member without a grant sees the event but no image. The owner sees that staff member's earlier view in the access log |
| **B — Home Assistant connector + control** | `custom_components/mihomes` v2 (pairing, ingest, heartbeat, command long-poll). Controls with confirm. Device → Asset. Push notifications. AI device summary | Pair a real HA. A leak sensor set to wet raises an Issue in the right room. A staff member with control toggles a light and the tile confirms within 5 s. Unplugging HA shows `offline` within 5 min |
| **C — Live view** | WHEP proxy (+ relay if §7.3 needs one), countdown, logging | Owner watches a 30-s live session from a phone. The session appears in the access log |
| **D — Dashboard grid + Ring certification** | Per-user card layout (pin/reorder/resize). Ring certification for > 5 accounts | A non-allowlisted US customer links Ring from the App Store |

**If O1 shows the pilot homes are outside the US, swap A and B.** Ship the HA connector first
(it has no territorial gate), and Ring waits on Ring's approval.

---

## 13. Non-goals

- An automation engine (scenes, schedules, triggers that act on devices). HA does this already. MiHomes links devices to estate work; it does not reproduce HA.
- Two-way audio, Ring Alarm control via Ring's API, recording/NVR, 24/7 video storage.
- Direct LAN or Matter/Zigbee/Z-Wave radio support. That is HA's job.
- Using the unofficial Ring API from MiHomes servers.
- Facial recognition, licence-plate reading, or identifying people in media.
- Professional alarm monitoring or any life-safety promise.
- Reviving the add-on/Supervisor deployment model.

---

## 14. Open decisions and risks

| # | Decision | Owner | Blocks |
|---|---|---|---|
| **O1** | **Which pilot homes are in the US?** Ring's program is US-only. If Belle or the first customers are outside the US, Ring-direct is blocked pending Ring's approval and Phase B goes first | Founder | Phase order |
| O2 | Ring distribution: private track (5 accounts, still shown with a "private" badge) for the pilot, then certification. **Request partner-initiated OAuth from Ring now**: without it, every link goes through the `unclaimed` → claim step (FR-C2b), and what Ring shows the user after a Ring-driven link is unverified | Founder | Phase A connect UX |
| O3 | Is MiHomes a "paid Ring app" under Req 2.15/2.17 (Ring billing, 10% share, monthly only) when Ring access comes bundled in a Stripe subscription? Ask Ring before certification | Founder + counsel | Phase D |
| O4 | Plan placement and device caps (§9) | Pricing | Config only |
| O5 | Retention periods for snapshots, events and state (§5.7 FR-P5) | Founder | Config only |
| O6 | May admins create and remove provider connections, or only owners? | Founder | RBAC row |
| O7 | May the AI provider (NVIDIA NIM) receive camera snapshots for descriptions? Default **no**. Images leave the building and must match the privacy policy | Founder + counsel | FR-M5, FR-A1 |
| O8 | Web server capacity for HA long-polls (one held request per connected home) | Eng | Phase B |
| O9 | Production hostname for the immutable Ring webhook URL | Founder | Production registration. It also blocks **Phase A** if the portal's Staging tab can't hold its own webhook URL (§11) |
| O10 | Publish the HA integration publicly on HACS (open source), or ship it privately per customer? | Founder | Phase B distribution |

**Risks**
- **Ring changes its program.** It went GA in 2026 and has shipped breaking changes since (refresh-token rotation 2026-10-22). Mitigation: everything behind `DeviceProvider`, and a reconcile job.
- **Customers expect Ring Alarm control.** It isn't available through Ring's API. Mitigation: say so on the connect screen, and point to HA.
- **E2EE Ring users see no video.** Mitigation: an explicit notice (FR-D6).
- **Privacy incident from a mis-scoped camera view.** Mitigation: the camera grant is separate from property scope (FR-P1), the access log (FR-P2), and a cross-property SSE test (§7.4).
- **The private track's 5-account ceiling** limits the pilot size until certification.

---

## Sources

- Ring developer docs: [Getting started](https://developer.amazon.com/docs/ring/get-started.html) ·
  [Partner API documentation](https://developer.amazon.com/docs/ring/api-documentation.html) ·
  [Release notes](https://developer.amazon.com/docs/ring/release-notes.html) ·
  [Ring integrations (customer help)](https://ring.com/support/articles/e2sg9/Ring-integrations) ·
  [Ring Developer Community announcement](https://community.amazondeveloper.com/t/welcome-to-the-ring-developer-community/27702)
- Home Assistant: [Authentication API](https://developers.home-assistant.io/docs/auth_api) ·
  [Ring integration](https://www.home-assistant.io/integrations/ring/) (cloud-only, no two-way audio)
- PRD.md:265 and :1038 already list "smart home devices" and "Smart home sensor integration" as future scope.
