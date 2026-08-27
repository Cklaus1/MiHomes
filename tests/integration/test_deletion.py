"""G8 · §6 Step 8 — account deletion (A7, A9, A10, A28, A29, A29b).

The other half of the GA gate at `SAAS_PRD:193`, and the spec's own framing of the stake: *"a
deletion that misses a table leaves personal data behind and is a regulatory finding, not a bug
report."*

**Every purge test runs against a fully populated account.** §9: *"A purge test against an account
with three rows proves nothing."* `tests/helpers/populate.py` derives one row in each of the 49
tenant tables from `Base.metadata`, and `assert_fully_populated` fails if any table is empty — so
an assertion that a table is empty *after* the purge cannot pass because it was empty before.
"""

from __future__ import annotations

import datetime
import json
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from mihomes.models import Base
from mihomes.services.privacy.deletion import (
    ANONYMIZE,
    ANONYMIZED_TABLES,
    DELETE,
    DISPOSITIONS,
    PRESERVED_TABLES,
    account_referencing_global_columns,
    cancel_deletion,
    disposition_for,
    purge,
    request_deletion,
)
from mihomes.tenancy.context import account_context
from mihomes.tenancy.registry import GLOBAL_TABLES, TENANT_TABLES
from tests.helpers.populate import assert_fully_populated, populate_account


@pytest.fixture
def populated(session, account_a):
    """One row in every tenant table, seeded **through the test's own session**.

    The first version committed on a separate raw connection and **deadlocked the suite**: the
    `session` fixture holds an open transaction for the whole test, so its uncommitted INSERT
    into `account_deletion_requests` blocked the fixture's teardown DELETE against the same
    table, and pytest hung with no output at all. Diagnosed from `pg_stat_activity` — one
    backend `active` on `Lock`, one `idle in transaction`.

    Seeding through `session` removes both the second connection and the teardown: everything
    this fixture writes rolls back with the test, which is the property the rest of the suite
    already relies on.

    The trade is that the rows are not committed, so a test wanting data its *own* session
    cannot see must still use a raw connection — no test here needs that. What every purge test
    needs is rows in all 49 tables, and `assert_fully_populated` proves they are there before
    any assertion about their absence afterwards.
    """
    user_id = uuid.uuid4()
    connection = session.connection()
    connection.execute(
        text(
            "INSERT INTO users (id, google_sub, email, name, created_at) "
            "VALUES (:id, :sub, :email, 'Purge Fixture', now())"
        ),
        {"id": user_id, "sub": str(user_id), "email": f"{user_id}@example.com"},
    )
    populate_account(connection, account_a, user_id)
    assert_fully_populated(connection, account_a)
    return user_id


def _rows(session, table_name, account_id) -> int:
    """Row count via the test's session — the fixture's rows are uncommitted."""
    table = Base.metadata.tables[table_name]
    return session.execute(
        sa.select(sa.func.count())
        .select_from(table)
        .where(table.c.account_id == account_id)
    ).scalar()


# --- the state machine (A9) ---------------------------------------------------------------


def test_request_deletes_nothing(session, account_a, populated):
    """`requested` starts a clock. It must not touch a single row."""
    before = _rows(session, "properties", account_a)

    with account_context(account_a):
        request = request_deletion(session, account_a, populated)
        session.flush()

    assert request.purged_at is None
    assert request.cancelled_at is None
    assert request.purge_after > request.requested_at
    assert _rows(session, "properties", account_a) == before


def test_requesting_twice_returns_the_same_request(session, account_a, populated):
    """Two rows would mean two `purge_after` dates and no answer to "when was this asked for"."""
    with account_context(account_a):
        first = request_deletion(session, account_a, populated)
        second = request_deletion(session, account_a, populated)

    assert first.id == second.id


def test_cancel(session, account_a, populated):
    """**A9** — a cancelled deletion restores normal service.

    "Restores" is asserted as *the data is still there and a fresh request can be made*, not
    merely that a timestamp was set: a cancel that stamped the row while the purge had already
    run would satisfy a timestamp check and be the cruellest possible bug.
    """
    before = _rows(session, "properties", account_a)

    with account_context(account_a):
        request_deletion(session, account_a, populated)
        assert cancel_deletion(session, account_a) is True
        session.flush()

        assert _rows(session, "properties", account_a) == before

        # Normal service: the account can ask again, and gets a NEW request rather than the
        # cancelled one back.
        fresh = request_deletion(session, account_a, populated)
        assert fresh.cancelled_at is None


def test_cancelling_twice_is_not_an_error(session, account_a, populated):
    """Idempotent — the second call reports that nothing changed."""
    with account_context(account_a):
        request_deletion(session, account_a, populated)
        assert cancel_deletion(session, account_a) is True
        assert cancel_deletion(session, account_a) is False


def test_a_purged_account_cannot_be_cancelled(session, account_a, populated):
    """A purge cannot be undone, and the code must not pretend otherwise."""
    with account_context(account_a):
        request = request_deletion(session, account_a, populated)
        purge(session, request)
        session.flush()

        assert cancel_deletion(session, account_a) is False


# --- the purge (A7, A28) -------------------------------------------------------------------


def test_purge_dispositions_all_tables(session, account_a, populated):
    """**A28** — every tenant table is reached and carries **exactly one** disposition.

    The partition is asserted as total against `TENANT_TABLES` discovered at test time, which is
    what makes it a gate rather than a description: a model added tomorrow appears in the
    registry, and if the purge does not reach it this fails.

    `ANONYMIZE` being empty today does not weaken it — the assertion is that every table lands
    in exactly one of three buckets, so the empty bucket is still part of a total partition.
    """
    with account_context(account_a):
        request = request_deletion(session, account_a, populated)
        manifest = purge(session, request)
        session.flush()

    for table_name in sorted(TENANT_TABLES):
        assert table_name in manifest, (
            f"{table_name} is account-scoped and the purge never reached it — a missed table "
            f"leaves personal data behind after an erasure request"
        )
        entry = manifest[table_name]
        assert entry["disposition"] in DISPOSITIONS, entry
        # Exactly one: a table cannot be both preserved and deleted.
        buckets = [
            table_name in PRESERVED_TABLES,
            table_name in ANONYMIZED_TABLES,
            disposition_for(table_name) == DELETE,
        ]
        assert sum(buckets) == 1, f"{table_name} has {sum(buckets)} dispositions, not 1"


def test_the_manifest_records_counts_not_just_names(session, account_a, populated):
    """A bare list of tables cannot distinguish "held nothing" from "never reached".

    That distinction is the manifest's whole job (D18) — and how support answers "what was
    deleted" versus "what was kept without your name on it".
    """
    with account_context(account_a):
        request = request_deletion(session, account_a, populated)
        purge(session, request)
        session.flush()

    stored = json.loads(request.purge_manifest)
    assert stored["properties"]["rows"] >= 1, (
        "the fixture populates every table, so a zero here means the purge counted after "
        "deleting rather than before"
    )


def test_purge_complete(session, account_a, populated):
    """**A7** — zero rows survive in every `DELETE` table.

    **Scoped to the `DELETE` disposition, deliberately.** A7 as written says "every
    `TenantOwned` table", and `account_deletion_requests` is both `TenantOwned` and `PRESERVE`
    — so a literal reading contradicts A29, which requires that same row to survive. The two
    criteria are consistent only if A7 means the tables the purge deletes.
    """
    with account_context(account_a):
        request = request_deletion(session, account_a, populated)
        purge(session, request)
        session.flush()

    survivors = {
        name: _rows(session, name, account_a)
        for name in sorted(TENANT_TABLES)
        if disposition_for(name) == DELETE
    }
    assert not any(survivors.values()), {
        name: count for name, count in survivors.items() if count
    }


# --- what survives, and why (A29, A29b) ----------------------------------------------------


def test_deliberate_survivors(session, account_a, populated):
    """**A29** — the deletion record and the suppression list survive untouched.

    The deletion record is the artifact a regulator asks for; forgetting a suppression is the
    failure the erasure request was protecting against, not a form of privacy.
    """
    from mihomes.services.email.suppression import suppress

    address = f"survivor-{uuid.uuid4().hex[:8]}@example.com"

    with account_context(account_a):
        suppress(session, address, reason="unsubscribe")
        session.flush()
        request = request_deletion(session, account_a, populated)
        purge(session, request)
        session.flush()

    # The proof of the request, with its manifest.
    #
    # Two rows, not one: the populated fixture seeds a *cancelled* request (it has to be
    # resolved, or `request_deletion`'s idempotency hands it back instead of creating one).
    # Both survive, which is the point — `PRESERVE` means untouched, not "the latest one".
    assert _rows(session, "account_deletion_requests", account_a) == 2
    assert request.purged_at is not None
    assert request.purge_manifest

    # The suppression, unchanged.
    remaining = session.execute(
        text("SELECT count(*) FROM email_suppressions WHERE address = :a"),
        {"a": address},
    ).scalar()
    assert remaining == 1, "a suppressed address must stay suppressed after the purge"


def test_anonymize_is_declared_even_though_it_is_empty(session, account_a, populated):
    """D18's trap, asserted rather than described.

    `ANONYMIZE` is **not** a third kind of exclusion. Both `PRESERVE` entries are *skips*, and a
    skipped row keeps its `account_id` — so using `PRESERVE` for authored content would look
    like caution and be a violation. Anonymize is an UPDATE.

    Empty today (SPEC-008's `VendorReview` is the first real member) and declared anyway, so the
    partition stays total and the distinction survives until someone needs it.
    """
    assert ANONYMIZED_TABLES == {}, (
        "a table joined ANONYMIZE — confirm its author columns are NULLABLE, because a "
        "NOT NULL column cannot be anonymized"
    )
    assert ANONYMIZE in DISPOSITIONS
    assert set(PRESERVED_TABLES) == {"account_deletion_requests", "email_suppressions"}


def test_anonymize_is_an_update_not_a_skip(session, account_a, populated, monkeypatch):
    """**D18's trap**, exercised by a fixture table — as §6 Step 8 says it must be.

    *"Today the anonymize category is empty — it is exercised by a fixture table until SPEC-008
    supplies the first real one."*

    Without this the `ANONYMIZE` branch never runs, and deleting it entirely leaves every other
    test green: measured, by mutation. A declared-but-unexercised disposition is a comment.

    The distinction it protects is the one a reader gets wrong. `PRESERVE` **skips**, so the row
    keeps its `account_id` — using it for authored content retains personal data after an
    erasure request. `ANONYMIZE` is an UPDATE: the content survives for the people who rely on
    it, the author does not. `notes` stands in for SPEC-008's `VendorReview`.
    """
    # `updated_at`, not `content` — and the reason IS the criterion. The first version nulled
    # `content` and hit `NotNullViolation`, which is §5.4's warning arriving on schedule:
    # *"a NOT NULL column cannot be anonymized, and that is discovered at implementation time
    # if nobody says so first."* SPEC-008's VendorReview must declare its author columns
    # NULLABLE, and this is the empirical proof of why.
    monkeypatch.setitem(ANONYMIZED_TABLES, "notes", ("updated_at",))

    notes = Base.metadata.tables["notes"]

    # **Set it non-NULL first, or the assertion cannot fail.** The seeder leaves `updated_at`
    # NULL (no column default), so "is None afterwards" was true beforehand too — measured by
    # mutation: disabling the whole ANONYMIZE branch left this test green. A column that starts
    # at the value you are asserting proves nothing about what nulled it.
    session.execute(
        sa.update(notes)
        .where(notes.c.account_id == account_a)
        .values(updated_at=datetime.datetime(2026, 5, 5, 9, 0))
    )
    before = session.execute(
        sa.select(sa.func.count()).select_from(notes).where(notes.c.account_id == account_a)
    ).scalar()
    assert before >= 1, "the fixture must have seeded a note for this to mean anything"
    assert session.execute(
        sa.select(notes.c.updated_at).where(notes.c.account_id == account_a)
    ).scalars().first() is not None

    with account_context(account_a):
        request = request_deletion(session, account_a, populated)
        manifest = purge(session, request)
        session.flush()

    assert manifest["notes"]["disposition"] == ANONYMIZE

    rows = session.execute(
        sa.select(notes.c.updated_at, notes.c.content).where(
            notes.c.account_id == account_a
        )
    ).all()

    # **Survived** — an anonymize that deleted would satisfy "no personal data" and destroy a
    # record other people rely on.
    assert len(rows) == before, "anonymize must not delete the row"
    # …with the nominated column nulled. A skip would leave it populated, which is the failure
    # mode PRESERVE-for-authored-content produces.
    assert all(nulled is None for nulled, _ in rows)
    # And the content — what other people rely on — is untouched.
    assert all(content is not None for _, content in rows)


def test_no_dangling_global_refs(session, account_a, populated):
    """**A29b** — no account-referencing column on a global table still points at the account.

    **The set is empty today, which is exactly why this is derived.** §5.4: *"There are none
    today; SPEC-008 adds the first."* An assertion over an empty literal would pass forever
    without a mechanism behind it — a criterion that cannot fail.

    So the columns are discovered from `GLOBAL_TABLES` at test time, and `purge` nulls whatever
    that discovery returns. This asserts both halves: the discovery runs over the real global
    tables, and the purge consumes its output.
    """
    columns = account_referencing_global_columns()

    # The mechanism exists and looked at every global table.
    assert isinstance(columns, list)
    assert GLOBAL_TABLES, "the sweep must have something to sweep"

    with account_context(account_a):
        request = request_deletion(session, account_a, populated)
        manifest = purge(session, request)
        session.flush()

    for table_name, column_name in columns:
        assert f"{table_name}.{column_name}" in manifest, (
            f"{table_name}.{column_name} references an account and the purge did not null it"
        )

    # And the webhook ledger's `account_id` is excluded on purpose: it must outlive the
    # account, or a replayed event for a deleted account is processed as if new (SPEC-004 B7).
    assert ("processed_webhook_events", "account_id") not in columns


def test_storage_before_rows(session, account_a, populated, monkeypatch):
    """**A10** — storage objects are deleted before their rows.

    Asserted by making the row deletion fail *after* storage succeeded, then checking the
    storage call happened anyway. Ordering checked by call sequence alone would pass with both
    operations in one try block, where a mid-purge failure could still leave orphans.

    The direction matters: rows pointing at deleted files are findable and fixable. Orphaned
    objects in a bucket are referenced by nothing and enumerable by no query.
    """
    calls = []

    class RecordingStorage:
        def delete(self, key):
            calls.append(key)

        def url(self, key, *, expires_in=900):
            return None

    monkeypatch.setattr(
        "mihomes.storage.get_storage", lambda *a, **k: RecordingStorage()
    )

    original_execute = session.execute
    state = {"storage_done": False}

    def failing_execute(statement, *args, **kwargs):
        # Fail on the first DELETE against a tenant table, which is after `_delete_storage_objects`.
        if getattr(statement, "is_delete", False):
            state["storage_done"] = True
            raise RuntimeError("row deletion failed midway")
        return original_execute(statement, *args, **kwargs)

    with account_context(account_a):
        request = request_deletion(session, account_a, populated)
        session.flush()
        monkeypatch.setattr(session, "execute", failing_execute)
        with pytest.raises(RuntimeError, match="row deletion failed"):
            purge(session, request)

    assert state["storage_done"], "the purge never reached a row deletion"
    # Storage ran first, so a mid-purge failure leaves rows pointing at deleted files —
    # findable — rather than objects nothing points at.
    assert calls, "storage objects must be deleted BEFORE any row is deleted"
