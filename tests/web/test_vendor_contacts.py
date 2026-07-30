"""W.6 · H32 — vendor contact rows must be zipped strictly.

The edit form submits four parallel arrays (c_name/c_role/c_phone/c_email). A
plain ``zip`` silently truncates to the shortest, so a dropped middle field would
pair the wrong name with the wrong phone/email — silent data corruption. Using
``zip(strict=True)`` turns a length mismatch into a ValueError that the route
surfaces as a form error instead of persisting scrambled contacts.
"""

from mihomes.services import vendor as vendor_svc


def _make_vendor(client):
    with client._SessionLocal() as s:
        v = vendor_svc.create_vendor(s, "Contact Test Vendor", service_categories=["Plumbing"])
        s.commit()
        return v.slug


def test_aligned_contacts_saved(client):
    slug = _make_vendor(client)
    resp = client.post(
        f"/vendors/{slug}/edit",
        data={
            "company_name": "Contact Test Vendor",
            "c_name": ["Alice", "Bob"],
            "c_role": ["Owner", "Tech"],
            "c_phone": ["111", "222"],
            "c_email": ["a@x.com", "b@x.com"],
        },
    )
    assert resp.status_code == 200
    with client._SessionLocal() as s:
        v = vendor_svc.get_vendor(s, slug)
        contacts = {c["name"]: c for c in (v.contacts or [])}
        assert contacts["Alice"]["phone"] == "111"
        assert contacts["Bob"]["email"] == "b@x.com"


def test_mismatched_contact_arrays_surface_form_error(client):
    slug = _make_vendor(client)
    # Two names but only one phone → misalignment. Must NOT silently truncate.
    resp = client.post(
        f"/vendors/{slug}/edit",
        data={
            "company_name": "Contact Test Vendor",
            "c_name": ["Alice", "Bob"],
            "c_role": ["Owner", "Tech"],
            "c_phone": ["111"],
            "c_email": ["a@x.com", "b@x.com"],
        },
    )
    # Surfaced as a user-facing 400, not a 500 and not a silent success.
    assert resp.status_code == 400
    # And the scrambled contacts must not have been persisted.
    with client._SessionLocal() as s:
        v = vendor_svc.get_vendor(s, slug)
        assert not v.contacts
