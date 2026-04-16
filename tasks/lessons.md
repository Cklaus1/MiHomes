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

## Fifth Review Lessons (2026-03-27)
- **SIGPIPE data loss**: When CLI output is piped through `head`/`tail`/etc, SIGPIPE can kill the process before `get_session()` commits. Fix: collect data inside `with get_session()`, print AFTER the session context exits (so commit happens before any output). Critical for commands with long output that modify data.
- Always test CLI commands with `| head -1` to catch SIGPIPE issues.
- Test with adversarial inputs: empty strings, very long strings, negative numbers, invalid dates, non-existent references. These find real user-facing bugs that code reading misses.
