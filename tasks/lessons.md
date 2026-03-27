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
