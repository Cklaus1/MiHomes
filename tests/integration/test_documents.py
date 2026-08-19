"""G9 · §6 Step 9 — `documents.staff_visible` (A14, D13).

D13 closes a gap §9.3's carve-out leaves open: it names *"account-level vendors, budgets, account
settings"* — **not documents** (F2c). An account-level document (a contract, an insurance policy)
has no property to scope by, and no sentence anywhere resolves the case. Silent, with no rescuing
text, unlike F2b.

**Default `false`, fail closed:** *"a housekeeper sees an appliance manual once it is ticked; a
newly uploaded invoice is never exposed by default."*

**C11 — `Document` has no `property_id`.** Step 9 says staff queries filter on `staff_visible`
*and* property scope, but the model is polymorphic (`entity_type`/`entity_id`) and carries no
property column. The scope is therefore resolved **through the parent**, and a document with
`entity_id IS NULL` is account-level and invisible to staff regardless of the flag.
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
        created["orphan_title"] = "Account level policy"

    web_client_as.seed(_seed)
    return created


def _set_visible(web_client_as, doc_id, value=True):
    from sqlalchemy import text

    web_client_as.connection.execute(
        text("UPDATE documents SET staff_visible = :v WHERE id = :id"),
        {"v": value, "id": doc_id},
    )


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


class TestTickedDocuments:
    def test_ticked_and_in_scope_is_visible(self, web_client_as, documents):
        """The positive control: ticking it works.

        Without this, a filter that hid *every* document from staff would pass A14 perfectly and
        make the feature useless — the housekeeper would never see the appliance manual.
        """
        belle = documents["Belle Estate"]
        _set_visible(web_client_as, belle["doc_id"])

        client = web_client_as("staff", scoped_to=[belle["property_id"]])
        assert "Belle Estate manual" in client.get("/documents/").text

    def test_ticked_but_out_of_scope_stays_hidden(self, web_client_as, documents):
        """**Both conditions, not either** — Step 9 says `staff_visible` *and* property scope.

        This is the test that catches an implementation which filters on the flag alone: Blue
        Room's manual is ticked, and a staff member scoped only to Belle must still not see it.
        """
        blue = documents["Blue Room"]
        _set_visible(web_client_as, blue["doc_id"])

        client = web_client_as("staff", scoped_to=[documents["Belle Estate"]["property_id"]])
        body = client.get("/documents/").text

        assert "Blue Room manual" not in body, (
            "ticking staff_visible must not override property scope — the two are ANDed"
        )

    def test_account_level_document_is_never_visible_to_staff(self, web_client_as, documents):
        """C11 — `entity_id IS NULL` means there is no property to scope by.

        The document is deliberately seeded with `staff_visible=True`, so the flag alone would
        expose it. Fail closed: an account-level document has no parent whose scope could
        authorise it, and D13's whole posture is that a document is hidden unless something
        positively authorises it.
        """
        client = web_client_as("staff", scoped_to=[documents["Belle Estate"]["property_id"]])
        assert documents["orphan_title"] not in client.get("/documents/").text


class TestPrivilegedUnaffected:
    @pytest.mark.parametrize("role", ["owner", "admin"])
    def test_privileged_see_every_document(self, web_client_as, documents, role):
        """D13 is owner/admin controlled — the flag restricts staff, nobody else."""
        body = web_client_as(role).get("/documents/").text
        assert "Belle Estate manual" in body
        assert "Blue Room manual" in body
        assert documents["orphan_title"] in body
