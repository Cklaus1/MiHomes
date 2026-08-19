"""G8 · §6 Step 8 — redaction **applied**, not merely available (A12, A13).

F4 is the finding this closes: *"money lives inside rows staff are permitted to see."* G7 decided
**whether** staff get the row; this decides **which columns**. Matrix rows 6/7 grant staff scoped
work orders and assets, row 9 denies finances, and they collide on the same records — a
housekeeper who may see a work order can see its cost.

**These are end-to-end tests on purpose.** `tests/unit/test_redaction.py` already proves
`redact_for_role` strips the right fields; what it cannot prove is that anything *calls* it. A
phase can pass every unit test in this area while the rendered page still shows the money, which
is precisely the "leak wearing the feature's clothes" the spec warns about.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.models.property import Property
from mihomes.models.vendor import Vendor
from mihomes.models.work_order import WorkOrder


@pytest.fixture
def estate(web_client_as):
    """One property, one vendor, and a work order carrying both costs."""
    created = {}

    def _seed(session):
        prop = Property(
            id=uuid.uuid4(), name="Belle Estate", slug=f"belle-{uuid.uuid4().hex[:6]}"
        )
        session.add(prop)
        session.flush()

        vendor = Vendor(
            id=uuid.uuid4(), company_name="Orkin Pest", slug=f"orkin-{uuid.uuid4().hex[:6]}",
            contact_name="Dana Reyes", phone="+1-555-0100",
            license_number="LIC-SECRET-9", insurance_info="Policy SECRET-123",
            notes="Internal note: renegotiate rate",
        )
        session.add(vendor)
        session.flush()

        session.add(
            WorkOrder(
                id=uuid.uuid4(), title="Quarterly pest treatment",
                slug=f"wo-{uuid.uuid4().hex[:8]}",
                property_id=prop.id, vendor_id=vendor.id,
                estimated_cost=1234.0, actual_cost=4321.0,
            )
        )
        created["property_id"] = prop.id

    web_client_as.seed(_seed)
    return created


class TestMoneyRedactedInTheRenderedPage:
    def test_staff_cannot_see_work_order_costs(self, web_client_as, estate):
        """A12, end to end. The staff member **may** see this work order — that is the point.

        Asserting on the rendered digits rather than on the object is what makes this a test of
        the *applied* redaction. `1234` and `4321` are chosen to be implausible as incidental
        markup (ids, dates, CSS) so a match is real.
        """
        client = web_client_as("staff", scoped_to=[estate["property_id"]])
        body = client.get("/work-orders/").text

        assert "Quarterly pest treatment" in body, (
            "staff must still see the work order itself — D14 redacts money, it does not "
            "remove records staff need to do their jobs"
        )
        assert "1234" not in body, "estimated_cost leaked to staff"
        assert "4321" not in body, "actual_cost leaked to staff"

    def test_admin_still_sees_the_costs(self, web_client_as, estate):
        """The negative control, and it is not decoration.

        A redactor that stripped money from *everyone* would satisfy the assertion above while
        breaking the product for the people who run it. Without this test, "hide it from
        everybody" is a passing implementation.
        """
        client = web_client_as("admin")
        body = client.get("/work-orders/").text

        assert "Quarterly pest treatment" in body
        assert "1234" in body or "1,234" in body, "admin must still see estimated_cost"


class TestVendorContactOnlyEndToEnd:
    def test_staff_see_contact_fields_but_not_the_sensitive_ones(self, web_client_as, estate):
        """A13 · D12 — *"Staff get company_name, contact_name, phone, email, contacts. Never
        insurance_info, license_number, notes, or ratings."*"""
        client = web_client_as("staff", scoped_to=[estate["property_id"]])
        body = client.get("/vendors/").text

        assert "Orkin Pest" in body
        assert "LIC-SECRET-9" not in body, "license_number leaked to staff"
        assert "Policy SECRET-123" not in body, "insurance_info leaked to staff"
        assert "renegotiate rate" not in body, "vendor notes leaked to staff"

    def test_admin_sees_vendor_detail(self, web_client_as, estate):
        client = web_client_as("admin")
        body = client.get("/vendors/").text
        assert "Orkin Pest" in body


class TestRedactionSurvivesTraversal:
    def test_vendor_fields_are_redacted_when_reached_through_a_work_order(
        self, web_client_as, estate
    ):
        """The transitive case, on a real page.

        `_query_work_orders`-style traversal (`wo.vendor.license_number`) is how a flat redactor
        leaks: the work order is redacted, the vendor hanging off it is not. G2 proved the proxy
        handles this in isolation; this proves the page does.
        """
        client = web_client_as("staff", scoped_to=[estate["property_id"]])
        body = client.get("/work-orders/").text

        assert "LIC-SECRET-9" not in body
        assert "Policy SECRET-123" not in body
