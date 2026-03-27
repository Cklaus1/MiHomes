# MiHomes — Phase 1a Build Plan

## Chunk 1: Project Skeleton + Database Foundation
- [ ] pyproject.toml with deps and CLI entry point
- [ ] src/mihomes/__init__.py, config.py, db.py
- [ ] models/__init__.py with Base, TimestampMixin, SlugMixin
- [ ] alembic/ setup with dynamic DB path
- [ ] Verify: pip install -e . and empty migration

## Chunk 2: Slug Generation + Audit Log
- [ ] services/slug.py (generate, ensure_unique, resolve_identifier)
- [ ] models/audit_log.py + services/audit.py
- [ ] tests/unit/test_slug.py, test_audit.py
- [ ] Verify: pytest passes

## Chunk 3: Property + Space (First E2E Stack)
- [ ] models/property.py, models/space.py
- [ ] services/property.py, services/space.py
- [ ] cli/__init__.py (root app), cli/property.py, cli/space.py
- [ ] cli/formatters.py (shared Rich helpers)
- [ ] First alembic migration
- [ ] tests for property service + CLI
- [ ] Verify: full CLI round-trip

## Chunk 4: Staff + Vendor
- [ ] models/staff.py, models/vendor.py
- [ ] services/staff.py, services/vendor.py
- [ ] cli/staff.py, cli/vendor.py
- [ ] Migration + tests
- [ ] Verify: staff add with property assignment

## Chunk 5: Task + Recurrence Engine
- [ ] models/task.py, models/task_schedule.py
- [ ] services/recurrence.py (calculate_next_due, seasonal)
- [ ] services/task.py (CRUD + complete with advance)
- [ ] cli/task.py
- [ ] tests/unit/test_recurrence.py (critical)
- [ ] Verify: recurring task complete → next occurrence created

## Chunk 6: Issue Tracking
- [ ] models/issue.py
- [ ] services/issue.py (CRUD + resolve lifecycle)
- [ ] cli/issue.py
- [ ] Verify: full lifecycle add → resolve

## Chunk 7: Budget + Transactions
- [ ] models/budget.py, models/transaction.py
- [ ] services/budget.py
- [ ] cli/budget.py, cli/expense.py (separate sub-apps)
- [ ] Verify: budget set → expense add → report shows variance

## Chunk 8: Notes, Config, Alerts, Staff Schedule
- [ ] models/note.py, models/configuration.py, models/alert.py
- [ ] services/note.py, services/config.py, services/alerts.py
- [ ] cli/note.py, cli/config.py, cli/alerts.py
- [ ] Staff schedule + workload commands
- [ ] Verify: notes, config, alerts all working

## Chunk 9: Init Wizard + Demo Data
- [ ] cli/init.py (setup wizard, --demo)
- [ ] services/demo.py (sample data)
- [ ] First-run detection, mihomes version
- [ ] Verify: clean init --demo → property list works

## Chunk 10: Dashboard + Audit CLI
- [ ] services/dashboard.py (data aggregation)
- [ ] cli/dashboard.py (Rich layout)
- [ ] cli/audit.py (entity history, recent changes)
- [ ] Verify: mihomes dashboard renders after demo data

## Chunk 11: Polish
- [ ] Consistent edit/delete across all entities
- [ ] Delete cascade warnings
- [ ] mihomes help curated overview
- [ ] Shell completion
- [ ] Final migration

## Chunk 12: Test Suite + Final Verification
- [ ] tests/conftest.py (fixtures)
- [ ] Full unit + integration test coverage
- [ ] tests/e2e/test_full_workflow.py
- [ ] pytest --cov >80% on services
