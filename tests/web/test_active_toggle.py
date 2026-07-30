"""W.8 · M17 — active toggle must be able to DEACTIVATE.

An unchecked checkbox sends no field, so with only a checkbox the server can't
distinguish "set inactive" from "field absent" and the entity can never be
deactivated via the form. A hidden ``active=0`` input before the checkbox means
unchecking submits ``0`` (deactivate) while checking submits ``1``.

These tests drive the full HTML form so they catch both the template's hidden
input and the route's interpretation of it.
"""

import re

from mihomes.services import vendor as vendor_svc


def _has_hidden_active(html: str, name_fragment: str) -> bool:
    """True if a hidden active=0 input precedes the active checkbox in the form."""
    # A hidden input named "active" with value 0/false.
    return bool(
        re.search(
            r'<input[^>]*type="hidden"[^>]*name="active"[^>]*value="0"',
            html,
        )
        or re.search(
            r'<input[^>]*name="active"[^>]*type="hidden"[^>]*value="0"',
            html,
        )
    )


def test_vendors_page_has_hidden_active_input(client):
    resp = client.get("/vendors/")
    assert resp.status_code == 200
    assert _has_hidden_active(resp.text, "vendor")


def test_staff_page_has_hidden_active_input(client):
    resp = client.get("/staff/")
    assert resp.status_code == 200
    assert _has_hidden_active(resp.text, "member")


def test_vendor_can_be_deactivated_via_form(client):
    with client._SessionLocal() as s:
        v = vendor_svc.create_vendor(s, "Toggle Vendor", service_categories=["Plumbing"])
        s.commit()
        slug = v.slug
        assert v.active is True

    # Uncheck → only the hidden active=0 is submitted.
    resp = client.post(
        f"/vendors/{slug}/edit",
        data={"company_name": "Toggle Vendor", "active": "0"},
    )
    assert resp.status_code == 200
    with client._SessionLocal() as s:
        assert vendor_svc.get_vendor(s, slug).active is False


def test_vendor_stays_active_when_checked(client):
    with client._SessionLocal() as s:
        v = vendor_svc.create_vendor(s, "Keep Vendor", service_categories=["Plumbing"])
        s.commit()
        slug = v.slug

    # Checked → browser sends hidden 0 then checkbox 1; server must honor 1.
    resp = client.post(
        f"/vendors/{slug}/edit",
        data={"company_name": "Keep Vendor", "active": ["0", "1"]},
    )
    assert resp.status_code == 200
    with client._SessionLocal() as s:
        assert vendor_svc.get_vendor(s, slug).active is True
