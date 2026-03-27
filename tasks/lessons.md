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
