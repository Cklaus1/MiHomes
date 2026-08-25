"""U7 — the four unenforced entity classes, and the leaks that proved they were unenforced.

**The finding this file exists for.** §4.1 defines six entity classes and G1 pins that every
mapped model lands in exactly one. G17 then asked a different question — *which classes does any
code actually read?* — and the answer was **one**. `authz/query_scope.py` derived its model list
from `EntityClass.PROPERTY_SCOPED` and nothing consulted the other five, so `ACCOUNT_LEVEL`,
`PERSONNEL`, `PROPERTY_LINKED` and `FLAGGED` were labels with no mechanism behind them. A model
classified `ACCOUNT_LEVEL` was not denied to staff by that classification; it was denied only if
some route happened to declare an action staff lack — and where a route declared a `SCOPED`
action instead, the class said "staff never see this" while the query returned it.

Four leaks came out of that gap, each found the same way — by asking which class enforces the
rule rather than trusting that the rule was enforced:

* `/library/` returned another property's books (G17, fixed by reclassifying `Book`)
* `/ai/sessions*` served transcripts to staff (G17, fixed by redeclaring the routes)
* `/search/` returns notes from properties a staff member cannot see  ← **closed here**
* `/vendors/` renders vendor ratings to staff, which D12 denies by name  ← **closed here**

The last two are not route mistakes and cannot be fixed by redeclaring anything. Both routes are
*correctly* declared with actions staff legitimately hold (`property.view`, `vendor.view_contact`,
both `SCOPED`), and both read an `ACCOUNT_LEVEL` model from inside a **service** rather than the
endpoint body. That is why G17's static scan missed them — it admitted the residual in writing —
and it is the case for giving the class real query-layer teeth rather than patching two call
sites and waiting for the third.

**The leak tests come first in this file deliberately.** They are the empirical claim; everything
below them asserts the mechanism that makes them pass. A mechanism test that goes green while the
leak stays open is the failure mode this ordering is meant to make impossible.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from mihomes.authz.actions import ENTITY_CLASSES, EntityClass

# ─────────────────────────────────────────────────────────────────────────────
# The two live leaks
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def two_properties_with_notes(web_client_as):
    """Two properties, each with a note whose text names it.

    `Note` is polymorphic — `entity_type`/`entity_id`, no `property_id` — which is the whole
    reason it needs its own criteria shape. A note is reachable only *through* the row it hangs
    off, so scoping it means scoping by the parent's property.
    """
    created = {}

    def _seed(session):
        from mihomes.models.note import Note
        from mihomes.models.property import Property

        for name in ("Belle Estate", "Blue Room"):
            prop = Property(
                id=uuid.uuid4(),
                name=name,
                slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
            )
            session.add(prop)
            session.flush()
            session.add(
                Note(
                    id=uuid.uuid4(),
                    entity_type="property",
                    entity_id=prop.id,
                    content=f"CONFIDENTIAL {name} alarm code is 4417",
                )
            )
            created[name] = prop.id

    web_client_as.seed(_seed)
    return created


class TestSearchDoesNotLeakOutOfScopeNotes:
    """`services/search.py` runs a raw `Note.content ILIKE` across the whole account.

    The route is `SCOPED` for staff and correctly so — a staff member may search. What they may
    not have is a search that reaches a property outside their scope, and before U7 the note
    branch had nothing standing between the two: `Note` is `ACCOUNT_LEVEL`, and `ACCOUNT_LEVEL`
    filtered nothing.
    """

    def test_staff_search_excludes_notes_from_unscoped_properties(
        self, web_client_as, two_properties_with_notes
    ):
        client = web_client_as(
            "staff", scoped_to=[two_properties_with_notes["Belle Estate"]]
        )
        body = client.get("/search/?q=alarm").text

        assert "Blue Room alarm code" not in body, (
            "a staff member scoped to Belle Estate searched and received a note attached to Blue "
            "Room — the property they cannot see. `Note` is ACCOUNT_LEVEL, which before U7 meant "
            "nothing at the query layer."
        )

    def test_the_search_still_works_for_the_scoped_property(
        self, web_client_as, two_properties_with_notes
    ):
        """The positive control. Without it, `_note_criteria` returning `false()` for every row
        would pass the test above while breaking search entirely."""
        client = web_client_as(
            "staff", scoped_to=[two_properties_with_notes["Belle Estate"]]
        )
        body = client.get("/search/?q=alarm").text

        assert "Belle Estate alarm code" in body, (
            "the scoped property's own note is missing, so the filter is denying everything "
            "rather than denying what is out of scope"
        )

    def test_the_owner_still_sees_both(self, web_client_as, two_properties_with_notes):
        """Scoping is intra-account and applies to staff only; an owner has no scope at all."""
        body = web_client_as("owner").get("/search/?q=alarm").text
        assert "Belle Estate alarm code" in body
        assert "Blue Room alarm code" in body


@pytest.fixture
def vendor_with_rating(web_client_as):
    """A vendor carrying a rating whose free-text notes are the thing D12 protects."""
    created = {}

    def _seed(session):
        from mihomes.models.property import Property
        from mihomes.models.vendor import Vendor
        from mihomes.models.vendor_rating import VendorRating

        prop = Property(
            id=uuid.uuid4(), name="Belle Estate", slug=f"belle-{uuid.uuid4().hex[:6]}"
        )
        session.add(prop)
        vendor = Vendor(
            id=uuid.uuid4(),
            company_name="Orkin Pest",
            slug=f"orkin-{uuid.uuid4().hex[:6]}",
        )
        session.add(vendor)
        session.flush()
        session.add(
            VendorRating(
                id=uuid.uuid4(),
                vendor_id=vendor.id,
                quality_score=2,
                reliability_score=1,
                overall_score=2,
                notes="OVERCHARGED us badly, disputing the invoice",
                rated_date=date(2026, 8, 1),
                # **`property_id` left NULL on purpose** — it is nullable here, and a rating
                # naming no property is the harder case to reason about: `NULL IN (...)` is NULL
                # in SQL rather than false. It is still excluded, because a WHERE clause keeps
                # only rows evaluating to *true*, and the module's first draft carried a
                # redundant `is_not(None)` guard on the mistaken belief that it would not be.
                # Mutation testing removed the guard and this seed is what keeps the behaviour
                # asserted rather than assumed.
                property_id=None,
            )
        )
        created["vendor_slug"] = vendor.slug
        created["property_id"] = prop.id

    web_client_as.seed(_seed)
    return created


class TestVendorRatingsAreNotServedToStaff:
    """D12 denies staff vendor ratings **by name**, and `redact.py` asserted it was already so.

    That comment — *"`VendorRating` is classified `ACCOUNT_LEVEL`, so staff never receive the
    row"* — was false, and it is the most instructive part of this finding: it is a written claim
    that a classification enforces something, in a file whose entire job is enforcement. The
    classification was right; nothing read it. Corrected in the same commit as this test.
    """

    def test_staff_do_not_see_a_rating(self, web_client_as, vendor_with_rating):
        client = web_client_as("staff", scoped_to=[vendor_with_rating["property_id"]])
        body = client.get("/vendors/").text

        assert "OVERCHARGED" not in body, (
            "a staff member received a vendor rating's free-text notes. D12 denies staff ratings "
            "by name, and `VendorRating` is classified ACCOUNT_LEVEL — before U7 neither of those "
            "facts filtered a single row."
        )

    def test_the_vendor_itself_is_still_visible(self, web_client_as, vendor_with_rating):
        """The positive control: `vendor.view_contact` is `SCOPED`, not denied. Staff may see the
        vendor — contact fields only (D12) — so a test that merely proved `/vendors/` was empty
        would prove the wrong thing."""
        client = web_client_as("staff", scoped_to=[vendor_with_rating["property_id"]])
        body = client.get("/vendors/").text
        assert "Orkin Pest" in body, (
            "staff can no longer see the vendor at all, which over-denies: D12 grants them "
            "contact fields and withholds ratings"
        )

    def test_the_owner_still_sees_the_rating(self, web_client_as, vendor_with_rating):
        """The other half of the claim, and the one that keeps the fix from being over-broad.

        `/vendors/` is the only vendor route — `_ctx` builds the ratings for the page — so both
        roles are asserted against the same URL. If the filter denied ratings outright rather
        than denying them *to staff*, the test above would still pass and the product would have
        lost a feature; this is what notices.
        """
        body = web_client_as("owner").get("/vendors/").text
        assert "OVERCHARGED" in body, "the owner lost access to their own vendor ratings"


# ─────────────────────────────────────────────────────────────────────────────
# The mechanism
# ─────────────────────────────────────────────────────────────────────────────


class TestEveryClassNowHasAMechanism:
    """The structural claim, derived rather than listed.

    `test_leak_matrix.py` carries the inverse of this — the set of classes with *no* mechanism —
    and the two must be edited as a pair. Asserting the derived set here means a newly added
    class cannot quietly join the unenforced group.
    """

    def test_account_level_is_read_by_the_query_layer(self):
        from mihomes.authz import query_scope

        models = query_scope._models_in_class(EntityClass.ACCOUNT_LEVEL)
        assert models, "ACCOUNT_LEVEL resolves to no models, so its filter can never fire"

    def test_personnel_is_read_by_the_query_layer(self):
        from mihomes.authz import query_scope

        assert query_scope._models_in_class(EntityClass.PERSONNEL)

    def test_the_derivation_follows_the_classification_not_a_hardcoded_list(self):
        """The property N4 is actually about: classify a model and it is enforced.

        A hardcoded list would pass every other test in this class while leaving the next model
        someone adds unprotected — which is precisely how `Book` and `VendorRating` came to be
        classified-but-unenforced.
        """
        from mihomes.authz import query_scope

        derived = query_scope._models_in_class(EntityClass.ACCOUNT_LEVEL)
        expected = {
            m
            for m, c in ENTITY_CLASSES.items()
            if c is EntityClass.ACCOUNT_LEVEL
            and m.__name__ not in query_scope._ACCOUNT_LEVEL_EXEMPT
        }
        assert set(derived) == expected


class TestTheSeventhClassIsDeclaredNotDecorative:
    """`ACCOUNT_SHARED` (SPEC-003 U6) applies no row filter — **and says so, in data.**

    This is the exact shape U7 was about, arrived at from the other direction. There, four classes
    were unfiltered because nothing read them, and "decided" was indistinguishable from
    "forgotten" until two leaks made the difference visible. A seventh class that is *correctly*
    unfiltered would look identical to those four unless the decision is recorded somewhere a test
    can check — so it is recorded in `query_scope.UNFILTERED_CLASSES` with its reason.
    """

    def test_the_class_is_declared_unfiltered_with_a_reason(self):
        from mihomes.authz import query_scope

        assert EntityClass.ACCOUNT_SHARED in query_scope.UNFILTERED_CLASSES
        reason = query_scope.UNFILTERED_CLASSES[EntityClass.ACCOUNT_SHARED]
        assert len(reason) > 80, (
            "a one-line reason is not a reason. This entry is what stands between 'we decided not "
            "to filter this' and 'nobody noticed it was unfiltered'."
        )

    def test_every_unfiltered_class_is_accounted_for(self):
        """Derived: the classes with no criteria must be exactly the ones declared unfiltered.

        A new class added without either a filter or an entry here fails this test rather than
        silently joining the unenforced group — which is precisely how `ACCOUNT_LEVEL` and
        `PERSONNEL` spent a phase.
        """
        from mihomes.authz import query_scope

        filtered = {
            EntityClass.PROPERTY_SCOPED,
            EntityClass.ACCOUNT_LEVEL,
            EntityClass.PERSONNEL,
            EntityClass.FLAGGED,
        }
        declared_unfiltered = set(query_scope.UNFILTERED_CLASSES)

        assert filtered | declared_unfiltered == set(EntityClass), (
            "a class is neither filtered nor declared unfiltered: "
            f"{sorted(c.value for c in set(EntityClass) - filtered - declared_unfiltered)}"
        )
        assert not (filtered & declared_unfiltered), (
            "a class is both filtered and declared unfiltered — one of the two is wrong: "
            f"{sorted(c.value for c in filtered & declared_unfiltered)}"
        )

    def test_templates_are_in_the_new_class_and_not_exempted(self):
        """The pair that motivated the class, asserted together.

        Being in `ACCOUNT_SHARED` *and* absent from the exemption list is the whole change: before,
        the model was mislabelled and the exemption hid it; now the label is right and no exemption
        is needed.
        """
        from mihomes.authz import query_scope
        from mihomes.models.template import Template, TemplateItem

        for model in (Template, TemplateItem):
            assert ENTITY_CLASSES[model] is EntityClass.ACCOUNT_SHARED
            assert model.__name__ not in query_scope._ACCOUNT_LEVEL_EXEMPT

    def test_staff_can_still_read_templates_at_the_query_layer(self, web_client_as):
        """The behaviour the class asserts, measured rather than inferred from the label."""
        from mihomes.authz.scope import authz_context
        from mihomes.models.template import Template

        name = f"SHAREDTEMPLATE-{uuid.uuid4().hex[:6]}"

        def _seed(session):
            session.add(Template(id=uuid.uuid4(), name=name, slug=f"t-{uuid.uuid4().hex[:8]}"))

        web_client_as.seed(_seed)
        session = web_client_as.session_for_scope()

        with authz_context("staff", frozenset({uuid.uuid4()})):
            names = {t.name for t in session.query(Template).all()}

        assert name in names, (
            "a staff member with a scope bound cannot read templates. ACCOUNT_SHARED means no row "
            "filter — if one is being applied, running a template becomes impossible."
        )


class TestScopeExemptions:
    """Two models must stay exempt, and the reasons are different in kind."""

    def test_membership_tables_are_exempt(self):
        """`scope.py scoped_property_ids` reads them to *compute* the staff scope.

        Denying them makes the primitive recursive on its own filter: resolving "which properties
        may this staff member see" would require already knowing the answer. This is the one
        exemption that is structural rather than a deferral.
        """
        from mihomes.authz import query_scope

        assert "Membership" in query_scope._ACCOUNT_LEVEL_EXEMPT
        assert "MembershipPropertyScope" in query_scope._ACCOUNT_LEVEL_EXEMPT

    def test_every_exemption_is_structural(self):
        """An exemption list is a hole in the mechanism, so **what kind** of hole matters most.

        All three remaining entries share one reason: the filter would be **circular**. Each names
        a table the authorization primitive itself reads, so subjecting it to its own criteria
        would mean answering the question by already knowing the answer.

        - `Membership`/`MembershipPropertyScope` — `scoped_property_ids` reads them to compute the
          staff scope.
        - `DocumentAccess` — `_document_criteria` reads it to decide document visibility.

        **`Template`/`TemplateItem` were here and are not**, which is the property this test now
        protects. They were exempted for a *different* reason — `ACCOUNT_LEVEL` was simply the
        wrong label, and the exemption neutralised it — and SPEC-003 U6 fixed that at the source by
        adding `EntityClass.ACCOUNT_SHARED`. A list holding only circular-dependency cases stays
        small on its own; one that also accepts "the class is wrong here" grows, because every new
        entry has a precedent that looks just like it.
        """
        from mihomes.authz import query_scope

        assert query_scope._ACCOUNT_LEVEL_EXEMPT == frozenset(
            {
                "Membership",
                "MembershipPropertyScope",
                "DocumentAccess",
            }
        ), (
            "the exemption list changed. Every entry must be a table the authz primitive itself "
            "reads — if a model is exempt because its *class* is wrong, fix the class instead."
        )


class TestModelsWithNoPropertyLinkageAreDenied:
    """The third `ACCOUNT_LEVEL` shape — and **this class exists because mutation testing found
    it missing.**

    Flipping `_account_level_criteria`'s final `return false()` to `return true()` turned no test
    red. The arm was not decoration — probing it directly showed the `false()` branch correctly
    suppressing a `Tag` row a staff member would otherwise read — it was simply **untested**, and
    an untested deny is one refactor away from an allow that nobody notices. Seven models land in
    that branch: `Tag`, `Configuration`, `AIConversation`, `Invite`, `OnboardingState`,
    `TelegramLink`, `Account`.

    `Tag` stands in for the group. It is the cheapest to construct and the branch is chosen by
    column shape rather than by model identity, so covering one covers the mechanism; the
    membership of the group is asserted separately below, which is what would catch a model
    silently changing shape.
    """

    def test_a_staff_member_reads_no_tags(self, web_client_as):
        from mihomes.authz.scope import authz_context
        from mihomes.models.tag import Tag

        def _seed(session):
            session.add(Tag(id=uuid.uuid4(), name=f"Payroll-{uuid.uuid4().hex[:6]}"))

        web_client_as.seed(_seed)
        session = web_client_as.session_for_scope()

        with authz_context("staff", frozenset({uuid.uuid4()})):
            assert session.query(Tag).all() == [], (
                "a staff member read an account-level Tag. Models with no property linkage have "
                "no scope under which they become visible, so the criteria is `false()`."
            )

    def test_the_owner_still_reads_tags(self, web_client_as):
        """The control. `false()` for everyone would pass the test above and break the product."""
        from mihomes.models.tag import Tag

        name = f"Payroll-{uuid.uuid4().hex[:6]}"

        def _seed(session):
            session.add(Tag(id=uuid.uuid4(), name=name))

        web_client_as.seed(_seed)
        session = web_client_as.session_for_scope()
        assert name in {t.name for t in session.query(Tag).all()}

    def test_the_no_linkage_group_is_exactly_the_models_with_neither_shape(self):
        """Derived, so a model changing shape moves branch without an edit here.

        If someone adds `property_id` to `Configuration`, it stops being denied outright and
        starts being scope-filtered — a real behaviour change that this test surfaces rather than
        letting it pass as a refactor.
        """
        from mihomes.authz import query_scope

        no_linkage = set()
        for model in query_scope._models_in_class(EntityClass.ACCOUNT_LEVEL):
            columns = set(model.__table__.c.keys())
            if "property_id" in columns:
                continue
            if {"entity_type", "entity_id"} <= columns:
                continue
            no_linkage.add(model.__name__)

        assert no_linkage == {
            "Tag",
            "Configuration",
            "AIConversation",
            "Invite",
            "OnboardingState",
            "TelegramLink",
            "Account",
            # SPEC-004 §4.2 — the AI usage meter, and this gate is doing its job by demanding
            # the addition be acknowledged rather than absorbed.
            #
            # Both are **billing data**: `calls_used` against a plan's cap tells a staff member
            # which tier the household pays for, and the event log's `entry_point` traces who
            # used the assistant and when. Denying them outright is the intended outcome — row
            # 15 (`billing.manage`) is owner-only, and D10 keeps RBAC and entitlements separate,
            # so an Estate plan buys more calls and never the right to read whose they were.
            "AIUsageEvent",
            "AIUsageRollup",
        }, (
            "the set of models denied outright has changed. Each one is a model staff can no "
            "longer read at all (or can now read) — confirm which, and why, before updating this."
        )


class TestPersonnelFiltersToOwnRecord:
    """§4.1's `PERSONNEL` rule, now that `staff.user_id` (U6a) makes "mine" answerable.

    Exercised at the service layer rather than over HTTP, deliberately: the HR routes are all
    `member.manage` until U6b redeclares them, so there is no URL a staff member can reach yet.
    The filter has to be correct *before* the route opens, not after — building the mechanism and
    the permission in one commit is how you end up unable to tell which of the two is wrong.
    """

    def test_a_staff_member_sees_only_their_own_staff_row(self, web_client_as, account_a):
        from mihomes.authz.scope import authz_context
        from mihomes.models.staff import Staff
        from mihomes.tenancy.context import current_user

        mine = uuid.uuid4()
        web_client_as.connection.execute(
            text(
                "INSERT INTO users (id, google_sub, email, created_at) "
                "VALUES (:id, :sub, :email, now())"
            ),
            {"id": mine, "sub": f"s-{mine.hex[:8]}", "email": f"{mine.hex[:6]}@example.com"},
        )

        def _seed(session):
            session.add(
                Staff(id=uuid.uuid4(), name="Me Myself",
                      slug=f"me-{uuid.uuid4().hex[:6]}", user_id=mine)
            )
            session.add(
                Staff(id=uuid.uuid4(), name="Someone Else",
                      slug=f"else-{uuid.uuid4().hex[:6]}")
            )

        web_client_as.seed(_seed)

        session = web_client_as.session_for_scope()
        token = current_user.set(mine)
        try:
            with authz_context("staff", frozenset()):
                names = {s.name for s in session.query(Staff).all()}
        finally:
            current_user.reset(token)

        assert "Someone Else" not in names, (
            "a staff member read a colleague's HR record. PERSONNEL means own record only (F2d)."
        )
        assert "Me Myself" in names, (
            "their own record is missing too, so the filter denies everything rather than "
            "denying everyone else — the rule is 'own record only', not 'no record'"
        )

    def test_an_unbound_user_sees_no_personnel_rows(self, web_client_as):
        """Fail-closed when a staff role is bound but no user is — D3's zero-scope direction.

        The opposite reading (no user → no filter) would make every unattended path a full read
        of the HR table. `current_user` is deliberately left unset here.
        """
        from mihomes.authz.scope import authz_context
        from mihomes.models.staff import Staff

        def _seed(session):
            session.add(
                Staff(id=uuid.uuid4(), name="Nobody", slug=f"n-{uuid.uuid4().hex[:6]}")
            )

        web_client_as.seed(_seed)

        session = web_client_as.session_for_scope()
        with authz_context("staff", frozenset()):
            assert session.query(Staff).all() == []


class TestTheFilterSurvivesCountAndDelete:
    """The gap that made this file necessary twice over.

    `tenancy/session.py` gates on `state.all_mappers`, and that collection is **empty** for
    `query(M).count()` — the statement's top-level column is a bare `count(*)`, not an entity — so
    the tenant filter silently skips every count. Measured, not inferred, while building U7: a
    row planted under another account was counted by `.count()` and correctly absent from
    `.all()`. A criteria that applies to `.all()` and not to `.count()` protects the page and not
    the number on it.

    Statement type matters for the reason `tenancy/session.py`'s N2 note gives: `is_select` alone
    leaves `session.query(X).delete()` unscoped.
    """

    def test_count_is_filtered_not_just_all(self, web_client_as, two_properties_with_notes):
        from mihomes.authz.scope import authz_context
        from mihomes.models.note import Note

        scope = frozenset({two_properties_with_notes["Belle Estate"]})
        session = web_client_as.session_for_scope()
        with authz_context("staff", scope):
            loaded = len(session.query(Note).all())
            counted = session.query(Note).count()

        assert counted == loaded, (
            f"`.count()` returned {counted} where `.all()` returned {loaded} rows — the filter "
            "is not reaching count statements, so any total, badge or aggregate built this way "
            "reports rows the user cannot open"
        )

    def test_delete_is_filtered(self, web_client_as, two_properties_with_notes):
        """A bulk delete must not reach out-of-scope rows (N2)."""
        from mihomes.authz.scope import authz_context
        from mihomes.models.note import Note

        scope = frozenset({two_properties_with_notes["Belle Estate"]})
        session = web_client_as.session_for_scope()
        with authz_context("staff", scope):
            session.query(Note).delete(synchronize_session=False)
            session.flush()

        remaining = session.execute(text("SELECT COUNT(*) FROM notes")).scalar()
        assert remaining >= 1, (
            "a scoped staff delete removed every note in the account, including the property "
            "they cannot see"
        )
