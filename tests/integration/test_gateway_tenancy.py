"""G4.1 · §6 Step 4 — **A11, the phase's definition of done** (D11, N3).

> A gateway without tenancy does not fail closed — it fails into the *wrong account*. Every
> other criterion here can pass while A11 fails, and the symptom is a row appearing in a
> stranger's estate with a cheerful confirmation sent back to the person who caused it. Nobody
> is watching a screen when it happens.

Three construction decisions, each guarding a way this test could pass while the leak was live:

**1. The categories are enumerated from `REVIEW_SCHEMA`, not hand-listed.** §9 is explicit:
*"A hand-listed subset passes forever while the fourteenth branch leaks."* A fifteenth category
added without scoping must turn this red, and it only can if the list comes from the tree.

**2. The 14/15 split is asserted, not the count** (harness C9). Of the 15 enum members, exactly
14 write and `informational` writes nothing — correctly, it is the "nothing to do" category. An
A11 that walked all 15 asserting *"nothing appeared in B"* would have **one arm that is
vacuously true**, since `informational` writes nothing in either account. So the test records
which categories wrote and asserts the partition. A branch that silently stops writing then
fails here instead of quietly joining `informational`.

**3. Account B is POPULATED before anything is asserted about it.** *"Nothing appeared in B"* is
trivially true of an empty account. §9: *"A11 is meaningless without a second populated account,
so `account_b` gets one too."* B gets its own property and its own rows, and the assertion is
that B's counts are **unchanged**, never that they are zero.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from mihomes.services.gateways import review_common as rc
from mihomes.services.property import create_property
from mihomes.tenancy.context import account_context

#: Every table a `dispatch_items` branch can write. Counted in BOTH accounts around every
#: dispatch, so a leak into B shows up regardless of which branch caused it.
WATCHED_TABLES = (
    "issues",
    "tasks",
    "work_orders",
    "assets",
    "consumables",
    "transactions",
    "notes",
    "appointments",
    "books",
    "staff_pto_requests",
)


def schema_categories() -> list[str]:
    """The category enum, read from `REVIEW_SCHEMA` in the tree (§9's instruction).

    Not a literal list. The whole force of A11 is that a category added tomorrow without
    scoping turns this test red, and a transcribed list cannot do that.
    """
    return list(
        rc.REVIEW_SCHEMA["properties"]["items"]["items"]["properties"]["category"]["enum"]
    )


def _counts(session, account_id) -> dict[str, int]:
    """Row counts per watched table, for one account. Read with raw SQL and an explicit
    `account_id` filter rather than through the ORM: the scoped session would filter to the
    bound tenant, so an ORM read could never *see* a leak into the other account — the
    measurement would inherit the very protection it is trying to test."""
    out = {}
    for table in WATCHED_TABLES:
        out[table] = session.execute(
            text(f"SELECT count(*) FROM {table} WHERE account_id = :a"),  # noqa: S608
            {"a": account_id},
        ).scalar_one()
    return out


def _item_for(category: str, prop_slug: str, *, issue_ref: str = "") -> dict:
    """A minimally valid item for one category, with the fields its branch reads.

    **Every category must actually reach its branch**, or it lands in the "did nothing" bucket
    for want of a field rather than for want of a branch — and the vacuity guard below would
    then be measuring the fixture instead of the dispatcher. Measured, not assumed: an earlier
    draft omitted `pto_request`'s staff member and `note_addition`'s `entity_ref`, and both
    silently joined `informational`.
    """
    base = {
        "category": category,
        "title": f"A11 {category} {uuid.uuid4().hex[:6]}",
        "description": "created by the tenancy enumeration test",
        "property_slug": prop_slug,
        "severity": "medium",
    }
    extras = {
        "supply_need": {"quantity_in_stock": 3},
        "expense_log": {"amount": 42.5},
        "pto_request": {"reported_by": "A11 Staffer", "pto_dates": ["2026-09-01"]},
        "asset_addition": {"related_asset": "Boiler"},
        "book_addition": {"title": f"A11 Book {uuid.uuid4().hex[:6]}"},
        "appointment_request": {"timestamp": "2026-09-01T10:00:00Z"},
        "task_completion": {"assigned_to": "A11 Staffer"},
        "issue_resolution": {"title": "A11 issue"},
        # `add_note` needs a target entity; without `entity_ref` the branch `continue`s.
        "note_addition": {"entity_type": "issue", "entity_ref": issue_ref},
    }
    base.update(extras.get(category, {}))
    return base


@pytest.fixture
def populated_b(_pg_engine, account_b):
    """Account B, with a property and one row in every watched table.

    This is the fixture that makes A11 mean something. Without it the test asserts "B is still
    empty", which an implementation that writes everything into A satisfies perfectly — and so
    does one that writes nothing anywhere.
    """
    from sqlalchemy.orm import sessionmaker

    connection = _pg_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(
        bind=connection, future=True, join_transaction_mode="create_savepoint"
    )
    sess = Session()
    with account_context(account_b):
        prop = create_property(sess, "B Estate")
        sess.flush()
        # Through the services, not the models directly: `slug` is NOT NULL and is generated
        # by the service layer, so a bare `Issue(...)` fails the constraint.
        from mihomes.services.issue import create_issue
        from mihomes.services.task import create_task

        create_issue(sess, "B's own issue", prop.slug)
        create_task(sess, "B's own task", prop.slug)
        sess.flush()
        sess.commit()
    sess.close()
    transaction.commit()
    connection.close()

    yield account_b

    # Everything committed above must be removed, `audit_log` included — the service layer
    # writes an audit row per create, and those are what leaked past an earlier version of this
    # fixture and made `test_archive.py::test_counts_eligible_rows` see 5 rows instead of 2.
    cleanup = _pg_engine.connect()
    tx = cleanup.begin()
    for table in ("issues", "tasks", "audit_log", "properties"):
        cleanup.execute(
            text(f"DELETE FROM {table} WHERE account_id = :a"),  # noqa: S608
            {"a": account_b},
        )
    tx.commit()
    cleanup.close()


@pytest.fixture
def _cleanup_account_a(_pg_engine, account_a):
    """Delete everything this test commits into account A, afterwards.

    The `session` fixture normally makes cleanup unnecessary — it rolls back an outer
    transaction — but this test *commits* on purpose, so its rows escape that rollback and
    persist for the rest of the run. Measured: without this,
    `test_archive.py::TestGetStats::test_counts_eligible_rows` fails with `assert 5 == 2`,
    because the dispatcher's `audit_log` writes are still there.

    Ordered children-before-parents; `properties` last, since most rows reference it.
    """
    yield
    conn = _pg_engine.connect()
    tx = conn.begin()
    for table in (*WATCHED_TABLES, "audit_log", "staff", "properties"):
        conn.execute(
            text(f"DELETE FROM {table} WHERE account_id = :a"),  # noqa: S608
            {"a": account_a},
        )
    tx.commit()
    conn.close()


def test_cross_account_isolation(
    session, account_a, populated_b, monkeypatch, _cleanup_account_a
):
    """**A11** — a message from account A creates rows in A only; B sees nothing.

    Walks every category in `REVIEW_SCHEMA` under account A's session and asserts two different
    things on two different axes, because they are two different claims:

    * **Isolation** — measured on raw row counts in B, around every single dispatch.
    * **The 14/15 split** — measured on `dispatch_items`' own return value, *not* on row counts.
      This distinction was found by measurement rather than reasoning: `question`,
      `task_completion` and `issue_resolution` all act without inserting into any watched table
      (one replies, two UPDATE existing rows), so a row-count reading of "handled" would put
      three working branches in the same bucket as `informational` — and the fix would have
      been to hand-list the exceptions, which is exactly the subset §9 forbids.
      `logged + replied + errors > 0` is C9's "handled" definition exactly.

    `ai_response` is stubbed. Not for speed: it fails without an API key, and its `except`
    branch calls `session.rollback()` (H27), which discards **every prior category's writes in
    the same batch**. That is a real defect and it is recorded in `opportunities.md` rather than
    fixed here — it is not a tenancy bug and A11 should not grow to cover it.
    """
    monkeypatch.setattr(rc, "ai_response", lambda *a, **k: "A11 stub answer")

    account_b = populated_b
    categories = schema_categories()
    assert len(categories) == 15, (
        f"REVIEW_SCHEMA has {len(categories)} categories, not 15 — the split asserted below "
        "was measured against 15 (harness C9). Confirm which category changed, and why"
    )

    prop = create_property(session, "A Estate")
    session.flush()
    # Committed so the loop's own setup survives; several branches resolve the property by
    # slug on every item.
    #
    # **A commit escapes the `session` fixture's rollback**, so account A's rows would outlive
    # this test and pollute every later one sharing the database — measured: it made
    # `test_archive.py::test_counts_eligible_rows` see 5 audit rows instead of 2. The
    # `_cleanup_account_a` finalizer below is what keeps that contained; `populated_b` does the
    # same for B, and both are required because the commits here are deliberate.
    session.commit()

    # `note_addition` needs an entity to hang off, and a staff member must exist for
    # `pto_request` to resolve its reporter. Both were measured: without them the two
    # categories fall through for want of a *field*, not for want of a branch, and the vacuity
    # guard below would be measuring this fixture instead of the dispatcher.
    from mihomes.services.issue import create_issue
    from mihomes.services.staff import create_staff

    anchor = create_issue(session, "A11 anchor issue", prop.slug)
    create_staff(session, "A11 Staffer", role="housekeeper")
    session.flush()
    session.commit()

    # **Load-bearing, not a triviality.** This proves the raw-SQL read can actually SEE account
    # B's rows. If RLS or a scoping filter silently zeroed these reads, every "B is unchanged"
    # assertion below would compare 0 to 0 and pass through a live cross-tenant leak.
    before_b = _counts(session, account_b)
    assert sum(before_b.values()) > 0, (
        "account B reads as empty, so 'nothing appeared in B' proves nothing — either the "
        "populated_b fixture did not commit, or this read cannot see B's rows at all (§9)"
    )

    adapter = rc.GatewayAdapter(label="A11", send=lambda cid, text: None)
    acted: list[str] = []
    inert: list[str] = []

    for category in categories:
        b_before = _counts(session, account_b)

        result = rc.dispatch_items(
            session,
            [_item_for(category, prop.slug, issue_ref=anchor.slug)],
            account=account_a,
            adapter=adapter,
            reply_target="chat-a11",
            messages=[],
            property_slug=prop.slug,
            resolve_reporter=lambda item: None,
            sender_trusted=True,
        )
        session.flush()

        b_after = _counts(session, account_b)

        # --- THE assertion: B is untouched by A's message --------------------------------
        assert b_after == b_before, (
            f"category {category!r} dispatched under account A changed account B: "
            f"{ {k: (b_before[k], b_after[k]) for k in b_after if b_before[k] != b_after[k]} }. "
            "This is a cross-tenant write — the failure A11 exists to catch"
        )

        effect = result["logged"] + result["replied"] + len(result["errors"])
        (acted if effect else inert).append(category)

    # --- the 14/15 split, asserted rather than counted -----------------------------------
    assert inert == ["informational"], (
        "exactly one category — `informational` — is handled by no branch; it is the "
        "'nothing to do' case. A branch that has silently stopped acting joins it unnoticed "
        f"otherwise, which is the regression a count-of-branches test never sees. Inert: {inert}"
    )
    assert len(acted) == 14, f"expected 14 handled categories, got {len(acted)}: {acted}"

    # --- and B is unchanged overall ------------------------------------------------------
    assert _counts(session, account_b) == before_b


def test_dispatch_items_refuses_a_missing_account(session, account_a):
    """`account` is required and never defaulted (D11) — and the requirement has teeth.

    A keyword that is accepted and ignored would let A11 pass on a signature change that
    changed no behaviour, since tenancy is actually enforced by the session. So `dispatch_items`
    asserts the account it was handed agrees with the session's bound tenant, and refuses
    `None` outright.
    """
    adapter = rc.GatewayAdapter(label="A11", send=lambda cid, text: None)

    with pytest.raises(TypeError):
        rc.dispatch_items(
            session,
            [],
            adapter=adapter,
            reply_target="chat",
            messages=[],
            property_slug=None,
            resolve_reporter=lambda item: None,
        )

    with pytest.raises(rc.AccountMismatch):
        rc.dispatch_items(
            session,
            [],
            account=None,
            adapter=adapter,
            reply_target="chat",
            messages=[],
            property_slug=None,
            resolve_reporter=lambda item: None,
        )


def test_dispatch_items_refuses_an_account_the_session_is_not_bound_to(
    session, account_a, account_b
):
    """The ingress bug, caught at the chokepoint.

    The sender resolved to B and the session is bound to A. Every writing branch would land in
    A while the confirmation went back to B's sender, and nothing would look wrong to anyone —
    so this is refused rather than logged. A cross-tenant write is not a degraded mode to
    continue in.
    """
    adapter = rc.GatewayAdapter(label="A11", send=lambda cid, text: None)

    with pytest.raises(rc.AccountMismatch) as excinfo:
        rc.dispatch_items(
            session,
            [],
            account=account_b,  # session is bound to account_a
            adapter=adapter,
            reply_target="chat",
            messages=[],
            property_slug=None,
            resolve_reporter=lambda item: None,
        )

    assert str(account_b) in str(excinfo.value)


def test_the_category_enumeration_reads_the_tree(session):
    """Guard on the guard: the enumeration must not silently return nothing.

    Every assertion in `test_cross_account_isolation` is driven by `schema_categories()`. If
    the schema path ever changes shape, an empty list would make the loop pass by having
    nothing to do — the empty-set trap that a hand-written list is supposed to avoid, arriving
    through the back door.
    """
    categories = schema_categories()
    assert len(categories) == 15
    assert "informational" in categories
    assert "issue" in categories
    # And it really is read from the module, not a copy living in this file.
    assert json.dumps(rc.REVIEW_SCHEMA).count("informational") >= 1
