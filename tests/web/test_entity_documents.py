"""Documents tab on staff and vendor records.

Rebuilt from telegram-bot's `6d077ae`, which wrote uploads to `/static/uploads/` — the public,
cross-tenant path the SaaS line removed. These routes go through `read_document_upload` (tenant-
prefixed storage key) like the asset/issue/work-order Docs tabs, and the delete route refuses a
document that belongs to a different record than the one in the URL.
"""

from __future__ import annotations

import pytest

from mihomes.services import document as doc_svc
from mihomes.services import staff as staff_svc
from mihomes.services import vendor as vendor_svc
from mihomes.storage import get_storage, reset_storage


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
    reset_storage()
    provider = get_storage(refresh=True, override_root=tmp_path / "objects")
    yield provider
    reset_storage()


def _vendor_slug(client) -> str:
    with client._SessionLocal() as s:
        return vendor_svc.list_vendors(s)[0].slug


def _staff_slug(client) -> str:
    with client._SessionLocal() as s:
        return staff_svc.list_staff(s)[0].slug


@pytest.mark.parametrize("kind", ["vendors", "staff"])
def test_upload_lands_in_tenant_storage_and_lists_on_the_record(client, storage, kind):
    slug = _vendor_slug(client) if kind == "vendors" else _staff_slug(client)
    resp = client.post(
        f"/{kind}/{slug}/documents",
        data={"title": "Signed agreement", "doc_type": "contract"},
        files={"file": ("agreement.pdf", b"%PDF-1.4 signed", "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    assert "Signed agreement" in resp.text
    assert "/static/uploads/" not in resp.text
    assert 'data-preview-fp="/documents/file/' in resp.text

    page = client.get(f"/{kind}/")
    assert "Documents (1)" in page.text


@pytest.mark.parametrize("kind", ["vendors", "staff"])
def test_disallowed_file_is_refused(client, storage, kind):
    slug = _vendor_slug(client) if kind == "vendors" else _staff_slug(client)
    resp = client.post(
        f"/{kind}/{slug}/documents",
        data={"title": "Sneaky", "doc_type": "other"},
        files={"file": ("logo.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
    )
    assert resp.status_code == 200
    assert "isn’t an allowed document type" in resp.text
    assert "Sneaky" not in resp.text


def test_delete_refuses_a_document_from_another_record(client, storage):
    vendor_slug = _vendor_slug(client)
    staff_slug = _staff_slug(client)
    client.post(
        f"/staff/{staff_slug}/documents",
        data={"title": "Passport copy", "doc_type": "other"},
        files={"file": ("passport.pdf", b"%PDF-1.4 id", "application/pdf")},
    )
    with client._SessionLocal() as s:
        doc = doc_svc.list_documents(s, entity_type="staff")[0]
        doc_id = str(doc.id)

    # A vendor URL must not reach a staff member's document.
    resp = client.delete(f"/vendors/{vendor_slug}/documents/{doc_id}")
    assert resp.status_code == 404
    with client._SessionLocal() as s:
        assert len(doc_svc.list_documents(s, entity_type="staff")) == 1

    resp = client.delete(f"/staff/{staff_slug}/documents/{doc_id}")
    assert resp.status_code == 200
    with client._SessionLocal() as s:
        assert doc_svc.list_documents(s, entity_type="staff") == []


def test_issue_photos_tab_upload_works(client, storage):
    """`"issue"` was missing from VALID_ENTITY_TYPES, so this upload raised on create_document."""
    from mihomes.services import issue as issue_svc
    from mihomes.services import property as prop_svc

    with client._SessionLocal() as s:
        prop = prop_svc.list_properties(s)[0]
        slug = issue_svc.create_issue(s, "Cracked tile", prop.slug).slug
        s.commit()
    resp = client.post(
        f"/issues/{slug}/documents",
        data={"title": "Tile photo", "doc_type": "photo"},
        files={"file": ("tile.png", b"\x89PNG fake", "image/png")},
    )
    assert resp.status_code == 200, resp.text
    assert "Tile photo" in resp.text
    with client._SessionLocal() as s:
        assert len(doc_svc.list_documents(s, entity_type="issue")) == 1
