# Legacy SQLite revisions — reference only, never run

These 40 revisions built the single-user SQLite schema up to `4db594964c82`. SPEC-002
Step 6 replaced them with a single squashed baseline, `alembic/versions/0001_pg_baseline.py`.

**They are not on Alembic's search path.** `version_locations` defaults to
`<script_location>/versions`, so moving them here is what retires them — nothing else was
needed. They are kept because they are the only record of how the schema evolved, and
several carry data-migration logic (orphan cleanup before an FK was added, enum-default
normalisation, the float → `Money` cast) whose *reasoning* is worth reading even though
the code will not run again.

## Why they cannot simply be replayed against Postgres

The chain is SQLite-only, and not by accident. `e5f6a7b8c9d0` opens with

> SQLite stores enums as VARCHAR, so no ALTER needed

which is exactly the assumption that breaks on Postgres, where the enum type is real. Several
others rely on SQLite batch mode (copy → `DROP` → `RENAME`) and on `PRAGMA foreign_keys=OFF`,
neither of which has a Postgres equivalent. Replaying them was measured and does not work;
this is why Step 6 squashes rather than ports.

## Tests deleted with them

`tests/integration/test_migration_reconciliation.py` and `test_money_migration.py` replayed
this chain and were removed in the same commit — 9 tests. They asserted properties of a
migration path that no longer executes: orphan cleanup, FK enforcement after the fact, enum
normalisation, and that the one-time float → `Money` cast preserved values.

Nothing of lasting value went with them:

- the `Money` type's own behaviour is covered by `tests/unit/test_money_type.py`, which tests
  the type rather than the migration that introduced it;
- the empty-autogenerate oracle those files carried (models match schema) is now enforced
  against the *live* tree by `tests/integration/test_pg_baseline.py`, on Postgres — a
  strictly stronger check than the SQLite version it replaces, which had to be skipped from
  G2 onward because SPEC-002 deliberately breaks the SQLite schema's agreement with the models.

## Do not add to this directory

New migrations go in `alembic/versions/` as deltas on `0001_pg_baseline`.
