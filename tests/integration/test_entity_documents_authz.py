"""Staff/vendor Documents routes are management actions — staff are refused before the body runs.

`member.manage` and `vendor.manage` are DENY for the staff role, so a staff member cannot attach
files to (or delete files from) a colleague's record or a vendor, whatever the slug.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.parametrize("prefix", ["/staff/someone", "/vendors/some-vendor"])
def test_staff_role_cannot_add_or_delete_documents(web_client_as, prefix):
    client = web_client_as("staff", scoped_to=[])
    resp = client.post(
        f"{prefix}/documents",
        data={"title": "x", "doc_type": "other"},
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert resp.status_code == 403
    assert client.delete(f"{prefix}/documents/{uuid.uuid4()}").status_code == 403
