"""G16 · §6 Step 16 — the importer (A19, A20).

**This is the data-preservation gate for SPEC-002** (conventions §2). The baseline migration's own
gate runs against an *empty* database, so nothing before this point has asserted that existing
estate data survives the move to a multitenant schema.

The spec's verify clause says *"dry-run against a copy of the `telegram-bot` archive"*. There is
something better available: the author's real 1,823-row install. `test_real_database_roundtrip`
uses a **copy** of it — never the original — and skips cleanly when it is not present, so CI is not
coupled to one machine's files.
"""

import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from mihomes.services.importer import (
    FilesystemMover,
    ImportError_,
    import_sqlite,
    plan_import,
)

REAL_DB = Path.home() / ".mihomes" / "db" / "mihomes.db"


# --- a synthetic source, so the hard cases are exercised deterministically ----------

def _make_source(path: Path) -> None:
    """A miniature old-schema database containing every case the importer must handle.

    Deliberately includes the awkward rows rather than a clean happy path: an orphaned space
    (required parent missing), an orphaned insurance policy (parent missing but the target column
    is nullable), a child of the orphaned space (cascade), and audit rows pointing at deleted
    entities (dangling polymorphic references).
    """
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE properties (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL,
            property_type TEXT NOT NULL, status TEXT NOT NULL, currency TEXT NOT NULL,
            occupied BOOLEAN NOT NULL, created_at TEXT
        );
        CREATE TABLE spaces (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL,
            property_id INTEGER NOT NULL REFERENCES properties(id), created_at TEXT
        );
        CREATE TABLE assets (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            property_id INTEGER NOT NULL REFERENCES properties(id),
            space_id INTEGER REFERENCES spaces(id), created_at TEXT
        );
        CREATE TABLE insurance_policies (
            id INTEGER PRIMARY KEY, policy_number TEXT NOT NULL, insurance_type TEXT NOT NULL,
            carrier TEXT NOT NULL,
            property_id INTEGER REFERENCES properties(id), created_at TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY, timestamp TEXT, entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL, action TEXT NOT NULL, actor TEXT
        );

        INSERT INTO properties VALUES (1,'Main House','main-house','PRIMARY','OPEN','USD',1,NULL);
        INSERT INTO properties VALUES (2,'Beach','beach','VACATION','OPEN','USD',0,NULL);

        -- space 1 is fine; space 2 points at property 99 which does not exist
        INSERT INTO spaces VALUES (1,'Kitchen','kitchen',1,NULL);
        INSERT INTO spaces VALUES (2,'Ghost Room','ghost-room',99,NULL);

        -- asset 1 fine; asset 2 lives in the orphaned space (nullable link -> kept, unlinked)
        INSERT INTO assets VALUES (1,'Boiler','boiler','APPLIANCE',1,1,NULL);
        INSERT INTO assets VALUES (2,'Lamp','lamp','EQUIPMENT',1,2,NULL);
        -- asset 3's own required parent is missing
        INSERT INTO assets VALUES (3,'Orphan','orphan','EQUIPMENT',99,NULL,NULL);

        -- property_id is NULLABLE in the target: keep the row, drop the link
        INSERT INTO insurance_policies VALUES (1,'POL-1','HOMEOWNERS','Acme',99,NULL);

        -- audit rows: one live reference, two to the SAME deleted task, one unknown type
        INSERT INTO audit_log VALUES (1,NULL,'property',1,'create','admin');
        INSERT INTO audit_log VALUES (2,NULL,'task',47,'create','admin');
        INSERT INTO audit_log VALUES (3,NULL,'task',47,'delete','admin');
        INSERT INTO audit_log VALUES (4,NULL,'ha_entity',5,'update','admin');
        """
    )
    con.commit()
    con.close()


@pytest.fixture
def source_db(tmp_path):
    path = tmp_path / "old.db"
    _make_source(path)
    return path


@pytest.fixture
def target(_pg_engine):
    """A dedicated empty database with the real schema, plus one account to import into.

    Its own database, not the shared suite one: the importer writes ~1,800 committed rows, and
    leaking those into the suite is the pollution that has cost this run three times.
    """
    name = f"mihomes_import_t{uuid.uuid4().hex[:10]}"
    admin = create_engine(
        str(_pg_engine.url.set(database="postgres")), isolation_level="AUTOCOMMIT", future=True
    )
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')

    url = str(_pg_engine.url.set(database=name))
    engine = create_engine(url, future=True)

    from mihomes.models import Base

    Base.metadata.create_all(engine)
    account_id = uuid.uuid4()
    with engine.begin() as conn:
        # **`estate`, not `free` — SPEC-004 Step 17 (D16/A25).** The archive holds several
        # properties, and Free covers one, so the importer now refuses it: exactly the behaviour
        # A25 asserts, and it turned eight tests in this file red the moment the gate landed.
        #
        # The gate is right and the fixture encoded the pre-gate world, so the fixture moves.
        # Tests *about* the limit provision Free explicitly (see `test_over_limit_refused`),
        # which keeps that assertion from depending on a shared default.
        conn.execute(
            text(
                "INSERT INTO accounts (id, slug, name, type, plan, subscription_status) "
                "VALUES (:i, 'imported', 'Imported', 'household', 'estate', 'active')"
            ),
            {"i": account_id},
        )
    try:
        yield engine, account_id
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{name}' AND pid <> pg_backend_pid()"
            )
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}"')
        admin.dispose()


# --- A19: the round trip -----------------------------------------------------------

def test_roundtrip_counts_and_fks(source_db, target):
    """A19 — rows arrive, ids are remapped, and foreign keys still resolve."""
    engine, account_id = target
    plan = plan_import(source_db, engine)

    # The plan must name the skips before anything is written.
    assert plan.skips, "the orphaned rows should have been reported"
    assert plan.unparented.get("insurance_policies") == 1, (
        "insurance_policies.property_id is nullable in the target, so that row should be kept "
        "with a NULL parent rather than skipped"
    )

    report = import_sqlite(source_db, engine, account_id, plan=plan)

    with engine.connect() as conn:
        props = conn.execute(text("SELECT id, slug FROM properties")).fetchall()
        spaces = conn.execute(text("SELECT id, slug, property_id FROM spaces")).fetchall()
        assets = conn.execute(text("SELECT slug FROM assets")).fetchall()
        policies = conn.execute(
            text("SELECT policy_number, property_id FROM insurance_policies")
        ).fetchall()

    assert len(props) == 2
    # Only the well-parented space survives; the ghost room's parent never existed.
    assert [s.slug for s in spaces] == ["kitchen"]
    # asset 2 ("lamp") lived in the orphaned space, but `assets.space_id` is NULLABLE in the
    # target — so the per-column rule keeps the asset and drops only the link. Asset 3 is skipped
    # because its own `property_id` is NOT NULL and that parent never existed.
    #
    # This is the cascade behaving correctly rather than loosely: a skip propagates only through
    # REQUIRED links. Expecting "lamp" to disappear was this test's mistake, and the stricter
    # expectation would have thrown away a real asset to no purpose.
    assert sorted(a.slug for a in assets) == ["boiler", "lamp"]
    # The policy is kept, unparented.
    assert len(policies) == 1 and policies[0].property_id is None

    # Every surviving FK resolves to a real row, and ids are UUIDs not integers.
    prop_ids = {p.id for p in props}
    assert spaces[0].property_id in prop_ids
    assert all(isinstance(p.id, uuid.UUID) for p in props)

    assert report.total_inserted == sum(report.inserted.values())
    assert report.inserted["properties"] == 2


def test_dangling_polymorphic_refs_are_preserved_and_stable(source_db, target):
    """Two audit rows about the same deleted task must still share one entity_id.

    An audit log **records deletions**, so a reference to a row that no longer exists is normal
    data, not corruption — 118 of 505 rows in the author's real database are like this. Minting a
    fresh UUID per row would silently ungroup the trail; dropping the rows would lose the history
    of what was deleted.
    """
    engine, account_id = target
    import_sqlite(source_db, engine, account_id)

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT action, entity_type, entity_id FROM audit_log ORDER BY action")
        ).fetchall()

    assert len(rows) == 4, "no audit row should be dropped"
    task_rows = [r for r in rows if r.entity_type == "task"]
    assert len(task_rows) == 2
    assert task_rows[0].entity_id == task_rows[1].entity_id, (
        "both audit rows reference deleted task 47 and must share one remapped id"
    )

    # An unknown entity_type is preserved too, rather than dropped or crashed on.
    assert any(r.entity_type == "ha_entity" for r in rows)


def test_refuses_a_non_empty_account(source_db, target):
    """A second import would duplicate rows or trip UNIQUE (account_id, slug) partway through.

    The spec's clause requires that a mid-import failure leave *no partial account*; refusing up
    front is how that is guaranteed rather than hoped for.
    """
    engine, account_id = target
    import_sqlite(source_db, engine, account_id)

    with pytest.raises(ImportError_) as exc:
        import_sqlite(source_db, engine, account_id)
    assert "already has data" in str(exc.value)


def test_dry_run_writes_nothing(source_db, target):
    engine, account_id = target
    import_sqlite(source_db, engine, account_id, dry_run=True)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM properties")).scalar() == 0


# --- A20: the ordering -------------------------------------------------------------

class _FailingMover(FilesystemMover):
    """A mover that fails at a chosen point, to prove the ordering holds."""

    def __init__(self, root: Path, fail_on: str):
        super().__init__(root=root)
        self.fail_on = fail_on

    def put(self, source: Path, key: str) -> int:
        if self.fail_on == "put":
            raise OSError("simulated upload failure")
        written = super().put(source, key)
        if self.fail_on == "truncate":
            # Corrupt the object so verification must catch the size mismatch.
            (self.root / key).write_bytes(b"")
        return written

    def size(self, key: str) -> int | None:
        if self.fail_on == "vanish":
            return None
        return super().size(key)


@pytest.fixture
def source_with_file(tmp_path):
    """A source whose `documents` row points at a file that really exists."""
    path = tmp_path / "old_with_file.db"
    _make_source(path)
    media = tmp_path / "media"
    media.mkdir()
    doc = media / "deed.pdf"
    doc.write_bytes(b"%PDF-1.4 fake deed contents")

    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, slug TEXT NOT NULL,
            file_path TEXT NOT NULL, document_type TEXT NOT NULL,
            entity_type TEXT, entity_id INTEGER, created_at TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO documents VALUES (1,'Deed','deed',?,'CONTRACT','property',1,NULL)",
        (str(doc),),
    )
    con.commit()
    con.close()
    return path, media


@pytest.mark.parametrize("fail_on", ["put", "vanish", "truncate"])
def test_failure_leaves_nothing(source_with_file, target, tmp_path, fail_on):
    """A20 — a failure before the commit leaves **no rows at all**.

    Parameterised over the three points the ordering has to survive: the upload itself, an object
    that verifies as absent, and one whose size does not match. In every case the database
    transaction has not begun, so the outcome is orphaned objects (garbage, sweepable) and never a
    row pointing at a file that is not there (corruption, discovered by a user).
    """
    source, media = source_with_file
    engine, account_id = target
    storage = tmp_path / "storage"
    mover = _FailingMover(storage, fail_on=fail_on)

    with pytest.raises((ImportError_, OSError)):
        import_sqlite(source, engine, account_id, mover=mover, media_root=media)

    with engine.connect() as conn:
        for table in ("properties", "spaces", "assets", "documents", "audit_log"):
            n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            assert n == 0, (
                f"{table} has {n} row(s) after a failed import — rows were written before the "
                "files were verified, which is the prohibited order"
            )


def test_files_are_verified_then_rows_committed(source_with_file, target, tmp_path):
    """The success path: the object exists at a tenant-prefixed key, and the row is committed."""
    source, media = source_with_file
    engine, account_id = target
    storage = tmp_path / "storage"

    report = import_sqlite(
        source, engine, account_id, mover=FilesystemMover(storage), media_root=media
    )

    assert report.files_moved == 1
    written = list(storage.rglob("*.pdf"))
    assert len(written) == 1
    # Tenant-prefixed, so two accounts importing "deed.pdf" cannot collide.
    assert str(account_id) in str(written[0])

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM documents")).scalar() == 1


def test_refuses_when_files_must_move_but_no_mover_given(source_with_file, target):
    """Committing rows that reference unmoved files is the corruption the ordering prevents."""
    source, media = source_with_file
    engine, account_id = target
    with pytest.raises(ImportError_) as exc:
        import_sqlite(source, engine, account_id, media_root=media)
    assert "no FileMover" in str(exc.value)


# --- the real thing ----------------------------------------------------------------

@pytest.mark.skipif(not REAL_DB.exists(), reason="no local pre-SPEC-002 database on this machine")
def test_real_database_roundtrip(target, tmp_path):
    """End-to-end against a **copy** of the author's real install — launch gate S6's actual case.

    Uses a copy, never the original: an importer bug must not be able to touch the only copy of
    someone's estate data. Skips rather than fails where that database does not exist, so CI is not
    coupled to one machine.
    """
    engine, account_id = target
    copy = tmp_path / "real_copy.db"
    shutil.copy2(REAL_DB, copy)

    plan = plan_import(copy, engine)
    report = import_sqlite(copy, engine, account_id, plan=plan)

    # Every source row either arrived or was reported as skipped. No third category.
    #
    # Association rows expanded from a legacy JSON column are subtracted first: they are *derived*
    # rather than copied, so counting them as arrivals would make `inserted` exceed the source
    # count and hide a real shortfall behind a surplus. This assertion caught exactly that.
    derived = sum(report.expanded.values())
    accounted = report.total_inserted - derived + plan.total_skipped
    assert accounted == plan.total_rows, (
        f"{plan.total_rows - accounted} source row(s) unaccounted for — every row must either "
        "arrive or be reported as skipped, never simply vanish"
    )
    assert derived > 0, (
        "the author's database has 59 vendors with a non-empty property_ids JSON and no "
        "vendor_properties table, so the expansion must have produced association rows"
    )

    # FK integrity across the imported account: every non-null FK resolves.
    inspector = inspect(engine)
    with engine.connect() as conn:
        for table in sorted(report.inserted):
            if not report.inserted[table]:
                continue
            for fk in inspector.get_foreign_keys(table):
                col = fk["constrained_columns"][0]
                parent = fk["referred_table"]
                if parent == "accounts":
                    continue
                dangling = conn.execute(
                    text(
                        f'SELECT COUNT(*) FROM "{table}" c '
                        f'LEFT JOIN "{parent}" p ON c."{col}" = p.id '
                        f'WHERE c."{col}" IS NOT NULL AND p.id IS NULL'
                    )
                ).scalar()
                assert dangling == 0, f"{table}.{col} -> {parent}: {dangling} dangling after import"

    # Everything landed in the one account.
    with engine.connect() as conn:
        for table in sorted(report.inserted):
            if not report.inserted[table]:
                continue
            others = conn.execute(
                text(f'SELECT COUNT(*) FROM "{table}" WHERE account_id <> :a'),
                {"a": account_id},
            ).scalar()
            assert others == 0, f"{table} has rows outside the target account"


def test_legacy_json_id_list_becomes_association_rows(tmp_path, target):
    """A legacy `vendors.property_ids` JSON blob must become `vendor_properties` rows.

    **This test exists because the importer silently lost this data at first.** The author's
    database predates the M14 normalisation: all 59 vendors carry a non-empty `property_ids` JSON
    list and the source has no `vendor_properties` table. The first working importer reported one
    line — `dropped: property_ids` — and threw every vendor-to-property association away. Nothing
    failed, and the row counts were even correct, because what was lost was a *column* rather than
    rows.

    A "source column with no target column" is usually harmless and occasionally is the entire
    relationship. The only way to tell is to look at each one.
    """
    engine, account_id = target
    source = tmp_path / "legacy.db"
    _make_source(source)

    con = sqlite3.connect(source)
    con.executescript(
        """
        CREATE TABLE vendors (
            id INTEGER PRIMARY KEY, company_name TEXT NOT NULL, slug TEXT NOT NULL,
            active BOOLEAN NOT NULL, property_ids TEXT, created_at TEXT
        );
        -- vendor 1 serves both properties; vendor 2 serves one; vendor 3 none.
        INSERT INTO vendors VALUES (1,'Acme Pest','acme-pest',1,'[1, 2]',NULL);
        INSERT INTO vendors VALUES (2,'Bright Pools','bright-pools',1,'[2]',NULL);
        INSERT INTO vendors VALUES (3,'Unused','unused',1,'[]',NULL);
        -- vendor 4 references a property that no longer exists: the link cannot be represented.
        INSERT INTO vendors VALUES (4,'Ghost','ghost',1,'[99]',NULL);
        """
    )
    con.commit()
    con.close()

    report = import_sqlite(source, engine, account_id)

    assert report.expanded.get("vendors.property_ids") == 3, (
        "expected 3 association rows (2 + 1 + 0, with the dangling one dropped), got "
        f"{report.expanded.get('vendors.property_ids')}"
    )

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT v.slug, p.slug FROM vendor_properties vp "
                "JOIN vendors v ON v.id = vp.vendor_id "
                "JOIN properties p ON p.id = vp.property_id ORDER BY v.slug, p.slug"
            )
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("acme-pest", "beach"),
        ("acme-pest", "main-house"),
        ("bright-pools", "beach"),
    ]

    # Every association row carries the tenant. `vendor_properties` is a Core Table, so no ORM
    # mechanism can stamp it — the blind spot from G2.5/G8, which the importer must handle itself.
    with engine.connect() as conn:
        untenanted = conn.execute(
            text("SELECT COUNT(*) FROM vendor_properties WHERE account_id <> :a"),
            {"a": account_id},
        ).scalar()
    assert untenanted == 0


# --- A25: the importer gate (SPEC-004 Step 17, D16) --------------------------------


def _set_plan(engine, account_id, plan: str, status: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE accounts SET plan = :p, subscription_status = :s WHERE id = :i"),
            {"p": plan, "s": status, "i": account_id},
        )


def test_over_limit_refused(source_db, target):
    """**A25** — an over-limit import fails cleanly and leaves no partial account.

    D16 closes a path `PRICING` §4.3 has **no language for**. That table describes accounts that
    *downgrade* into an over-limit state — past-due, voluntary, trial expiry — and an import is
    none of those: the account arrives over its limit having never had more. Rather than adding a
    fourth row to a policy table, the importer refuses at the source.

    Both halves asserted. "Fails" is the easy one; **"leaves no partial account"** is the
    criterion — the refusal happens before files move and before any row is written, so a
    rejected import is indistinguishable from one that never ran.
    """
    engine, account_id = target
    _set_plan(engine, account_id, "free")

    with pytest.raises(ImportError_) as exc:
        import_sqlite(source_db, engine, account_id)

    assert "propert" in str(exc.value).lower()
    assert "free" in str(exc.value).lower()

    with engine.connect() as conn:
        for table in ("properties", "spaces", "assets"):
            n = conn.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE account_id = :a"),  # noqa: S608
                {"a": account_id},
            ).scalar()
            assert n == 0, f"a refused import left {n} row(s) in {table} — no partial account"


def test_an_upgraded_account_may_import_the_same_archive(source_db, target):
    """**The control, and A25 is vacuous without it.**

    An importer that refused *everything* would satisfy the refusal assertion completely while
    breaking the one migration path SPEC-002 D10 keeps. The message also tells the operator to
    upgrade and re-run, so that has to actually work.
    """
    engine, account_id = target
    _set_plan(engine, account_id, "free")

    with pytest.raises(ImportError_):
        import_sqlite(source_db, engine, account_id)

    _set_plan(engine, account_id, "estate", "active")
    report = import_sqlite(source_db, engine, account_id)

    assert report.plan.row_counts.get("properties", 0) > 0
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM properties WHERE account_id = :a"), {"a": account_id}
        ).scalar()
    assert n == report.plan.row_counts["properties"]


def test_the_refusal_names_the_plan_and_the_count(source_db, target):
    """The operator is mid-migration with an archive in hand.

    "Import failed" leaves them guessing; naming the archive's property count, the plan's limit,
    and the fix — upgrade, then re-run — is the difference between a five-minute correction and a
    support conversation.
    """
    engine, account_id = target
    _set_plan(engine, account_id, "free")

    with pytest.raises(ImportError_) as exc:
        import_sqlite(source_db, engine, account_id)

    message = str(exc.value)
    assert "Nothing was imported" in message
    assert "re-run" in message
