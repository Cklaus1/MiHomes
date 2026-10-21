"""The vendor modal's Work orders tab must respect the viewer's property scope.

`/vendors/` lists each vendor's work orders (UI/UX pass). A staff member scoped to one property
may see the vendor, but not work orders the same vendor did at a property they are not scoped to.
The control — the in-scope work order — guards against a fix that simply hides everything.
"""

from __future__ import annotations

import uuid

from mihomes.models.property import Property
from mihomes.models.vendor import Vendor
from mihomes.models.work_order import WorkOrder


def test_staff_see_only_in_scope_work_orders_on_vendor_page(web_client_as):
    created = {}

    def _seed(session):
        mine = Property(id=uuid.uuid4(), name="Mine", slug=f"mine-{uuid.uuid4().hex[:6]}")
        other = Property(id=uuid.uuid4(), name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
        session.add_all([mine, other])
        session.flush()
        vendor = Vendor(id=uuid.uuid4(), company_name="Shared Vendor", slug=f"shared-{uuid.uuid4().hex[:6]}")
        session.add(vendor)
        session.flush()
        for prop, title in ((mine, "In-scope pruning"), (other, "Out-of-scope roof job")):
            session.add(WorkOrder(id=uuid.uuid4(), title=title, slug=f"wo-{uuid.uuid4().hex[:8]}",
                                  property_id=prop.id, vendor_id=vendor.id))
        created["mine"] = mine.id

    web_client_as.seed(_seed)
    body = web_client_as("staff", scoped_to=[created["mine"]]).get("/vendors/").text

    assert "Shared Vendor" in body
    assert "In-scope pruning" in body
    assert "Out-of-scope roof job" not in body, "work order at an unscoped property leaked via /vendors/"
