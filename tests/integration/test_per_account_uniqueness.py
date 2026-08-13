"""G5 · §6 Step 5 — uniqueness is per account, not global.

The spec's verify clause, verbatim: *"two accounts can each create a 'main-house' property
and a 'Plumbing' tag."* Under the pre-SPEC-002 schema `slug` and `tags.name` were globally
unique, so the **second** account to name a property "Main House" got an IntegrityError —
a tenant learning another tenant's data exists is itself the leak, before the failure is
even inconvenient.

The mirror assertion matters just as much: uniqueness must still bite *within* one account.
A test that only proved two accounts can coexist would also pass if the constraint had been
dropped altogether.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from mihomes.models import Base
from mihomes.services import property as prop_svc
from mihomes.services import tag as tag_svc
from mihomes.tenancy import account_context


def _session_for(engine, connection, account_id):
    return sessionmaker(bind=connection, future=True,
                        join_transaction_mode="create_savepoint")()


def test_two_accounts_can_reuse_a_slug_and_a_tag_name(_pg_engine, account_a, account_b):
    """The spec's clause. Same property name and same tag name in both accounts."""
    connection = _pg_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection, future=True,
                           join_transaction_mode="create_savepoint")
    try:
        for account in (account_a, account_b):
            with account_context(account):
                with Session() as s:
                    prop = prop_svc.create_property(s, "Main House")
                    assert prop.slug == "main-house", (
                        "the second account got a de-duplicated slug, which means slug "
                        f"uniqueness is still global: {prop.slug!r}"
                    )
                    tag_svc.create_tag(s, "Plumbing")
                    s.commit()

        # Both rows really are present, one per account.
        rows = connection.execute(
            text("SELECT account_id FROM properties WHERE slug = 'main-house'")
        ).fetchall()
        assert len(rows) == 2, f"expected one 'main-house' per account, got {len(rows)}"
        assert {r[0] for r in rows} == {account_a, account_b}
    finally:
        transaction.rollback()
        connection.close()


def test_slug_still_unique_within_one_account(_pg_engine, account_a):
    """The mirror: per-account does not mean unenforced.

    Raw SQL, because `create_property` de-duplicates slugs in the service layer
    (`ensure_unique_slug`) and would never hand the database a collision.
    """
    insert = text(
        "INSERT INTO properties "
        "(id, account_id, name, slug, property_type, status, currency, occupied) "
        "VALUES (:id, :acct, 'Dup', 'dup-slug', 'PRIMARY', 'OPEN', 'USD', false)"
    )
    connection = _pg_engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(insert, {"id": uuid.uuid4(), "acct": account_a})
        with pytest.raises(IntegrityError) as exc:
            connection.execute(insert, {"id": uuid.uuid4(), "acct": account_a})
        assert "uq_properties_account_slug" in str(exc.value), (
            f"rejected, but not by the per-account slug constraint: {exc.value}"
        )
    finally:
        transaction.rollback()
        connection.close()


def test_every_slug_table_has_the_constraint():
    """Enumerated from the mixin rather than a hand-list, so a 16th SlugMixin model
    cannot be added without the constraint.

    `TEST_ONLY_TABLES` is excluded: a test module's `Dummy` model registers itself into
    `Base.registry` as soon as it is imported, so this count is 15 when the file runs alone
    and 16 under the full suite. (That is also why the pre-flight measured "16 SlugMixin
    classes" — it counted `dummy` — so the earlier correction of 15 to 16 was right about
    the number and wrong about which tables.)
    """
    from mihomes.models import SlugMixin
    from mihomes.tenancy.registry import TEST_ONLY_TABLES

    slug_tables = sorted(
        m.class_.__tablename__
        for m in Base.registry.mappers
        if issubclass(m.class_, SlugMixin)
        and m.class_.__tablename__ not in TEST_ONLY_TABLES
    )
    assert len(slug_tables) == 15, f"SlugMixin table count changed: {slug_tables}"
    for table in slug_tables:
        names = {c.name for c in Base.metadata.tables[table].constraints}
        assert f"uq_{table}_account_slug" in names, (
            f"{table} uses SlugMixin but has no UNIQUE (account_id, slug). Present: "
            f"{sorted(names)}"
        )
    # F4: tag names too.
    tag_constraints = {c.name for c in Base.metadata.tables["tags"].constraints}
    assert "uq_tags_account_name" in tag_constraints


def test_task_schedule_task_id_stays_globally_unique():
    """The spec says to skip this one, so assert it was skipped rather than trusting it.

    `task_schedules.task_id UNIQUE` is a one-schedule-per-task rule. task_id is already
    account-scoped through the task, so making it per-account would weaken it to no
    purpose — but a sweep over "every unique constraint" would have caught it.
    """
    table = Base.metadata.tables["task_schedules"]
    task_id = table.c.task_id
    assert task_id.unique is True, "task_schedules.task_id lost its global UNIQUE"
    assert "uq_task_schedules_account_task_id" not in {c.name for c in table.constraints}
