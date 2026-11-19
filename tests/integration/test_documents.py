"""G9 · §6 Step 9 — document visibility for staff (A14, D13), **now per-person (SPEC-004)**.

D13 closes a gap §9.3's carve-out leaves open: it names *"account-level vendors, budgets, account
settings"* — **not documents** (F2c). An account-level document (a contract, an insurance policy)
has no property to scope by, and no sentence anywhere resolves the case. Silent, with no rescuing
text, unlike F2b.

**The gate changed at SPEC-004 and the posture did not.** D13's original mechanism was one boolean
per document, `documents.staff_visible`: ticked, and every staff member in scope saw it. That could
not express "this document is for Ana and not for Marco", which an estate needs — so the gate is
now a per-person grant (`document_access`), and the flag is retained-but-unread.
`TestTheFlagNoLongerGrantsAnything` pins that, because "the column still exists" and "the column
still does something" are different claims.

Fail-closed is unchanged and is now structural rather than a default: there is no grant until
someone creates one, so a newly uploaded invoice is invisible to staff with no column default to
rely on.

**C11 — `Document` has no `property_id`.** Staff queries filter on the grant *and* property scope,
but the model is polymorphic (`entity_type`/`entity_id`) and carries no property column. The scope
is therefore resolved **through the parent**, and a document with `entity_id IS NULL` is
account-level and invisible to staff regardless of any grant.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.models.asset import Asset
from mihomes.models.document import Document, DocumentType
from mihomes.models.property import Property


@pytest.fixture
def documents(web_client_as):
    """Two properties, an asset in each, and a document on each asset.

    Plus an account-level document with no parent at all — C11's case.
    """
    created = {}

    def _seed(session):
        for name in ("Belle Estate", "Blue Room"):
            prop = Property(
                id=uuid.uuid4(), name=name,
                slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
            )
            session.add(prop)
            session.flush()
            asset = Asset(
                id=uuid.uuid4(), name=f"{name} boiler",
                slug=f"boiler-{uuid.uuid4().hex[:6]}",
                asset_type="APPLIANCE", property_id=prop.id,
            )
            session.add(asset)
            session.flush()
            doc = Document(
                id=uuid.uuid4(), title=f"{name} manual",
                slug=f"manual-{uuid.uuid4().hex[:6]}",
                file_path=f"/docs/{uuid.uuid4().hex}.pdf",
                document_type=DocumentType.MANUAL,
                entity_type="asset", entity_id=asset.id,
            )
            session.add(doc)
            session.flush()
            created[name] = {"property_id": prop.id, "doc_id": doc.id}

        orphan = Document(
            id=uuid.uuid4(), title="Account level policy",
            slug=f"policy-{uuid.uuid4().hex[:6]}",
            file_path=f"/docs/{uuid.uuid4().hex}.pdf",
            document_type=DocumentType.OTHER,
            entity_type=None, entity_id=None,
            staff_visible=True,  # ticked, and still must not be visible — C11
        )
        session.add(orphan)
        session.flush()
        created["orphan_title"] = "Account level policy"
        created["orphan_id"] = orphan.id

    web_client_as.seed(_seed)
    return created


def _set_visible(web_client_as, doc_id, value=True):
    """**Legacy helper, kept only to prove the flag no longer does anything.**

    `staff_visible` was D13's whole gate until SPEC-004 replaced it with per-person grants. It is
    still a column (nothing depended on it — it never had a setter outside tests like this one), and
    `query_scope._document_criteria` no longer reads it. `TestTheFlagNoLongerGrantsAnything` is what
    keeps that honest; every other test here now grants access properly via `_grant`.
    """
    from sqlalchemy import text

    web_client_as.connection.execute(
        text("UPDATE documents SET staff_visible = :v WHERE id = :id"),
        {"v": value, "id": doc_id},
    )


def _grant(web_client_as, doc_id, staff_id):
    """Insert a grant on the test's own connection, bypassing the route.

    Raw SQL rather than the service, for the reason the root conftest gives for `_make_account`:
    these rows are the *input* to the mechanism under test, so creating them must not depend on the
    route and matrix declaration that this file also exercises separately.
    """
    from sqlalchemy import text

    web_client_as.connection.execute(
        text(
            "INSERT INTO document_access (id, account_id, document_id, staff_id, created_at) "
            "VALUES (:i, (SELECT account_id FROM documents WHERE id = :d), :d, :s, now())"
        ),
        {"i": uuid.uuid4(), "d": doc_id, "s": staff_id},
    )


def _revoke(web_client_as, doc_id, staff_id):
    """Remove a grant on the test's connection — the mirror of `_grant`."""
    from sqlalchemy import text

    web_client_as.connection.execute(
        text(
            "DELETE FROM document_access WHERE document_id = :d AND staff_id = :s"
        ),
        {"d": doc_id, "s": staff_id},
    )


def _grant_count(web_client_as, doc_id, staff_id) -> int:
    """How many grant rows exist for this pair.

    Asserted as a *count* rather than a boolean so a duplicate is distinguishable from a single
    grant — idempotency is one of the claims this file makes, and `>= 1` would not check it.
    """
    from sqlalchemy import text

    return web_client_as.connection.execute(
        text(
            "SELECT COUNT(*) FROM document_access WHERE document_id = :d AND staff_id = :s"
        ),
        {"d": doc_id, "s": staff_id},
    ).scalar()


def _slug_of(web_client_as, doc_id) -> str:
    from sqlalchemy import text

    return web_client_as.connection.execute(
        text("SELECT slug FROM documents WHERE id = :d"), {"d": doc_id}
    ).scalar()


def _a_user(web_client_as) -> uuid.UUID:
    """A real `users` row, for tests that need *some* login rather than the client's own.

    `staff.user_id` has a real FK, so a made-up UUID is refused by the database — which is the
    constraint doing its job and was worth hitting once: an invented id would otherwise have made
    `_staff_row` produce a row that looks linked and matches no one.
    """
    from sqlalchemy import text

    user_id = uuid.uuid4()
    web_client_as.connection.execute(
        text(
            "INSERT INTO users (id, google_sub, email, created_at) "
            "VALUES (:i, :s, :e, now())"
        ),
        {"i": user_id, "s": f"sub-{user_id.hex[:10]}", "e": f"{user_id.hex[:8]}@example.com"},
    )
    return user_id


def _staff_row(web_client_as, account_id, user_id, name="Ana"):
    """A staff row linked to a signed-in user — the shape a grant can actually match.

    The link is `staff.user_id` (SPEC-003 U6a). A grant naming a staff row *without* one matches
    nothing, which `TestGrantsNeedALogin` asserts directly.

    `account_id` is threaded in as a parameter rather than resolved by a subquery. An earlier
    version of this helper wrote `(SELECT account_id FROM documents LIMIT 1)`, which is the exact
    mistake `_plant_legacy` made during U1: with two accounts in the fixture set, `LIMIT 1` can
    pick the wrong one and the test then passes or fails for reasons unrelated to its subject.
    """
    from sqlalchemy import text

    staff_id = uuid.uuid4()
    web_client_as.connection.execute(
        text(
            "INSERT INTO staff (id, account_id, name, slug, role, active, user_id, created_at) "
            "VALUES (:i, :a, :n, :sl, 'HOUSEKEEPER', true, :u, now())"
        ),
        {
            "i": staff_id,
            "a": account_id,
            "n": name,
            "sl": f"{name.lower()}-{uuid.uuid4().hex[:6]}",
            "u": user_id,
        },
    )
    return staff_id


class TestDefaultHidden:
    def test_default_hidden(self, web_client_as, documents):
        """A14 — a newly uploaded document is invisible to staff **until ticked**.

        The staff member is scoped to Belle and the document hangs off Belle's asset, so the
        *only* thing keeping it hidden is `staff_visible` defaulting to false. That is what makes
        this a test of D13 rather than of G7's property scoping.
        """
        client = web_client_as("staff", scoped_to=[documents["Belle Estate"]["property_id"]])
        body = client.get("/documents/").text

        assert "Belle Estate manual" not in body, (
            "a document must be invisible to staff until staff_visible is ticked (D13) — "
            "default false, fail closed"
        )

    def test_column_default_is_false_in_the_database(self, web_client_as, documents):
        """Fail-closed at the schema, not only in the constructor.

        A Python-side default would leave rows inserted by raw SQL, a migration, or an importer
        silently visible. D13's "default false" has to be a server default to mean anything.
        """
        from sqlalchemy import text

        row = web_client_as.connection.execute(
            text(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'documents' AND column_name = 'staff_visible'"
            )
        ).one()
        assert row.is_nullable == "NO"
        assert "false" in (row.column_default or "").lower()


class TestGrantedDocuments:
    """SPEC-004 — a per-person grant is the gate, replacing D13's one-boolean `staff_visible`.

    **This class was `TestTickedDocuments` and its subject changed, not just its mechanics.** The
    flag said "every staff member in scope may see this"; the grant says "*this person* may". The
    two conditions that must both hold are otherwise unchanged: the grant and the property scope.
    """

    def test_granted_and_in_scope_is_visible(self, web_client_as, documents, account_a):
        """The positive control: granting works.

        Without this, a filter that hid *every* document from staff would pass A14 perfectly and
        make the feature useless — the housekeeper would never see the appliance manual.
        """
        belle = documents["Belle Estate"]
        client = web_client_as("staff", scoped_to=[belle["property_id"]])
        staff_id = _staff_row(web_client_as, account_a, client.user_id)
        _grant(web_client_as, belle["doc_id"], staff_id)

        assert "Belle Estate manual" in client.get("/documents/").text

    def test_granted_but_out_of_scope_stays_hidden(self, web_client_as, documents, account_a):
        """**Both conditions, not either** — the grant *and* property scope.

        This is the test that catches an implementation filtering on the grant alone: Blue Room's
        manual is granted to this person, and a staff member scoped only to Belle must still not
        see it. A grant is not an escape hatch from G7.
        """
        blue = documents["Blue Room"]
        client = web_client_as("staff", scoped_to=[documents["Belle Estate"]["property_id"]])
        staff_id = _staff_row(web_client_as, account_a, client.user_id)
        _grant(web_client_as, blue["doc_id"], staff_id)

        body = client.get("/documents/").text

        assert "Blue Room manual" not in body, (
            "a grant must not override property scope — the two are ANDed"
        )

    def test_account_level_document_is_never_visible_to_staff(
        self, web_client_as, documents, account_a
    ):
        """C11 — `entity_id IS NULL` means there is no property to scope by.

        Granted *and* seeded with the legacy `staff_visible=True`, so neither gate alone would
        keep it hidden. Fail closed: an account-level document has no parent whose scope could
        authorise it, and the posture is that a document is hidden unless something positively
        authorises it.
        """
        client = web_client_as("staff", scoped_to=[documents["Belle Estate"]["property_id"]])
        staff_id = _staff_row(web_client_as, account_a, client.user_id)
        _grant(web_client_as, documents["orphan_id"], staff_id)

        assert documents["orphan_title"] not in client.get("/documents/").text


class TestTheFlagNoLongerGrantsAnything:
    """`documents.staff_visible` is retained but unread — asserted, not assumed.

    SPEC-004 replaced it: one boolean per document meant a ticked document was visible to *every*
    staff member in scope, and an estate has paperwork appropriate for one person and not another.
    Requiring both the flag and a grant would make the owner set two controls to express one
    intention, with a silent empty page whenever they set only one.

    The column stays because nothing depends on it — it never had a setter outside tests — and
    dropping it would trade a reversible decision for an irreversible one. But "retained" and
    "still doing something" are different claims, and only a test can tell them apart.
    """

    def test_ticking_the_flag_alone_grants_nothing(self, web_client_as, documents):
        belle = documents["Belle Estate"]
        _set_visible(web_client_as, belle["doc_id"])

        client = web_client_as("staff", scoped_to=[belle["property_id"]])
        assert "Belle Estate manual" not in client.get("/documents/").text, (
            "staff_visible still grants access. SPEC-004 replaced it with per-person grants; if "
            "the flag is being read again, the owner now has two controls for one intention."
        )

    def test_a_grant_works_with_the_flag_false(self, web_client_as, documents, account_a):
        """The converse, so the pair pins 'the grant is the whole gate' from both sides."""
        belle = documents["Belle Estate"]
        _set_visible(web_client_as, belle["doc_id"], value=False)

        client = web_client_as("staff", scoped_to=[belle["property_id"]])
        staff_id = _staff_row(web_client_as, account_a, client.user_id)
        _grant(web_client_as, belle["doc_id"], staff_id)

        assert "Belle Estate manual" in client.get("/documents/").text


class TestUnattendedPathsAreDenied:
    """A staff role with no signed-in user sees no documents — **and this class exists because
    mutation testing found the arm untested.**

    Flipping `_document_criteria`'s `if user_id is None: granted = false()` to `true()` turned no
    test red. Probing directly showed the branch working correctly, so it was real but uncovered —
    the same diagnosis, and the same fix, as U7's no-linkage `false()` branch. An untested deny is
    one refactor away from an allow nobody notices.

    The branch matters because the paths that reach it are the unattended ones: the CLI, background
    jobs, the Telegram bot. D16 makes an unlinked sender staff-level, so a bot message from an
    unrecognised number arrives with a staff role and no user — precisely the case where a
    permissive default would be least likely to be observed.
    """

    def test_a_staff_role_with_no_user_sees_nothing(self, web_client_as, documents, account_a):
        from mihomes.authz.scope import authz_context
        from mihomes.models.document import Document as D

        belle = documents["Belle Estate"]
        # Granted to a real person, so the *only* thing withholding it is the absent user binding.
        staff_id = _staff_row(
            web_client_as, account_a, _a_user(web_client_as), name="Hal"
        )
        _grant(web_client_as, belle["doc_id"], staff_id)

        session = web_client_as.session_for_scope()
        with authz_context("staff", frozenset({belle["property_id"]})):
            titles = [d.title for d in session.query(D).all()]

        assert titles == [], (
            "a staff-role request with no signed-in user received documents. There is no identity "
            "to match a grant against, so the fail-closed answer is none."
        )


class TestGrantsNeedALogin:
    """A grant to a staff row with no `user_id` matches nothing — the reason the picker filters.

    The criteria resolves the request's user to a staff row through `staff.user_id` (U6a), so a
    person with no MiHomes login cannot be the subject of any request. `document_svc.grantable_staff`
    excludes them and `grant_access` refuses them, because a control that silently does nothing is
    worse than one that is absent: the owner ticks a box, sees no error, and reasonably concludes
    the person has access.
    """

    def test_a_grant_to_a_loginless_staff_row_grants_nothing(
        self, web_client_as, documents, account_a
    ):
        from sqlalchemy import text

        belle = documents["Belle Estate"]
        client = web_client_as("staff", scoped_to=[belle["property_id"]])

        loginless = uuid.uuid4()
        web_client_as.connection.execute(
            text(
                "INSERT INTO staff (id, account_id, name, slug, role, active, created_at) "
                "VALUES (:i, :a, 'Marco', :sl, 'CHEF', true, now())"
            ),
            {"i": loginless, "a": account_a, "sl": f"marco-{uuid.uuid4().hex[:6]}"},
        )
        _grant(web_client_as, belle["doc_id"], loginless)

        assert "Belle Estate manual" not in client.get("/documents/").text

    def test_grantable_staff_excludes_them(self, web_client_as, documents, account_a):
        from sqlalchemy import text

        from mihomes.services import document as doc_svc

        client = web_client_as("owner")
        linked = _staff_row(web_client_as, account_a, client.user_id, name="Ana")
        web_client_as.connection.execute(
            text(
                "INSERT INTO staff (id, account_id, name, slug, role, active, created_at) "
                "VALUES (:i, :a, 'Marco', :sl, 'CHEF', true, now())"
            ),
            {"i": uuid.uuid4(), "a": account_a, "sl": f"marco-{uuid.uuid4().hex[:6]}"},
        )

        session = web_client_as.session_for_scope()
        names = {s.name for s in doc_svc.grantable_staff(session)}
        assert "Ana" in names, "a staff member with a login must be offerable"
        assert "Marco" not in names, (
            "the picker offered someone with no login — a grant to them would look active and "
            "authorise nothing"
        )
        assert linked is not None

    def test_the_service_refuses_to_grant_to_them(self, web_client_as, documents, account_a):
        from sqlalchemy import text

        from mihomes.services import document as doc_svc

        web_client_as("owner")
        loginless = uuid.uuid4()
        web_client_as.connection.execute(
            text(
                "INSERT INTO staff (id, account_id, name, slug, role, active, created_at) "
                "VALUES (:i, :a, 'Marco', :sl, 'CHEF', true, now())"
            ),
            {"i": loginless, "a": account_a, "sl": f"marco-{uuid.uuid4().hex[:6]}"},
        )

        session = web_client_as.session_for_scope()
        with pytest.raises(ValueError, match="no MiHomes login"):
            doc_svc.grant_access(session, str(documents["Belle Estate"]["doc_id"]), loginless)


class TestPrivilegedUnaffected:
    @pytest.mark.parametrize("role", ["owner", "admin"])
    def test_privileged_see_every_document(self, web_client_as, documents, role):
        """D13 is owner/admin controlled — the flag restricts staff, nobody else."""
        body = web_client_as(role).get("/documents/").text
        assert "Belle Estate manual" in body
        assert "Blue Room manual" in body
        assert documents["orphan_title"] in body


class TestGrantingIsOwnerAndAdminOnly:
    """The `document.grant` half — who may administer access, as opposed to who has it.

    Row 7 is split for this (SPEC-004, on the row-8 precedent): `inventory.manage` is `SCOPED` for
    staff and correctly so, since a housekeeper may read an appliance manual for a property they
    cover. Deciding *who else* sees it is a different power, and a three-valued cell cannot say
    "you may read these rows but not administer who reads them".
    """

    def test_the_owner_can_grant(self, web_client_as, documents, account_a):
        belle = documents["Belle Estate"]
        owner = web_client_as("owner")
        staff_id = _staff_row(web_client_as, account_a, _a_user(web_client_as), name="Ana")

        response = owner.post(
            f"/documents/{_slug_of(web_client_as, belle['doc_id'])}/access",
            data={"staff_id": str(staff_id)},
        )
        assert response.status_code == 200
        assert _grant_count(web_client_as, belle["doc_id"], staff_id) == 1

    def test_an_admin_can_grant(self, web_client_as, documents, account_a):
        """Admins are included on the owner's instruction — the same pairing as `member.manage`."""
        belle = documents["Belle Estate"]
        admin = web_client_as("admin")
        staff_id = _staff_row(web_client_as, account_a, _a_user(web_client_as), name="Bea")

        response = admin.post(
            f"/documents/{_slug_of(web_client_as, belle['doc_id'])}/access",
            data={"staff_id": str(staff_id)},
        )
        assert response.status_code == 200
        assert _grant_count(web_client_as, belle["doc_id"], staff_id) == 1

    def test_staff_cannot_grant(self, web_client_as, documents, account_a):
        """**The assertion the split exists for.** A staff member who could grant could grant
        themselves, and the whole control would be decorative."""
        belle = documents["Belle Estate"]
        client = web_client_as("staff", scoped_to=[belle["property_id"]])
        staff_id = _staff_row(web_client_as, account_a, client.user_id, name="Cass")

        response = client.post(
            f"/documents/{_slug_of(web_client_as, belle['doc_id'])}/access",
            data={"staff_id": str(staff_id)},
        )
        assert response.status_code == 403, (
            f"expected 403 from document.grant (DENY for staff), got {response.status_code}"
        )
        assert _grant_count(web_client_as, belle["doc_id"], staff_id) == 0, (
            "the request was refused but a grant row was written anyway"
        )

    def test_staff_cannot_revoke_either(self, web_client_as, documents, account_a):
        belle = documents["Belle Estate"]
        client = web_client_as("staff", scoped_to=[belle["property_id"]])
        staff_id = _staff_row(web_client_as, account_a, client.user_id, name="Dee")
        _grant(web_client_as, belle["doc_id"], staff_id)

        response = client.post(
            f"/documents/{_slug_of(web_client_as, belle['doc_id'])}/access/revoke",
            data={"staff_id": str(staff_id)},
        )
        assert response.status_code == 403
        assert _grant_count(web_client_as, belle["doc_id"], staff_id) == 1, (
            "a staff member revoked a grant despite the 403 — the refusal did not prevent the write"
        )

    def test_the_denial_comes_from_the_matrix(self):
        """Teeth: a 403 is only meaningful if it came from the grant we think it did."""
        from mihomes.authz.actions import MATRIX as M
        from mihomes.authz.actions import Access, Grant

        spec = M["document.grant"]
        assert spec.staff is Grant.DENY
        assert spec.owner is Grant.ALLOW
        assert spec.admin is Grant.ALLOW
        assert spec.access is Access.ACCOUNT


class TestGrantingIsIdempotent:
    def test_granting_twice_leaves_one_row(self, web_client_as, documents, account_a):
        """The caller is a checkbox; ticking an already-ticked box is not an error."""
        belle = documents["Belle Estate"]
        owner = web_client_as("owner")
        staff_id = _staff_row(web_client_as, account_a, _a_user(web_client_as), name="Eve")
        slug = _slug_of(web_client_as, belle["doc_id"])

        owner.post(f"/documents/{slug}/access", data={"staff_id": str(staff_id)})
        owner.post(f"/documents/{slug}/access", data={"staff_id": str(staff_id)})

        assert _grant_count(web_client_as, belle["doc_id"], staff_id) == 1

    def test_revoking_something_ungranted_is_not_an_error(
        self, web_client_as, documents, account_a
    ):
        belle = documents["Belle Estate"]
        owner = web_client_as("owner")
        staff_id = _staff_row(web_client_as, account_a, _a_user(web_client_as), name="Fay")

        response = owner.post(
            f"/documents/{_slug_of(web_client_as, belle['doc_id'])}/access/revoke",
            data={"staff_id": str(staff_id)},
        )
        assert response.status_code == 200


class TestRevokingTakesEffect:
    def test_a_revoked_document_disappears_again(self, web_client_as, documents, account_a):
        """The round trip, which is what proves the grant is genuinely the gate rather than a
        one-way door that happens to be open."""
        belle = documents["Belle Estate"]
        client = web_client_as("staff", scoped_to=[belle["property_id"]])
        staff_id = _staff_row(web_client_as, account_a, client.user_id, name="Gus")

        _grant(web_client_as, belle["doc_id"], staff_id)
        assert "Belle Estate manual" in client.get("/documents/").text

        _revoke(web_client_as, belle["doc_id"], staff_id)
        assert "Belle Estate manual" not in client.get("/documents/").text
