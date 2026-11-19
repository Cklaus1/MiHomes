"""G7 · §6 Step 7 — staff scoping enforced **at the query layer**.

§9.4 step 4's own words: *"filtered to scoped homes at the query layer, not post-hoc."* The
distinction is not stylistic. Post-hoc filtering means the rows were fetched, passed through
application code, and possibly counted, aggregated, or logged before being dropped — and any
surface that forgets to drop them leaks. A query that never selects them cannot leak them.

**Step 7's real change, in the spec's words:** *"Reuses the per-property filtering pages already
have… the change is that staff get an **enforced allowed-set** rather than an optional
user-chosen filter."* `list_tasks(property_id_or_slug=...)` was always optional; omitting it
returned the whole account. For staff it is now not a filter at all — it is a ceiling.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.models.property import Property
from mihomes.models.task import Task


@pytest.fixture
def two_properties(web_client_as):
    """Belle and Blue, each with one distinguishable task."""
    created = {}

    def _seed(session):
        for name in ("Belle Estate", "Blue Room"):
            prop = Property(
                id=uuid.uuid4(), name=name,
                slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
            )
            session.add(prop)
            session.flush()
            session.add(
                Task(
                    id=uuid.uuid4(), title=f"{name} task", property_id=prop.id,
                    slug=f"task-{uuid.uuid4().hex[:8]}",
                )
            )
            created[name] = prop.id

    web_client_as.seed(_seed)
    return created


class TestCollectionScoping:
    def test_collection_scoped_rows_only(self, web_client_as, two_properties):
        """A staff member scoped to Belle sees Belle's task and **not** Blue's.

        Asserting on the *absence* of the other property's row is the whole test; asserting only
        that Belle's task is present would pass against no filtering at all.
        """
        client = web_client_as("staff", scoped_to=[two_properties["Belle Estate"]])
        body = client.get("/tasks/").text

        assert "Belle Estate task" in body
        assert "Blue Room task" not in body, (
            "an out-of-scope property's rows must never reach the response — filtered at the "
            "query layer, not dropped in the template (§9.4 step 4)"
        )

    def test_unscoped_staff_sees_nothing(self, web_client_as, two_properties):
        """D3 — zero scope rows means zero properties, never "all".

        The fail-closed case, and the one a "no scope rows → apply no filter" implementation gets
        exactly backwards: it would return both.
        """
        client = web_client_as("staff", scoped_to=[])
        body = client.get("/tasks/").text

        assert "Belle Estate task" not in body
        assert "Blue Room task" not in body

    def test_privileged_unchanged(self, web_client_as, two_properties):
        """G7.3 — owner and admin behaviour is untouched.

        The regression guard. Scoping that also constrained owners would "pass" every staff
        assertion above while silently breaking the product for the person who owns it.
        """
        for role in ("owner", "admin"):
            client = web_client_as(role)
            body = client.get("/tasks/").text
            assert "Belle Estate task" in body, role
            assert "Blue Room task" in body, role

    def test_privileged_with_scope_rows_still_sees_everything(
        self, web_client_as, two_properties
    ):
        """A11 again, now end to end: `ONBOARDING:44` says owner/admin scope rows are ignored.

        A scope row on an admin is not a restriction — it is data the whitelist ignores. An
        implementation that applied the rows uniformly would quietly demote every admin who
        happened to have one.
        """
        client = web_client_as("admin", scoped_to=[two_properties["Belle Estate"]])
        body = client.get("/tasks/").text
        assert "Blue Room task" in body


class TestExplicitOutOfScopeRequest:
    def test_out_of_scope_explicit_is_404(self, web_client_as, two_properties):
        """G7.2 — asking for a property you may not see is **404, not an empty list**.

        §6 Step 7 is explicit about this, and the reason is D9: an empty list says "this property
        exists and has no tasks", which is information. A 404 says nothing at all. The two are
        indistinguishable to a careless implementation and completely different to someone
        enumerating ids.
        """
        blue = two_properties["Blue Room"]
        client = web_client_as("staff", scoped_to=[two_properties["Belle Estate"]])

        response = client.get(f"/tasks/?property_id={blue}")
        assert response.status_code == 404, (
            f"expected 404 for an out-of-scope property, got {response.status_code} — an empty "
            "list would confirm the property exists"
        )

    def test_in_scope_explicit_still_works(self, web_client_as, two_properties):
        """The positive control: the filter still filters for the properties staff *may* see."""
        belle = two_properties["Belle Estate"]
        client = web_client_as("staff", scoped_to=[belle])

        response = client.get(f"/tasks/?property_id={belle}")
        assert response.status_code == 200
        assert "Belle Estate task" in response.text

    def test_privileged_may_request_any_property(self, web_client_as, two_properties):
        """An owner asking for a specific property is filtering, not trespassing."""
        client = web_client_as("owner")
        response = client.get(f"/tasks/?property_id={two_properties['Blue Room']}")
        assert response.status_code == 200
        assert "Blue Room task" in response.text


class TestScopeAppliesBeyondTasks:
    def test_scoping_is_not_a_tasks_only_patch(self, web_client_as, two_properties):
        """N4 — the scope must reach every property-scoped entity, not the one page it was
        first tested on.

        The dashboard aggregates across properties without taking a `property_id` at all, so it
        is the surface a per-route filter would miss entirely. If scoping is applied by the query
        layer rather than by editing each view, this passes for free — and if someone later
        "optimises" it into a per-route argument, this is what fails.
        """
        client = web_client_as("staff", scoped_to=[two_properties["Belle Estate"]])
        body = client.get("/").text
        assert "Blue Room" not in body
