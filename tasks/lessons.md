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
- **Record the working interpreter/tool invocation in the harness itself.** On this machine
  `python` hits the Microsoft Store shim and fails; `py -m pytest` works. An autonomous loop that
  meets that would spend its whole 3-attempt poison ceiling on a launcher error and mark a good
  task `[!]`. Environment quirks belong in the harness, not in the operator's head.

## Fifth Review Lessons (2026-03-27)
- **SIGPIPE data loss**: When CLI output is piped through `head`/`tail`/etc, SIGPIPE can kill the process before `get_session()` commits. Fix: collect data inside `with get_session()`, print AFTER the session context exits (so commit happens before any output). Critical for commands with long output that modify data.
- Always test CLI commands with `| head -1` to catch SIGPIPE issues.
- Test with adversarial inputs: empty strings, very long strings, negative numbers, invalid dates, non-existent references. These find real user-facing bugs that code reading misses.
