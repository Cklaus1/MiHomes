# MiHomes — Lessons & Patterns

Corrections, patterns, and self-improvement rules learned during development.
Review this at the start of each session.

## PRD Process
- Always renumber ALL section cross-references when inserting new sections (not just headers)
- When doing find/replace on section numbers, do it in reverse order (highest first) to avoid cascading replacements (e.g., 6→7→8→9 chains)
- Anonymize real names in examples before committing to a public repo

## Code Patterns
- Rich markup: never use empty style tags like `[]{text}[/]` — causes MarkupError. Only wrap in `[style]...[/style]` when style is non-empty.
- When filtering SQLAlchemy queries, double-check which enum (e.g., IssueSeverity vs IssueStatus) is used on which column
- `setuptools.backends._legacy:_Backend` doesn't exist — use `setuptools.build_meta`
- `alembic revision --autogenerate` requires the DB to be at `head` first. Always `alembic upgrade head` before generating.

## Architecture Review Lessons (2026-03-27)
- Never use `__import__()` in production code. Import at module top.
- Use `Boolean` not `Integer` for boolean SQLAlchemy columns — type mismatches cause subtle query bugs
- Never catch bare `Exception` — always catch specific types (ValueError, KeyError, TypeError)
- Never use `except Exception: pass` (silent swallowing) — at minimum log the error
- Always audit-log side effects (e.g., when work_order.verify() changes an issue's status)
- Fix N+1 queries by using joins in aggregation queries
- Use explicit singularization maps instead of naive `.rstrip("s")` for table names
- For unbudgeted categories, `pct_used` should be 100% (not 0%) if there's actual spending
- Wrap JSON.parse() in try/catch when reading persisted files (corrupted files shouldn't crash the app)

## Fourth Review Lessons (2026-03-27)
- NEVER use `hasattr(instance, key)` to guard `setattr` on ORM models — it matches relationships too. Use `key in {c.name for c in instance.__table__.columns}` to only allow column attributes. Created `safe_update()` helper.
- Always guard state-transition operations (complete, resolve, verify) against being called on entities already in a terminal state. A completed task must not be completable again.
- `init --demo` must be idempotent or guarded — running it twice creates duplicate data with slug suffixes.

## Session Lessons (2026-04-02)
- **URL-encode all query params**: `+` in ISO timestamps (`+00:00`) is treated as a space in URL query strings. Always use `urllib.parse.urlencode()` or format timestamps as UTC Z-suffix (`strftime + "Z"`) to avoid silent filtering failures.
- **Test API calls directly before assuming they work**: The WhatsApp `since` filter returned 0 results silently for weeks — a quick `curl` debug would have caught it immediately.
- **Don't run long-lived processes in background without crash recovery**: The monitor crashed silently with no restart. Add try/except at the top loop level and log crashes visibly.
- **Don't give users CLI commands to run — just run them**: User preference is for assistant to execute commands directly rather than printing instructions.
- **Verify end-to-end before declaring a feature done**: WhatsApp monitor was "done" but the URL bug meant it never actually caught any messages. Always prove the full path works with a real test.

## Session Lessons (2026-04-02) — Gap Analysis
- **Run commands, don't just read code**: When doing a PRD gap analysis, invoke every listed command and check exit codes. A file existing in the codebase does NOT mean the command works — `staff schedule` existed as a concept but was never implemented. The subagent found the file; only running it would have caught the gap.
- **Gap analysis = code survey + live test**: After identifying what *should* exist from the PRD, run `r.invoke(app, cmd)` for each one and check `exit_code == 0`. Report failures, not assumptions.

## Hardening Build-Loop Lessons (2026-07-29)
- **Verify each finding against CURRENT source before writing its test/fix, not against the spec's quoted line-state.** The spec's baseline predated the telegram-bot merge; D7/D8 were described as fully broken but the merge had already fixed the telegram.py STARTUPINFO guard, the WhatsApp backoff/gate/bridge-check, and the hot-loop. Only a subset (whatsapp `_start_watchdog_now` STARTUPINFO + scripts/watchdog zombie-reaping/fd-leak) was still live. Writing the test first caught this — the test for the "already fixed" half would have passed pre-fix, exposing the stale claim. Always `grep`/Read the exact cited lines before assuming a finding is real.
- **The zombie-vs-os.kill(pid,0) bug only matters where the process is a REAPING PARENT.** scripts/watchdog.py spawns and must `poll()` a retained Popen handle. The CLI `os.kill` sites check PIDs they did NOT spawn (read from a pid file), so no zombie can form there — the spec's "fix in all 3 copies" over-counted. Fix the reaping parent; the pid-file readers just need the platform guard.
- **`config.DB_URL`/`DB_PATH` are frozen at IMPORT time from `MIHOMES_DIR`.** A test that does `monkeypatch.setenv("MIHOMES_DIR", ...)` in a fixture changes nothing — config was already imported. Worse: a CLI integration test that imports `mihomes.cli` at module top WITHOUT first setting `MIHOMES_DIR` will, when it collects before the other isolated modules, freeze `DB_URL` to the developer's REAL `~/.mihomes` DB and then seed/mutate it — a passing-in-isolation test that silently writes to production data. Two-part fix: (1) `os.environ.setdefault("MIHOMES_DIR", tempfile.mkdtemp())` BEFORE importing mihomes (matches test_cli.py:13 / test_demo_boot.py:13 convention), and (2) for per-test isolation, `monkeypatch.setattr(config, "DB_PATH"/"DB_URL", ...)` + rebind the global engine (`db._engine=None; db.init_db(url=...)`) and save/restore `db._engine`/`db._SessionLocal` on teardown so sibling module-scoped engines are undisturbed. `is_initialized()` reads `config.DB_PATH` at call time, so the CLI guard needs DB_PATH patched too, not just the engine.
- **"Passes in isolation" is not proof for a DB-touching test — run it in BOTH collection orders against a sibling that also owns a module-scoped engine.** The engine-collision only appears under one ordering because whichever module imports config first wins the frozen path.
- **`from mihomes.config import DB_DIR` binds the path BY VALUE — an `importlib.reload(config)` anywhere silently desyncs it.** `mihomes.db` and `mihomes.web.server` both did `from mihomes.config import DB_DIR/DB_URL` at import. The logging/backup test fixtures `importlib.reload(mihomes.config)` (expecting it to take effect), which rebinds `config.DB_DIR` to a NEW object while those two modules keep the stale one. `_seed_demo_db()` then wrote schema to a dead directory and a later read saw "no such table" — a defect that only surfaced once new integration files shifted collection order so a reloader ran before `test_demo_boot`. Production imports once so it was masked. **Rule: modules that consume mutable/reloadable config paths must resolve them live (`import mihomes.config as config; config.DB_DIR`), never `from ... import DB_DIR`.** Also: a fixture that reloads config MUST reload it back to the true env on teardown (undo monkeypatch FIRST — LIFO — then `importlib.reload`), the pattern test_backup.py already uses and test_logging.py was missing.
- **A shared-on-disk-DB integration module must load demo data idempotently.** Because `config.DB_URL` freezes at first import, all on-disk integration modules share ONE db file; a second module-scoped `setup_db` calling `load_demo_data` hits its "already loaded" guard. Guard the seed: `if not session.query(Property).filter_by(slug="beach-house").first(): load_demo_data(session)`.
- **A "guard flag" that's only cleared on the success path deadlocks the retry loop.** M29's reconnect guard (`if !reconnecting` before scheduling `setTimeout(startConnection)`) prevents stacked timers — but if I only reset `reconnecting=false` inside the `open` handler, a reconnect ATTEMPT that itself fails before connecting leaves the flag stuck true and no further reconnect ever schedules. Fix: clear the guard at the START of the retried action (`startConnection`), so it means "a reconnect is pending" not "we ever tried". Rule: for any one-in-flight latch, reset it when the guarded action BEGINS, not only when it succeeds — otherwise a mid-flight failure is a permanent stall.
- **When a spec bundles N sites for one fix, re-derive the site list from the models, not the spec.** M27's `is_trusted_sender` was speced to match staff by `telegram_id`, but the Staff model has no such column (only `whatsapp_phone`). Grepping the model before writing the helper turned a would-be AttributeError into a correct allowlist+approver-id fallback for Telegram. Always confirm the field/column a cross-cutting fix leans on actually exists.
- **Two same-named modules; find the one the CLI actually calls before fixing.** L9 "nullable vendor scores" pointed at vendor ratings, but there were TWO modules: `services/vendor_rating.py` (all-4-scores-required, tested, but NOT wired to the CLI) and `services/vendor.py` `rate_vendor`/`get_vendor_ratings` (the real CLI path, which fabricated `cost_score=quality`/`communication_score=reliability`). Grepping `cli/vendor.py` for the callee (`vendor_svc.rate_vendor`) settled which one to touch. Fixing the tested-but-dead module would have left the bug live and passed CI.
- **Making a model column nullable is a 4-part change, and the render layer is part 3.** L9: (1) model `nullable=True`, (2) Alembic migration (`upgrade head` BEFORE `--autogenerate`, then apply), (3) stop fabricating in the service + filter None from aggregates, (4) **the CLI/render sites that assumed non-null** — `_stars(None)` did `int(round(None))` → crash, `str(None)` printed "None". A nullable migration with no render guard just moves the crash downstream. Always grep the field's read sites after widening its type.
- **iCal/escape unescaping: resolve the escaped-backslash FIRST, or use a single-pass scan.** `_unescape` chained `.replace("\\n","\n")…​.replace("\\\\","\\")` last, so `\\n` (escaped backslash + literal n) wrongly became backslash+newline. Chained `.replace()` for escape sequences is order-dependent and almost always subtly wrong; a single left-to-right scan that consumes `\` + next-char is order-free and the correct default.
- **A restart-survivable "already did X today" latch must live on disk, not in a loop-local variable.** The watchdog's `last_inventory_digest_date` was re-initialized to `None` on every process start, so a Monday restart re-sent the weekly digest. Persist the marker to a file and seed the in-memory var from it at startup. Any "once per day/week" guard in a supervised/restartable process needs durable state.
- **Hoist `sys.path.insert` (and any idempotent-looking setup) out of the loop body.** The watchdog re-ran `sys.path.insert(0, src)` on every 60s tick, growing `sys.path` unboundedly over days. Import-time, guarded (`if src not in sys.path`) is the correct home. Also delete genuinely dead helpers (`_bot_reachable` was defined, never called) rather than leaving them as future-confusion.

## Compound-Stop Reconciliation Lessons (2026-07-29)
- **The final spec-reconciliation walk is not a formality — it catches what the DAG author dropped.** G-Final's F.3 ("walk the spec top-to-bottom; every finding landed-with-test or deferred") found FOUR findings (H3, M7, M8, M9) that were in the spec but never assigned to any DAG group — they would have shipped unaddressed if the stop condition were "all checkboxes ticked + suite green" alone. The compound condition B (every *spec* finding reconciled, not every *task*) is what surfaced them. Rule: derive the reconciliation checklist from the SPEC's finding IDs, not from the task list — grep every finding ID against both build-loop.md and opportunities.md; anything in neither is an omission to fix-or-defer before STOP.
- **M8 — a raw-SQL date predicate that interpolates `.isoformat()` disagrees with the ORM at the day/second boundary.** SQLite stores `DateTime` space-separated (`2024-07-29 12:00:00+00:00`) but `datetime.isoformat()` is `T`-separated; since `' '(0x20) < 'T'(0x54)`, a lexical `WHERE ts < '...T...'` includes a row the ORM's `< cutoff` (a bound datetime, strict) excludes. The archive counted with the ORM and deleted with the raw string → a within-retention row silently archived+deleted. Always bind datetimes as parameters in `text()` (`WHERE ts < :cutoff`, `{"cutoff": dt}`), never f-string their isoformat — the binding uses the same comparison semantics as the ORM.
- **M9 — `datetime(hour=h+1)` is a latent ValueError bomb; use `start + timedelta(hours=1)`.** Adding an hour by incrementing the hour field crashes at 23:00 (`hour=24`). Any "end = one hour after start" must go through `timedelta`, which rolls the date. Doubly dangerous here because a bare `except: return False` swallowed it into a silent never-syncs.
- **Splitting a two-part finding: fix the part that fits, defer the part that needs closed infra.** M9 had a crash half (pure logic → fixed test-first) and a timezone half (needs a `Property.timezone` column, but the R4 migration group was already closed/committed). Landing the crash fix + deferring the schema half to opportunities.md satisfies condition B without reopening a committed migration group or violating minimal-impact. Don't reopen a sealed group to force a whole finding in — split it.

## Spec Build-Harness Authoring (2026-08-06)
- **A corrective harness's stop condition does not transfer to constructive work.** Chris's
  hardening loop gated on "full suite green + smoke green" — sound when *fixing* code the suite
  already covers. For greenfield specs a **stub satisfies every one of those conditions**: the
  suite never touched the new code, and the smoke path never reaches it. Any harness for new
  construction needs a condition bound to the spec's own acceptance criteria, plus test-first
  enforced as a gate (test must fail before the change) rather than as a principle.
- **A completeness condition needs a walk that proves it, and one walk is not enough.** Chris's
  F.3 (walk the spec top-to-bottom, confirm every finding is accounted for) caught four findings
  never assigned to any group. But "every criterion passes its test" is vacuously true for a
  criterion bound to no gate at all — so the walk splits in two: one over the *steps*, one over
  the *criteria*. Whenever a condition says "everything is covered," ask what proves nothing was
  dropped before the covering began.
- **Verify a claim's ref before trusting the claim.** SPEC-001..005 assert "33 test files" and
  "780+ tests" — both true against `telegram-bot`, both false against `origin/main` (82 files,
  1080 tests). A gate reading "the existing 33 test files pass" is satisfiable with 49 other
  files broken. `docs/specs/README.md` already says it: *"A claim about 'the code' without a ref
  is not a verified claim."* Re-verify counts and paths against the target ref before executing,
  and halt on mismatch instead of silently adopting the new number.
- **`merge-tree` conflict-free does not imply `cherry-pick` conflict-free.** `merge-tree` merges
  two tips; cherry-pick *replays each commit in order*. A commit that modifies a file the target
  branch never had is a modify/delete conflict on replay even though the three-way merge is
  clean. Predicting "no conflicts" from `merge-tree` and then cherry-picking is how you get
  surprised — check which files the *earliest* commit touches against the target.
- **A green local suite proves the code works *on this machine*, not that the declared deps are
  sufficient.** First CI run failed 10 tests: `pytest-asyncio` was declared **nowhere** while
  `pyproject.toml` sets `asyncio_mode = "auto"` (without the plugin that setting silently does
  nothing — async tests are collected and never run, so they had never actually passed anywhere),
  and `openai` was declared only as an *optional* extra while seven tests import it
  unconditionally. Both were installed on the dev machine, so `pip install -e ".[dev]"` on a clean
  machine could not run the suite and nobody could tell. **A config option that needs a plugin is
  a dependency**; when a test suite reads an env-driven or plugin-driven setting, check the plugin
  is declared, not merely present.
- **Reconcile CI's totals against local rather than comparing pass counts.** Local read
  `1 failed, 1233 passed, 1 skipped`; CI read `10 failed, 1225 passed, 0 skipped`. Different pass
  counts, *identical* collection — both total 1235. Comparing only "passed" would have suggested
  CI was missing tests; totalling all three buckets showed the difference was purely which tests
  each platform *can* run. Two tests that fail-or-skip on Windows pass on Linux, which is a
  coverage gain the CI decision bought immediately.
- **A verified signature is not a verified token — check `aud`, `iss` and `exp` separately.**
  Google signs every relying party's ID tokens with the same keys, so a token minted for *any
  other* Google app carries a perfectly valid signature. Without an audience check that token
  authenticates against your app. Keep claim validation in its own function so it is unit-testable
  without a live JWKS fetch — these are the checks people skip precisely because a good signature
  feels like proof.
- **A clock-skew allowance needs `<=`, not `<`, and a test on both sides.** `exp + SKEW < now`
  leaves a one-second hole exactly at the boundary — which is where a test written at that offset
  lands, and nowhere else. Pair the "clearly expired is rejected" test with a "just-inside-skew is
  accepted" one: tightening the comparison without the second test can silently turn into "reject
  everything slightly old", which breaks real sign-ins rather than fake ones.
- **A `secure` cookie makes `TestClient` look broken.** Cookies set `secure=True` are never sent
  back over `http://testserver`, so every request that depends on one fails with "missing cookie" —
  a test artifact that looks exactly like a bug in the flow. Point the base URL at `http://` for
  those tests and assert the production flags (`Secure`, `HttpOnly`, `SameSite=Lax`) in a separate
  test that inspects the `Set-Cookie` header directly. Note `SameSite` must be `Lax`, not `Strict`,
  for an OAuth state cookie: `Strict` drops it on the cross-site redirect back from the provider.
- **"Do not leak whether a record exists" constrains the *error* paths, not just the happy one.**
  `POST /waitlist` has five distinct outcomes — new address, repeat unconfirmed, already
  confirmed, past the resend ceiling, and malformed input — and N3 requires all five to render a
  byte-identical page. The already-confirmed case is the one that catches you out: the service
  correctly returns no token, so the obvious handler branches there and renders something
  friendlier, which is exactly the oracle. Write the uniform response *once* and return the same
  object from every path, including the `except`.
- **Commit before the side effect when the side effect is allowed to fail.** A10 requires a
  signup to survive a dead mail provider, which means the transaction must already be committed
  when the send is attempted — not merely that the send error is caught. Ordering, not exception
  handling, is what makes the guarantee real.
- **SQLite stores a `postgresql.UUID` column as UNDASHED hex, so SQL ordering on it is silently
  wrong.** Raw dump: `019fed54a792774084d2dd63dec8bdfc`, while the ORM binds
  `UUID('019fed54-a792-…')`. Any `WHERE id < :id` or `tuple_((created_at, id)) < …` therefore
  compiles to valid SQL, runs without error, and matches nothing — the ranking came back
  `[4, 4, 4, 4]` for four rows. This is the dangerous shape: not a crash, a *wrong answer*. When a
  Postgres-typed column has to be ordered and the test suite runs on SQLite, either rank in Python
  or assert the ordering against the real engine; do not trust a green SQLite test.
- **A `server_default` timestamp is not a unique sort key.** Rows created in the same second share
  an identical `created_at`, so `WHERE created_at < :ts` counts peers — and, if the row's own
  timestamp is included, the row itself — as "ahead". A 1-based position query returned 2 for the
  only confirmed row. Always pair a timestamp sort with a unique tie-break and exclude the subject
  row explicitly.
- **An `lru_cache`d factory makes `monkeypatch` both ineffective and contagious.**
  `render._get_env()` is cached, so a test that repointed `TEMPLATE_DIR` got the *stale*
  environment (patch ignored) and then leaked its temp-directory loader into every later test in
  the module — three unrelated tests failed with "no HTML template". Clearing the cache after the
  fact is not enough: clear it *before and after*, inside `try/finally`, so an assertion failure
  cannot leave the cache poisoned. Generally: patching a value that a cached factory already
  closed over is a no-op with side effects.
- **Jinja blocks are not module attributes.** `template.make_module(data)` exposes top-level
  names but not `{% block x %}`; blocks live in `template.blocks` and must be called with an
  explicit `template.new_context(data)`. This matters specifically for a *child* template
  overriding a block declared in its parent — `.blocks` resolves to the override, which is what
  an email subject line needs.
- **A duplicated exclusion list is a fix that does not propagate — grep for the other copies.**
  Adding `waitlist` to `_UNMANAGED_TABLES` in `alembic/env.py` fixed the main tree's autogenerate
  but broke two integration tests three modules away, because
  `test_migration_reconciliation.py` and `test_money_migration.py` each define their **own local**
  `_unmanaged` set instead of importing env.py's. Both already carried `dummy` for the identical
  reason (a model on the shared `Base` that the tree never migrates), which is the tell that the
  duplication had bitten before. When editing a filter list that exists to keep autogenerate
  quiet, grep the whole tree for other copies before assuming one edit suffices.
- **A tight loop does not advance the clock — time-ordering assertions over one need care.**
  The obvious UUIDv7 test, `sorted(ids, key=lambda u: u.bytes) == ids` over 1000 generated ids,
  **passes by luck**. Measured: all 1000 calls complete inside 1 millisecond (2 distinct
  timestamps, 998 adjacent pairs sharing one), and RFC 9562 orders v7 solely by its 48-bit
  unix_ts_ms prefix — with no monotonic counter the remaining 74 bits are random, so
  intra-millisecond order is *unspecified*. The assertion is a coin flip that would flake in CI
  indefinitely. Assert the guarantee that exists: one id per distinct millisecond sorts in
  creation order, plus an explicitly sleep-separated pair to prove the ordering comes from the
  clock. Generalizes past UUIDs — any test asserting time-ordering over rapidly-generated values
  must force a real time gap rather than assume iteration takes time.
- **On Postgres, "the first error was at revision 28" does not mean 27 revisions passed.**
  Postgres has transactional DDL, so a failed `alembic upgrade head` rolls back the *entire*
  chain — verified: 0 tables created, no `alembic_version` table, after a failure 28 revisions
  deep. Every earlier revision executed inside a transaction that vanished, so none of them is
  validated. This inverts the SQLite intuition, where a mid-chain failure leaves partial schema
  you can inspect to see how far you got. Consequence for a loop: the first error masks all later
  ones, so the only way to enumerate migration defects is fix-and-retry, and any static count
  under-reports. Never scope migration work from where the first error surfaced.
- **A migration's docstring is a statement of its assumptions — read it as a portability
  warning.** `e5f6a7b8c9d0` opens with *"SQLite stores enums as VARCHAR, so no ALTER needed"*.
  That sentence is precisely why it fails on Postgres, where the enum type is real. When
  evaluating whether a migration chain survives a backend change, grep the docstrings for the
  current backend's name before running anything — the authors already documented the coupling.
- **Collection is not a pass. Measure the baseline by running it.** I recorded `pytest --co` →
  1080 collected and wrote *"condition C means ≥1080 passing"* into the harness. The real result
  is **1078 passed / 1 failed / 1 skipped** — one Windows-only failure (`os.kill(pid, 0)`,
  `WinError 87`) that exists on the untouched baseline. A gate calibrated to the collected count
  would have failed on its first run and tripped the circuit breaker over a platform quirk
  unrelated to the work. Note this is the *same defect class* the harness's own pre-flight gate
  exists to catch — an unverified number about the tree — and I committed it anyway. Verify the
  number by producing it, not by inferring it from a cheaper command.
- **A known-failing baseline must be recorded as known-failing, not rounded to clean.** The
  honest gate is "1078 passing, the same one failure, no new red." Writing the aspirational
  number in would have forced whoever ran it to either fix an out-of-scope platform bug or
  quietly weaken the gate.
- **Record the working interpreter/tool invocation in the harness itself.** On this machine
  `python` hits the Microsoft Store shim and fails; `py -m pytest` works. An autonomous loop that
  meets that would spend its whole 3-attempt poison ceiling on a launcher error and mark a good
  task `[!]`. Environment quirks belong in the harness, not in the operator's head.

- **`Grep(path="src")` was silently hiding the entire web layer.** `.gitignore` carried
  `src/web/` (dead — the dir only existed because of the fixed H26 `parents[4]` bug). ripgrep
  drops an anchored pattern's leading component when that component *is* the search root, then
  applies the remainder unanchored: searching from `src` turned `src/web/` into `web/` and
  pruned all of `src/mihomes/web/` — 23 route modules — while `git check-ignore` correctly
  reported the files as *not* ignored. Searching from the repo root or from `src/mihomes` was
  fine, which is exactly why it survived unnoticed.
  **Rule:** a directory-scoped search that returns zero hits for something you have concrete
  reason to expect is a *tool* result to verify, not a fact. Confirm with a file-scoped grep
  before concluding "no matches" — I only caught this because I had read `run_in_executor` with
  Read moments earlier and the directory grep disagreed with my own eyes.
  **Rule:** never calibrate a "no unscoped X remain" gate from a search rooted at `src`.
  A false-green there is invisible: the gate reports clean *because* it looked nowhere.

- **"N tests are blocked on X" is a hypothesis until X lands.** I wrote — in the harness and in two
  commit messages — that 61 integration errors were "one missing artifact, not sixty bugs", the
  missing artifact being the Postgres baseline. The baseline landed and **the count did not move:
  61 before, 61 after.** They were two *stacked* blockers: the schema (`no such column account_id`),
  and behind it a missing account context (`LookupError: current_account`). Fixing the first only
  revealed the second.
  **Rule:** when several tests fail for one visible reason, that reason is the *first* blocker, not
  necessarily the only one. Say "blocked on X, and unknown what is behind it" — a reader planning
  around "61 tests go green when X lands" would have been wrong. The diagnosis was right; the
  extrapolation from it was not.
- **A test that passes alone and fails in the suite is the suite telling you about shared state.**
  This paid out twice in one session. (a) `alembic.ini`'s `fileConfig()` defaults to
  `disable_existing_loggers=True`, so a new migration test's `command.upgrade()` silently switched
  off loggers three `test_email_service` tests assert on. (b) A `SlugMixin` count was 15 alone and 16
  in the suite, because a test-only `Dummy` model registers itself on the shared `Base.metadata` when
  its module imports. Neither was reproducible in isolation, which is exactly why the full run is
  the gate and not an afterthought.
- **Don't assert on source text when you can assert on structure.** A guard I wrote checked
  `"waitlist" not in baseline_source` and failed on the baseline's own **docstring**, which mentions
  `waitlist` to explain why it is absent. Parsing `op.create_table('X')` asserts the schema; grepping
  the file asserts the prose. Same class of error as gating on `pytest --co` instead of a real run.
- **A dead exclusion list is not inert — it is a silent-omission machine.** `IDENTITY_TABLES`
  excluded six tables from autogenerate for good reason, and its own comment said it would retire
  with the tree. Autogenerating the baseline *with it still in place* would have produced a schema
  missing all six identity tables, with no error. This is the third time this session that stale
  config caused silent omission rather than a loud failure (see also the `src/web/` gitignore line
  and G6.1's one-ended type gate). **Deleting retired config is part of the change that retires it.**

- **Reading a lesson is not applying it: I hit the frozen-config trap that `lessons.md` already
  documented, and wrote test files into the user's real data directory.** The storage fixture used
  `monkeypatch.setenv("MIHOMES_DIR", tmp_path)`, but `config.MEDIA_DIR` is computed from that
  variable **at config import time** — exactly the hazard recorded above for `DB_URL`/`DB_DIR`. Eight
  fixture files landed in `~/.mihomes/media/objects`.
  **Rule:** when a test needs to redirect where production code writes, **pass the destination in**
  rather than trying to influence it through the environment. `get_storage(override_root=...)` cannot
  be got wrong; a monkeypatch of a frozen value silently does nothing. The general form: for any
  path-producing global, the safe fixture changes an *argument*, not an *environment variable*.
  **Tell:** a fixture that sets an env var read at import time is a no-op that looks like isolation.
- **Removing an insecure convenience is only half the change — find who depended on it.** Deleting
  the unauthenticated `/uploads` static mount silently broke three writers that returned
  `/uploads/<name>` URLs: the bytes still landed on disk and every resulting link 404'd. The suite
  stayed green because no test fetched those URLs. **Rule:** after removing a serving path, grep for
  everything that produced references to it. A security fix that leaves the feature broken will be
  reverted by whoever notices the breakage first.

- **A security test suite made only of negative assertions is satisfied by a system that returns
  nothing at all.** A21 asserted "account A cannot see B's rows" across 40 tables and four vectors,
  and it stayed **entirely green with the tenant GUC disabled** — because RLS then returns zero rows,
  so every denial is trivially true. Isolation looked perfect precisely because nothing worked.
  **Rule:** every "X cannot reach Y" suite needs a paired positive control ("X *can* reach its own
  X"). Without it, the most complete-looking outcome and the most broken one are indistinguishable.
- **Defence in depth means a test that exercises both layers verifies neither.** Disabling the ORM
  tenant filter outright left the main A21 test green, because it ran on a connection where RLS also
  blocked the read. It was asserting "something stopped this". **Rule:** when two independent
  controls guard the same property, pin each on the configuration where it is the *only* one present
  — otherwise each masks the other's failure and both can rot silently.
- **Mutation-test the gate that matters, and distinguish a toothless test from a bad mutation.**
  Breaking each control and confirming the matching assertion fails found 2 of 4 A21 arms had no
  teeth. A third mutation *also* left its arm green — but there the mutation was wrong, not the test:
  `WITH CHECK` is optional in Postgres (`USING` covers writes when it is absent), so removing it
  changes no behaviour. **A mutation that changes nothing proves nothing**, and reading it as "the
  test is weak" would have sent me rewriting a test that was already correct.

- **Fixing a spec defect is not the same as reporting it, and I conflated the two for six
  groups.** Across G3–G8 I found nine defects in SPEC-002 — including two in §4.4's
  copy-pasteable code, one of which makes the specified filter raise on its first query and
  another that makes sign-in impossible. I fixed each, documented each in
  `build-loop-spec002.md`, and explained each in a commit message. I did **not** route any of
  them to `opportunities.md` until asked. Every artifact I updated was the *builder's*; none
  was the one that goes back to the spec's author.
  This is not a bookkeeping nit. SPEC-002 §0.1 says *"divergence compounds — if SPEC-002 is
  implemented differently than specified, every spec above it inherits the difference"*, and
  SPEC-003…008 are all written against this design. A defect fixed only in the implementation
  leaves the next reader of §4.4 with code that does not run.
  **Rule:** the conventions' three-artifact routing (§5) is a checklist per *finding*, not per
  *group*. A defect in the spec goes to `opportunities.md` **when it is found** — the same turn
  it is worked around — because the workaround is exactly the moment the knowledge exists and
  the moment it stops being visible in the code.
  **Tell:** if a commit message explains a spec defect at length and `opportunities.md` is
  unchanged in that commit, the finding has been recorded for me and lost for everyone else.

- **A green suite means the tests and the code agree — not that the product works.** At G10 I
  disabled a shipped feature (archival), rewrote its five tests to assert the refusal, and
  reported *"1397 passed, no new failures"*. Every word was true and the overall impression was
  wrong: the suite got **greener** as the product got smaller. The user caught it by asking why
  a broken feature wasn't counted as a failure.
  **Rule:** when a fix is "turn it off and assert it's off", the delta is not zero — it is one
  fewer capability. It belongs in a **blocks-ship register** (conventions §3.3: *"visible, not
  silently satisfied"*), not only in a defect log and a docstring. I had not been maintaining
  that register at all; §0.0 of `build-loop-spec002.md` now exists for it.
  **Tell:** if a change makes the suite pass *more* while shipping *less*, say both numbers in
  the same breath — "1397 passing; archival no longer works" — because the first one alone reads
  as progress.
- **Disabling a service is not done until its callers are updated.** Returning `None` for
  `already_archived` left the CLI printing the literal string `"None"` in a Rich table and still
  advertising `mihomes archive run`, a command that now always exits 1. The service was honest;
  the surface the user actually sees was not. **Rule:** after changing a return contract, grep
  the callers — the sweep is three seconds and the alternative is a user-visible `"None"`.
- **`except Exception:` around a SQL statement is safe on SQLite and harmful on Postgres.**
  `archive.py` wrapped a count in `try: ... except Exception: archived = 0` so a missing table
  would degrade gracefully. On Postgres the failed statement **aborts the whole transaction**,
  so the next unrelated query in that session dies with
  `InFailedSqlTransaction: current transaction is aborted, commands ignored until end of
  transaction block` — an error pointing nowhere near the cause. Measured: `get_stats()` raised
  that instead of the `UndefinedTable` it had swallowed.
  **Rule:** to make a statement optional on Postgres, wrap it in a `SAVEPOINT`
  (`session.begin_nested()`) or do not issue it. A broad `except` that keeps using the same
  session is a landmine. Swept the codebase: one instance, now removed.
- **Assert on structure, never on the text of source files — I have now made this mistake
  twice.** In G6.3 a guard asserted `"waitlist" not in baseline_source` and failed on the
  baseline's own docstring explaining why waitlist is absent. In G10 a guard searched test files
  for `CREATE TABLE ... audit_log_archive` and failed on **its own class docstring** describing
  the pattern it was banning. Both times the fix was to assert the real thing: parse
  `op.create_table()` calls; query `inspect(engine).get_table_names()`.
  **Tell:** if a test reads `.py` files as text, it is testing prose. Writing about the thing
  you are banning is normal and must not trip the ban.
- **A test that fabricates the schema it needs proves nothing about production — and can encode
  a leak as an expectation.** `test_archive.py` ran `CREATE TABLE IF NOT EXISTS
  audit_log_archive (id UUID, ...)` in a helper and then asserted archival worked. No migration
  in the tree creates that table, so the tests passed while the feature was broken on every real
  database. Worse: the fabricated table had **no `account_id`**, so the assertion "rows move to
  the archive" was asserting that tenant data moves into an untenanted table.
  **Rule:** if a test creates a table, ask which migration creates it. If none does, the test is
  describing a schema that does not exist.

## Fifth Review Lessons (2026-03-27)
- **SIGPIPE data loss**: When CLI output is piped through `head`/`tail`/etc, SIGPIPE can kill the process before `get_session()` commits. Fix: collect data inside `with get_session()`, print AFTER the session context exits (so commit happens before any output). Critical for commands with long output that modify data.
- Always test CLI commands with `| head -1` to catch SIGPIPE issues.
- Test with adversarial inputs: empty strings, very long strings, negative numbers, invalid dates, non-existent references. These find real user-facing bugs that code reading misses.
