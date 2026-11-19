"""U6a — `staff.user_id`, the link that makes §4.1's `PERSONNEL` rule enforceable.

*"Staff may see their own record; never others'"* needs a hard answer to **which row is mine**, and
before this there was none. `Staff.email` looks like one and is not: nullable (so NULL matches
NULL), non-unique, and frequently not the address a person signs in with. The only member→staff
resolution in the tree is `review_common.resolve_reporter_by_name`, a fuzzy `ILIKE` on the *name*
written for attributing inbound messages — a guess, by design.

So this file asserts the column's *shape*, because the shape is the security property:
`query_scope` filters `PERSONNEL` on `user_id` and nothing else, which makes the FK's nullability
and its delete behaviour load-bearing rather than incidental.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from mihomes.models.staff import Staff


class TestTheColumnShape:
    def test_user_id_is_a_uuid_column(self):
        col = Staff.__table__.c["user_id"]
        assert isinstance(col.type, PGUUID)

    def test_it_is_nullable(self):
        """Most staff have no login at all — a gardener, a contractor's crew.

        NOT NULL here would force a fake `users` row per staff member, and inventing logins to
        satisfy a constraint is how you end up with accounts nobody can account for.
        """
        assert Staff.__table__.c["user_id"].nullable is True

    def test_it_points_at_users_with_set_null(self):
        """`SET NULL`, not CASCADE — the direction of the reference is why.

        This FK runs from a **tenant** row at a **global** one, so CASCADE would delete an
        employment record because a person deleted their MiHomes login. `0006` made the same call
        in the opposite direction: nulling drops the association and keeps both rows.
        """
        fks = list(Staff.__table__.c["user_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"
        assert fks[0].ondelete == "SET NULL"

    def test_there_is_no_index_on_it(self):
        """Deliberately unindexed — SPEC-002 Step 3 rejects a tenant index not led by account_id.

        `index=True` would emit `ix_staff_user_id = (user_id)` and turn
        `test_composite_indexes_lead_with_account_id` red, and buying an `EXPECTED_NON_LEADING`
        exemption for an index nothing needs would spend that list's credibility for nothing. The
        lookup is one row by a signed-in user's id, over tens of rows per account.
        """
        indexed = {c.name for idx in Staff.__table__.indexes for c in idx.columns}
        assert "user_id" not in indexed


class TestDeleteBehaviourInPostgres:
    """Declared behaviour and *actual* behaviour are different claims. This asserts the second."""

    def test_deleting_the_user_nulls_the_link_and_keeps_the_staff_row(
        self, session, account_a
    ):
        user_id = uuid.uuid4()
        session.execute(
            text(
                "INSERT INTO users (id, google_sub, email, created_at) "
                "VALUES (:id, :sub, :email, now())"
            ),
            {"id": user_id, "sub": f"sub-{user_id.hex[:8]}", "email": "gardener@example.com"},
        )
        member = Staff(name="Ana", slug=f"ana-{uuid.uuid4().hex[:6]}", user_id=user_id)
        session.add(member)
        session.flush()
        staff_id = member.id

        session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        session.expire_all()

        survivor = session.get(Staff, staff_id)
        assert survivor is not None, (
            "the HR record was deleted along with the login — this is the CASCADE behaviour the "
            "migration deliberately avoided"
        )
        assert survivor.user_id is None


class TestTheEditFormCannotSetIt:
    """`safe_update` applies any key matching a real column, so `user_id` *is* settable.

    That matters because the column now decides who may read the row: a staff member able to set it
    on their own record could point it at a colleague's and read that instead. They cannot, and this
    pins the reason rather than trusting it — `edit_staff` builds its kwargs from named `Form(...)`
    parameters, so an extra POST field never reaches the service.
    """

    def test_the_edit_form_cannot_set_user_id(self):
        import inspect

        from mihomes.web.routes import staff as staff_routes

        params = inspect.signature(staff_routes.edit_staff).parameters
        assert "user_id" not in params, (
            "edit_staff now accepts user_id from the form. That column controls PERSONNEL read "
            "access — move the filtering into staff_svc.update_staff before allowing this."
        )

        source = inspect.getsource(staff_routes.edit_staff)
        assert "**" not in source.split("kwargs = ")[0], (
            "edit_staff appears to forward arbitrary form data. It must build kwargs from an "
            "explicit parameter list, or user_id becomes settable over HTTP."
        )

    def test_the_service_still_accepts_it_from_trusted_callers(self, session, account_a):
        """The other half: the CLI and onboarding legitimately need to set this."""
        from mihomes.services import staff as staff_svc

        user_id = uuid.uuid4()
        session.execute(
            text(
                "INSERT INTO users (id, google_sub, email, created_at) "
                "VALUES (:id, :sub, :email, now())"
            ),
            {"id": user_id, "sub": f"sub-{user_id.hex[:8]}", "email": "chef@example.com"},
        )
        member = staff_svc.create_staff(session, "Marco", user_id=user_id)
        assert member.user_id == user_id
