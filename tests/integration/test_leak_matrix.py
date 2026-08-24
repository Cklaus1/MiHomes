"""G17 · §6 Step 17 — the cross-cutting adversarial leak matrix.

Every other group in this phase proves its own mechanism. This one asks the question none of them
can: **for each entity class in §4.1, is staff reach actually what the classification says?**

That question is not a restatement of G7 or G10. Those tests each pick a model and prove the
mechanism they built works on it. The classification, meanwhile, is a *promise about all 44 mapped
models* — and a promise is only as good as the weakest model nobody probed. Two failure modes live
in that gap, and G17 found one of each:

1. **A class no mechanism reads.** `EntityClass` has six values. Grep them outside `actions.py`
   and exactly **one** — `PROPERTY_SCOPED` — is read by anything (`query_scope.scoped_models`),
   plus `FLAGGED` reached by name in `_document_criteria`. `ACCOUNT_LEVEL`, `PROPERTY_LINKED`,
   `PERSONNEL` and `GLOBAL` are read by **no code at all**. They are enforced, where they are
   enforced, by whatever action the *route* happens to declare — which means the classification
   and the enforcement are two independent statements that nothing compares.

2. **A model whose class and whose route disagree.** That is not hypothetical: it is how
   `/library/` was returning another property's books to a scoped staff member. `Book` was
   `ACCOUNT_LEVEL` (`✗` for staff), `library.py` declared `inventory.manage` (`SCOPED` for
   staff), and since `ACCOUNT_LEVEL` has no query-layer enforcement the route's grant simply won.
   G6 predicted the contradiction and filed it as a documentation inconsistency; this file is
   where it turned out to be a live leak.

**So the load-bearing test here is `test_every_entity_class_has_a_mechanism`, not a reach
assertion.** A reach assertion on an unenforced class tells you about the one route you probed. A
census of which classes have a mechanism at all tells you where the next leak will be.

**Why the exception lists are two dicts and not one.** They record two different problems, and a
reader resolving one needs to know they are not resolving the other:

  NOT_YET_ENFORCEABLE   the class is right; no mechanism can reach the model. Fixing it is a
                        design change (child tables need a scoping path that does not exist).
  NO_CLASS_FITS         the model's class is wrong and none of the six is right. Fixing it is
                        spec work (a new class, or a new matrix key).

Collapsing them into one list of "not enforced" is how both stop being fixed.
"""

from __future__ import annotations

import datetime
import inspect as _inspect
import uuid

import pytest

from mihomes.authz import query_scope
from mihomes.authz.actions import ENTITY_CLASSES, MATRIX, Access, EntityClass, Grant
from mihomes.authz.declare import declared_action
from mihomes.authz.scope import authz_context
from mihomes.models.asset import Asset, AssetType, PriceEntry
from mihomes.models.book import Book
from mihomes.models.event import Guest
from mihomes.models.property import Property
from mihomes.models.template import Template

# ======================================================================================
# The two exception lists.
# ======================================================================================

#: `model_name -> reason`. The class is **correct**; no mechanism can reach the model.
#:
#: All three are children of a property-scoped parent that carry **no `property_id` of their
#: own**, which `query_scope.scoped_models()` requires. A query that loads them *through* the
#: parent is protected by the parent's filter; a direct query on the child is not.
#: `query_scope`'s module docstring names this set, and `opportunities.md` carries the `[DEFER]`.
#:
#: **Verified by probe, not assumed** — a scoped staff member issuing `session.query(PriceEntry)`
#: sees an out-of-scope property's row, while `session.query(Asset)` on the same connection
#: correctly returns zero. The mechanism works; these models are outside its reach.
NOT_YET_ENFORCEABLE = {
    "PriceEntry": (
        "Child of Asset with no property_id. Money — so the amount is separately stripped by "
        "REDACTED_FIELDS[PriceEntry] = {'price'}, which is why this is the milder of the two: "
        "the row is reachable, the number is not. Confirmed reachable by direct query."
    ),
    "Guest": (
        "Child of Event with no property_id, and the SHARPER of the two: a guest's name is not "
        "money, so no redaction covers it. A scoped staff member can enumerate the names of "
        "people invited to another property's event. Confirmed reachable by direct query."
    ),
    "TaskSchedule": (
        "Child of Task with no property_id — same shape as the two above and named in "
        "query_scope's docstring, but UNVERIFIED: the probe returned zero rows because none were "
        "seeded, not because a filter applied. Listed rather than trusted."
    ),
    # These two were NOT in the first draft of this dict. `test_property_scoped_models_are_
    # enforced_or_declared` derives its model list from ENTITY_CLASSES and turned red naming
    # both — the derivation catching its own author, which is the argument for deriving it.
    "ConsumablePriceEntry": (
        "Child of Consumable with no property_id. Money, so REDACTED_FIELDS strips the amount — "
        "the same mitigation and the same residual reach as PriceEntry, whose entry explains it. "
        "Found by the derived gate, not by hand: it was missing from this dict's first draft."
    ),
    "EventGuest": (
        "The Event-to-Guest association with no property_id of its own. Reachability here is the "
        "same exposure Guest's entry describes, reached from the other side: knowing WHICH event "
        "an out-of-scope guest attended. Also found by the derived gate rather than by hand."
    ),
}

#: `model_name -> reason`. The model's class is **wrong**, and none of §4.1's six is right.
#: **Empty, and it took three groups to get here.** `Template`/`TemplateItem` were the only entries
#: and they are resolved: SPEC-003 U6's closing change added `EntityClass.ACCOUNT_SHARED` — the
#: class this list's own diagnosis asked for, *"account-wide, not sensitive, staff use it"* — and
#: reclassified both models into it.
#:
#: The history is worth keeping because the entry was right and the plan to close it was wrong
#: twice. G17 recorded the gap. U6b expected a dedicated matrix key to let the rows be denied at
#: the query layer and instead **confirmed** the entry: `run_template` resolves by slug, so running
#: a template requires reading its row, and denying rows would leave staff a `/run` endpoint whose
#: targets they cannot see. What was actually missing was never enforcement — it was a name for
#: "staff may read these rows and not administer them", which is a verb distinction the route
#: declarations carry (`task.manage` to run, `automation.manage` to manage).
#:
#: Kept as an empty dict rather than deleted: `test_the_two_exception_lists_are_disjoint` and
#: `test_every_declared_exception_is_still_true` both read it, and an empty list that is still
#: asserted is how a new entry gets noticed.
NO_CLASS_FITS: dict[str, str] = {}

_ALL_EXCEPTIONS = {**NOT_YET_ENFORCEABLE, **NO_CLASS_FITS}


# ======================================================================================
# 1. The census — which classes have a mechanism at all.
# ======================================================================================

#: `EntityClass -> how staff reach is actually constrained`. Written from the code, and
#: `test_every_entity_class_has_a_mechanism` re-derives the `PROPERTY_SCOPED` entry from
#: `query_scope` so this table cannot drift into fiction.
CLASS_MECHANISM = {
    EntityClass.PROPERTY_SCOPED: "query_scope.scoped_models() — with_loader_criteria per model",
    EntityClass.FLAGGED: "query_scope._document_criteria — staff_visible AND parent in scope",
    EntityClass.PROPERTY_LINKED: "redact.REDACTED_FIELDS[Vendor] — row allowed, fields stripped",
    # **Both of these read "ROUTE DECLARATION ONLY" until U7.** That was the finding: four of the
    # six classes were labels with nothing behind them, and two live leaks (`/search/` notes,
    # `/vendors/` ratings) came out of exactly that gap — routes correctly declaring actions staff
    # hold, reading an ACCOUNT_LEVEL model from inside a service.
    EntityClass.ACCOUNT_LEVEL: (
        "query_scope._account_level_criteria — property_id IN scope / polymorphic parent "
        "subquery / false(), by column shape"
    ),
    EntityClass.PERSONNEL: "query_scope._personnel_criteria — Staff.user_id == current_user",
    EntityClass.GLOBAL: "OUT OF REMIT — not tenant data; nothing to scope",
    # The seventh class (SPEC-003 U6). "No row filter" is the *mechanism* here, and it is declared
    # in `query_scope.UNFILTERED_CLASSES` with its reason rather than being the absence of a code
    # branch — which is the distinction U7 was about. Enforcement is the route declarations:
    # `task.manage` to list and run, `automation.manage` to create and delete.
    EntityClass.ACCOUNT_SHARED: (
        "NO ROW FILTER BY DESIGN — query_scope.UNFILTERED_CLASSES; access is a verb distinction "
        "carried by the route declarations (task.manage to run, automation.manage to manage)"
    ),
}


def test_every_entity_class_has_a_named_mechanism():
    """Every one of the six classes says, in writing, how it is enforced — or that it is not.

    This is the test the group exists for. It does not assert that enforcement is *adequate*; it
    asserts that the question has been **answered for every class**, so a class enforced by
    nothing is visible in the table rather than discovered by a probe two phases later.
    """
    missing = sorted(c.value for c in EntityClass if c not in CLASS_MECHANISM)
    assert not missing, (
        f"these entity classes have no documented enforcement mechanism: {missing}. A class the "
        "code does not read is a classification with nothing behind it — say so explicitly."
    )


def test_no_class_is_left_to_route_declaration_alone():
    """Pins the census. **This test's expected value changed at U7, and that is the point of it.**

    At G17 it asserted `{ACCOUNT_LEVEL, PERSONNEL}` — the two classes enforced by nothing but a
    route's declaration. Both now have query-layer criteria, so the expected set is empty, and
    this test is what would notice a class quietly losing its mechanism again.

    Why "route declaration alone" is not enough, stated once here rather than rediscovered: a
    route declaration is a statement about *who may call an endpoint*, and a class is a statement
    about *which rows a person may see*. They coincide only when every route touching the class
    happens to declare an action staff lack. `/search/` and `/vendors/` are the counter-examples —
    both declare actions staff legitimately hold, and both read an `ACCOUNT_LEVEL` model through a
    service. Neither was a route mistake; the class simply had no teeth.

    `FLAGGED` and `PROPERTY_LINKED` are still reached by *model name* rather than by class, which
    counts — the mechanism exists — but is worth knowing, because a newly-added model in either
    class would not inherit it. `ACCOUNT_LEVEL` and `PERSONNEL` are derived from the
    classification and so do not have that weakness.
    """
    unenforced = {c for c, how in CLASS_MECHANISM.items() if how.startswith("ROUTE DECLARATION")}
    assert unenforced == set(), (
        "a class is back to being enforced by route declaration alone. That is a regression U7 "
        "closed: four leaks came from classifications nothing read. "
        f"Now: {sorted(c.value for c in unenforced)}"
    )


def test_the_enforced_classes_name_the_code_that_enforces_them():
    """A mechanism string is only worth having if it points at something real.

    `CLASS_MECHANISM` is prose, and prose drifts — `redact.py` carried a comment claiming
    `ACCOUNT_LEVEL` denied staff a row for an entire phase while it denied nothing. So the two
    classes U7 added are checked against the functions they name.
    """
    from mihomes.authz import query_scope

    assert callable(query_scope._account_level_criteria)
    assert callable(query_scope._personnel_criteria)
    assert "_account_level_criteria" in CLASS_MECHANISM[EntityClass.ACCOUNT_LEVEL]
    assert "_personnel_criteria" in CLASS_MECHANISM[EntityClass.PERSONNEL]


def test_property_scoped_models_are_enforced_or_declared():
    """Derived from `ENTITY_CLASSES` — so a new model fails closed instead of joining the leak.

    Every `PROPERTY_SCOPED` model must be either reachable by `scoped_models()` or listed in
    `NOT_YET_ENFORCEABLE` with a reason. Adding a property-scoped child table without a
    `property_id` and without an entry here turns this red, which is the whole point: the previous
    state of the world was that such a model was silently unfiltered.
    """
    enforced = {model.__name__ for model, _ in query_scope.scoped_models()}
    classified = {
        model.__name__
        for model, cls in ENTITY_CLASSES.items()
        if cls is EntityClass.PROPERTY_SCOPED
    }
    gap = sorted(classified - enforced - set(NOT_YET_ENFORCEABLE))
    assert not gap, (
        f"these models are PROPERTY_SCOPED but no mechanism filters them, and they are not "
        f"declared in NOT_YET_ENFORCEABLE: {gap}. Either give them a scoping path or list them "
        "with a written reason — do not leave them silently unfiltered."
    )


def test_every_declared_exception_is_still_real():
    """A stale exception is as dangerous as a missing one — the SPEC-002 lesson, applied here.

    `EXPECTED_NON_LEADING` needed this test and G5 duly retired 16 entries by it. Same discipline:
    an entry for a model that no longer exists, or that has since gained enforcement, would wave
    through the next model to take its name.
    """
    all_model_names = {model.__name__ for model in ENTITY_CLASSES}
    unknown = sorted(set(_ALL_EXCEPTIONS) - all_model_names)
    assert not unknown, (
        f"these exception entries name models that are not classified at all: {unknown}"
    )

    enforced = {model.__name__ for model, _ in query_scope.scoped_models()}
    now_enforced = sorted(set(NOT_YET_ENFORCEABLE) & enforced)
    assert not now_enforced, (
        f"these models now HAVE a scoping mechanism — remove them from NOT_YET_ENFORCEABLE so "
        f"the list keeps its teeth: {now_enforced}"
    )


def test_every_exception_reason_is_substantive():
    """A reason short enough to be a shrug is not a reason.

    Same guard as `declares_session`'s 20-character floor, for the same reason: the discipline
    that keeps an exception list honest is that writing the entry costs something.
    """
    for name, reason in sorted(_ALL_EXCEPTIONS.items()):
        assert len(reason) >= 80, f"{name}'s exception reason is too thin to review: {reason!r}"


def test_the_two_exception_lists_are_disjoint():
    """One model, one problem. An entry in both means nobody knows which fix applies."""
    both = sorted(set(NOT_YET_ENFORCEABLE) & set(NO_CLASS_FITS))
    assert not both, (
        f"{both} appear in both exception lists. NOT_YET_ENFORCEABLE is 'class right, no "
        "mechanism'; NO_CLASS_FITS is 'class wrong, no right class exists'. They have different "
        "fixes and cannot both apply."
    )


# ======================================================================================
# 2. Reach — the probes that turned the census into two findings.
# ======================================================================================


@pytest.fixture
def two_estates(web_client_as):
    """Belle (in scope) and Blue (out of scope), each with distinguishable content.

    Distinguishable is the load-bearing word. Asserting that the in-scope row is *present* passes
    against no filtering at all; the assertion that matters is the **absence** of the out-of-scope
    one, which needs a needle that could only have come from Blue.
    """
    ids = {}

    def _seed(session):
        belle = Property(id=uuid.uuid4(), name="Belle Estate", slug=f"belle-{uuid.uuid4().hex[:6]}")
        blue = Property(id=uuid.uuid4(), name="Blue Room", slug=f"blue-{uuid.uuid4().hex[:6]}")
        session.add_all([belle, blue])
        session.flush()
        ids["belle"], ids["blue"] = belle.id, blue.id

        session.add(Book(id=uuid.uuid4(), title="BLUEBOOKNEEDLE",
                         slug=f"bk-{uuid.uuid4().hex[:8]}", property_id=blue.id))
        session.add(Book(id=uuid.uuid4(), title="BELLEBOOKNEEDLE",
                         slug=f"bk-{uuid.uuid4().hex[:8]}", property_id=belle.id))

        blue_asset = Asset(id=uuid.uuid4(), name="Blue asset", slug=f"as-{uuid.uuid4().hex[:8]}",
                           property_id=blue.id, asset_type=AssetType.EQUIPMENT)
        session.add(blue_asset)
        session.flush()
        session.add(PriceEntry(id=uuid.uuid4(), asset_id=blue_asset.id, price=4242,
                               date=datetime.date(2026, 1, 1)))
        session.add(Guest(id=uuid.uuid4(), name="BLUEGUESTNEEDLE",
                          slug=f"g-{uuid.uuid4().hex[:8]}"))

    web_client_as.seed(_seed)
    return ids


class TestBookReclassification:
    """The leak G17 found and closed, pinned so it cannot come back.

    `Book` moved `ACCOUNT_LEVEL` → `PROPERTY_SCOPED`. Reverting that classification turns
    `test_library_scoped_for_staff` red — verified by mutation, because a security test that
    cannot fail is not a gate.
    """

    def test_library_scoped_for_staff(self, web_client_as, two_estates):
        """A staff member scoped to Belle must not see Blue's books on the all-properties page.

        `/library/` is the whole-estate book listing, which is exactly why it was the leak: it is
        *designed* to cross properties, so it had no per-property filter of its own and relied
        entirely on the query layer — which `ACCOUNT_LEVEL` never engaged.
        """
        client = web_client_as("staff", scoped_to=[two_estates["belle"]])
        body = client.get("/library/").text

        assert "BLUEBOOKNEEDLE" not in body, (
            "a scoped staff member is seeing an out-of-scope property's book. This is the G17 "
            "leak: Book must be PROPERTY_SCOPED for query_scope to filter it."
        )
        assert "BELLEBOOKNEEDLE" in body, (
            "in-scope books vanished — the fix over-corrected into denying staff their own "
            "property's library, which no rule asks for"
        )

    def test_owner_still_sees_the_whole_library(self, web_client_as, two_estates):
        """The regression guard. Scoping that also constrained owners would 'pass' the test above
        while breaking the feature for the person who owns the estate."""
        client = web_client_as("owner")
        body = client.get("/library/").text
        assert "BLUEBOOKNEEDLE" in body
        assert "BELLEBOOKNEEDLE" in body

    def test_book_is_classified_property_scoped(self):
        """Pins the classification itself, so the reason survives without the route test.

        If `/library/` is ever rewritten with its own filter, the route test could pass while the
        classification silently reverted — leaving every *other* Book query unfiltered.
        """
        assert ENTITY_CLASSES[Book] is EntityClass.PROPERTY_SCOPED, (
            "Book was reclassified by G17 because ACCOUNT_LEVEL has no query-layer enforcement "
            "and library.py declares inventory.manage (SCOPED for staff). Reverting this "
            "reopens the leak."
        )


class TestChildTablesAreReachable:
    """The `NOT_YET_ENFORCEABLE` entries, asserted as **currently true** rather than described.

    An exception list that merely *claims* a model is unreachable-by-the-mechanism is a comment.
    These tests prove the claim, so the day someone gives child tables a scoping path, they turn
    red and the list gets cleaned up instead of quietly outliving the problem.
    """

    def test_the_parent_is_filtered(self, web_client_as, two_estates):
        """Baseline — without this, the two tests below prove nothing.

        If `Asset` were *also* unfiltered the child results would say nothing about child tables
        specifically; they would just mean scoping is broken.
        """
        web_client_as("staff", scoped_to=[two_estates["belle"]])
        session = web_client_as.session_for_scope()
        with authz_context("staff", frozenset({two_estates["belle"]})):
            assert session.query(Asset).count() == 0, (
                "Asset carries property_id and must be filtered — if this fails, the whole "
                "query-scope layer is broken and the child-table findings are noise"
            )

    def test_price_entry_is_reachable_but_redacted(self, web_client_as, two_estates):
        """`PriceEntry`: row reachable, amount stripped. Both halves asserted."""
        from mihomes.authz.redact import REDACTED_FIELDS

        web_client_as("staff", scoped_to=[two_estates["belle"]])
        session = web_client_as.session_for_scope()
        with authz_context("staff", frozenset({two_estates["belle"]})):
            assert session.query(PriceEntry).count() == 1, (
                "PriceEntry is expected to be REACHABLE (it has no property_id). If it is now "
                "filtered, remove it from NOT_YET_ENFORCEABLE."
            )
        assert "price" in REDACTED_FIELDS[PriceEntry], (
            "the mitigation that makes PriceEntry the milder finding is field-level redaction; "
            "without it the amount leaks too"
        )

    def test_guest_is_reachable_and_nothing_covers_it(self, web_client_as, two_estates):
        """`Guest`: the sharper finding. Reachable, and no redaction applies to a name."""
        from mihomes.authz.redact import REDACTED_FIELDS

        web_client_as("staff", scoped_to=[two_estates["belle"]])
        session = web_client_as.session_for_scope()
        with authz_context("staff", frozenset({two_estates["belle"]})):
            names = [g.name for g in session.query(Guest).all()]
        assert "BLUEGUESTNEEDLE" in names, (
            "Guest is expected to be REACHABLE. If it is now filtered, remove it from "
            "NOT_YET_ENFORCEABLE — this is good news, not a test failure to suppress."
        )
        assert Guest not in REDACTED_FIELDS, (
            "if Guest gained field redaction, the 'nothing covers it' half of the finding is "
            "stale and the exception reason must be rewritten"
        )


class TestPersonnelIsOwnRecordOnly:
    """§4.1's `PERSONNEL` rule, finally expressible — **this class asserted the gap until U6b.**

    It used to be `TestPersonnelIsDeniedNotLeaked`, and its subject was the fail-closed state G6
    shipped: `staff.py` declared `member.manage`, denied to staff, so a housekeeper could not read
    *any* HR record — their own included, which §4.1 says they may. Not a leak; stricter than the
    spec, and recorded as U6.

    Two pieces had to land before the rule could be enforced rather than over-enforced, and the
    order mattered. U6a added `staff.user_id`, because *"their own record"* needs a hard answer to
    which row is mine and `Staff.email` cannot give one. U7 gave `PERSONNEL` a query-layer
    mechanism, because without one a route that let staff in would let them read everyone. Only
    then does `staff.view_own` (row 10, ALLOW for all three roles) become safe: the grant opens the
    page and the query layer narrows it to one row.
    """

    def test_staff_reach_the_hr_page(self, web_client_as, two_estates):
        """403 → 200. The grant changed, so this assertion had to change with it."""
        client = web_client_as("staff", scoped_to=[two_estates["belle"]])
        response = client.get("/staff/")
        assert response.status_code == 200, (
            f"expected 200 — staff.view_own is ALLOW for staff (U6b) — got "
            f"{response.status_code}. A 403 means the route is still declaring member.manage."
        )

    def test_staff_see_only_their_own_row(self, web_client_as, two_estates):
        """The half that makes the 200 above safe rather than a regression.

        A test asserting only reachability would pass against a route that hands a housekeeper the
        entire directory — which is precisely what `member.manage` was protecting against, and why
        opening the route without U7's filter would have been a leak rather than a fix.
        """
        from mihomes.models.staff import Staff

        client = web_client_as("staff", scoped_to=[two_estates["belle"]])

        def _seed(session):
            session.add(
                Staff(id=uuid.uuid4(), name="COLLEAGUENEEDLE",
                      slug=f"c-{uuid.uuid4().hex[:6]}")
            )

        web_client_as.seed(_seed)
        body = client.get("/staff/").text
        assert "COLLEAGUENEEDLE" not in body, (
            "a staff member read a colleague's HR record through /staff/. staff.view_own opens "
            "the page; query_scope._personnel_criteria is what must narrow it to their own row."
        )

    def test_the_grant_is_allow_and_the_narrowing_is_not_the_grant(self):
        """The teeth, and the design point in one assertion.

        `staff.view_own` must be `ALLOW`, not `SCOPED`: `SCOPED` means "filtered to the properties
        in your scope", and an HR record has nothing to do with which homes someone covers.
        `test_scoped_grants_are_never_declared_on_account_routes` forbids `SCOPED` on an `ACCOUNT`
        route anyway, correctly — there is no property target to scope by. So if the row-narrowing
        ever moves *into* the grant, that is a design regression even if the tests above still
        pass.
        """
        spec = MATRIX["staff.view_own"]
        assert spec.staff is Grant.ALLOW
        assert spec.access is Access.ACCOUNT
        assert MATRIX["member.manage"].staff is Grant.DENY, (
            "the write routes still declare member.manage; if that becomes ALLOW or SCOPED, "
            "staff can edit the roster"
        )

    def test_own_record_visibility_is_no_longer_a_gap(self):
        """The retired U-gate. This test asserted `== ["gateway.link_self"]` until U6b."""
        keys_mentioning_self = sorted(k for k in MATRIX if "own" in k or "self" in k)
        assert keys_mentioning_self == ["gateway.link_self", "staff.view_own"], (
            f"expected the two self-scoped keys; found {keys_mentioning_self}"
        )


class TestTemplatesAreRunnableButNotManageable:
    """U6b's row-5 split. **The interesting part is what it does NOT do.**

    G6 declared every `/templates/` route `task.manage`, which is `SCOPED` for staff — so a
    housekeeper could create and delete the templates driving everyone's work. Row 5 never meant
    that; it meant task management.

    The plan was to give templates their own key and then let U7's query layer deny the rows
    outright. **Writing it showed that to be wrong**, and the reason is worth stating:
    `template_service.run_template` resolves the template *by slug*, so running one requires
    reading the row. Denying rows would leave staff a `/run` endpoint they can call but whose
    targets they cannot see — a capability in name only, which the matrix deliberately grants.

    So the split is by **verb, not by row**: `automation.manage` (DENY for staff) governs create
    and delete; listing and running stay `task.manage`. `Template`/`TemplateItem` therefore remain
    in both `query_scope._ACCOUNT_LEVEL_EXEMPT` and `NO_CLASS_FITS` — U6b removed the unintended
    *write* access, and left the misclassification exactly where `NO_CLASS_FITS` already diagnosed
    it. A verb distinction belongs in a route declaration; the query layer can only express row
    visibility, and row visibility was never the rule here.
    """

    def test_staff_can_still_reach_the_template_list(self, web_client_as, two_estates):
        client = web_client_as("staff", scoped_to=[two_estates["belle"]])
        assert client.get("/templates/").status_code == 200, (
            "staff can no longer list templates, so they cannot pick one to run — "
            "`/{slug}/run` becomes unreachable in practice"
        )

    def test_staff_cannot_create_a_template(self, web_client_as, two_estates):
        client = web_client_as("staff", scoped_to=[two_estates["belle"]])
        response = client.post("/templates/", data={"name": "Staff made this"})
        assert response.status_code == 403, (
            f"expected 403 from automation.manage (DENY for staff), got {response.status_code}"
        )

    def test_staff_cannot_delete_a_template(self, web_client_as, two_estates):
        from mihomes.models.template import Template

        slug = f"tmpl-{uuid.uuid4().hex[:6]}"

        def _seed(session):
            session.add(Template(id=uuid.uuid4(), name="Spring open-up", slug=slug))

        web_client_as.seed(_seed)
        client = web_client_as("staff", scoped_to=[two_estates["belle"]])
        assert client.post(f"/templates/{slug}/delete").status_code == 403

    def test_running_a_template_is_still_task_work(self):
        """The declaration itself, so the verb split cannot drift without a test noticing."""
        import inspect as _i

        from mihomes.web.routes import templates_route

        assert declared_action(templates_route.run_template) == ("task.manage", Access.ITEM)
        assert declared_action(templates_route.create_template)[0] == "automation.manage"
        assert declared_action(templates_route.delete_template)[0] == "automation.manage"
        # And the reason the list route keeps task.manage is recorded where a reader will find it.
        assert "requires reading the row" in _i.getdoc(templates_route.list_templates)

    def test_the_template_exemptions_are_gone(self):
        """**The inverse of what this test asserted at U6b**, and the change is SPEC-003 U6's close.

        Both entries existed only to neutralise a misclassification: `ACCOUNT_LEVEL` said "✗ for
        staff" while row 5 granted staff the rows, so the exemption list carried the contradiction.
        `ACCOUNT_SHARED` names what these models actually are, and both crutches retire with it.

        Asserting their *absence* matters as much as asserting their presence did. An exemption
        list holding only structural "the filter would be circular here" cases is a different thing
        from one that also absorbs "the class is wrong here" cases — and the second kind grows
        quietly, because each new entry looks like the last one.
        """
        assert "Template" not in query_scope._ACCOUNT_LEVEL_EXEMPT
        assert "TemplateItem" not in query_scope._ACCOUNT_LEVEL_EXEMPT
        assert NO_CLASS_FITS == {}, (
            f"NO_CLASS_FITS gained an entry: {sorted(NO_CLASS_FITS)}. A model whose class does not "
            "fit is spec work — record it here, but do not let an exemption list absorb it."
        )


class TestAccountLevelReachIsDeclaredOrDenied:
    """`ACCOUNT_LEVEL` is enforced only by route declaration — so pin the declarations.

    This is the class the Book leak came from, and the one where a new model is most likely to
    leak next: classifying something `ACCOUNT_LEVEL` feels like protecting it, and protects
    nothing on its own.
    """

    def test_no_account_level_model_is_read_by_a_staff_allowed_route(self):
        """The generalisation of the Book leak, as a static check over every route.

        Book was `ACCOUNT_LEVEL` behind an `inventory.manage` (staff-`SCOPED`) route and nothing
        compared the two. This walks every declared route and flags any `ACCOUNT_LEVEL` model
        **read** by a route staff are granted.

        **Read, not merely mentioned** — this test's first version scanned the whole *module* for
        `import <Model>`, and flagged `/ai/ask` for *writing* a conversation row, which is the
        assistant working exactly as designed. Module granularity cannot tell a route that serves
        a model's rows from one that appends to them, and a gate that cannot tell them apart is
        one somebody eventually silences. So the scan is per-endpoint and looks for a **query**:
        `db.query(Model)` or `select(Model)`. Writes do not leak; reads do.

        Residual, stated rather than discovered: a route that reads through a *service* function
        rather than inline is invisible here — which is exactly how `/library/` hid, since it
        called `book_svc.list_books`. The runtime probes above are what cover that shape. This
        static arm catches the inline case cheaply and across every route at once; neither arm
        subsumes the other.
        """
        from mihomes.web import app as app_module

        account_level = {
            model.__name__
            for model, cls in ENTITY_CLASSES.items()
            if cls is EntityClass.ACCOUNT_LEVEL and model.__name__ not in NO_CLASS_FITS
        }
        staff_allowed = {key for key, spec in MATRIX.items() if spec.staff is not Grant.DENY}

        offenders = []
        application = app_module.create_app()
        for route in application.routes:
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None:
                continue
            declared = declared_action(endpoint)
            if declared is None or declared[0] not in staff_allowed:
                continue
            try:
                body = _inspect.getsource(endpoint)
            except (OSError, TypeError):  # pragma: no cover - builtins, C funcs
                continue
            for model_name in sorted(account_level):
                if f"query({model_name}" in body or f"select({model_name}" in body:
                    offenders.append(f"{route.path} ({declared[0]}) reads {model_name}")

        assert not offenders, (
            "these routes are reachable by staff and READ an ACCOUNT_LEVEL model, which has no "
            "query-layer enforcement — the exact shape of the /library/ and /ai/sessions "
            "leaks:\n  "
            + "\n  ".join(sorted(set(offenders)))
            + "\nEither reclassify the model (if the class is wrong, as Book's was), change the "
            "declaration (as the four /ai/sessions routes did), or add it to NO_CLASS_FITS with "
            "a written reason."
        )

    def test_template_reach_is_the_declared_exception_and_still_true(
        self, web_client_as, two_estates
    ):
        """**Was an exception; is now the classification.** SPEC-003 U6's closing change.

        This test asserted `NO_CLASS_FITS` — that staff reach `/templates/` *despite* the class
        saying they should not. That was true and uncomfortable: it pinned a contradiction rather
        than a rule. `ACCOUNT_SHARED` resolves it, so the same reachability is now what the
        classification *predicts* instead of what it fails to prevent.

        Reachability is still asserted, and by the same probe. A class change that quietly took
        `/templates/` away from staff would break a capability row 5 grants, and this is what would
        notice.
        """
        def _seed(session):
            session.add(Template(id=uuid.uuid4(), name="TEMPLATENEEDLE",
                                 slug=f"t-{uuid.uuid4().hex[:8]}"))

        web_client_as.seed(_seed)
        client = web_client_as("staff", scoped_to=[two_estates["belle"]])
        body = client.get("/templates/").text

        assert "TEMPLATENEEDLE" in body, (
            "staff no longer reach /templates/. Row 5 grants running a template and "
            "`run_template` resolves by slug, so a staff member who cannot list templates cannot "
            "run one — the /run endpoint becomes unreachable in practice."
        )
        assert ENTITY_CLASSES[Template] is EntityClass.ACCOUNT_SHARED, (
            "Template's classification changed. ACCOUNT_SHARED is what makes the reachability "
            "above correct rather than exceptional; moving it back to ACCOUNT_LEVEL would reopen "
            "the contradiction NO_CLASS_FITS existed to record."
        )


class TestAITranscriptStore:
    """The second leak G17 found: reading someone else's answer is not "using the assistant".

    `AIConversation` is `ACCOUNT_LEVEL` — correctly, it has no `property_id` — and the four
    `/ai/sessions*` routes declared `ai.use`, which row 18 grants staff as `SCOPED`. So a scoped
    housekeeper could fetch an owner's saved conversation verbatim, financial answers included.

    **This is the leak G10 structurally could not see.** G10 scoped the *live* AI path: it proved
    a staff member asking about another property gets nothing. The transcript of a question an
    *owner* already asked is a stored row on a different route, and no amount of scoping the
    question reaches it. Two surfaces onto the same data, one of them scoped.
    """

    def test_staff_cannot_read_a_saved_conversation(self, web_client_as, two_estates):
        from mihomes.models.ai_conversation import AIConversation

        def _seed(session):
            session.add(AIConversation(
                id=uuid.uuid4(), session_id="owner-private",
                session_name="Owner budget review", role="financial",
                user_message="What is our total spend across all properties?",
                ai_response="TOTALSPENDNEEDLE is 4.2 million euros.",
            ))

        web_client_as.seed(_seed)
        client = web_client_as("staff", scoped_to=[two_estates["belle"]])
        response = client.get("/ai/sessions/owner-private")

        assert response.status_code == 403, (
            f"expected 403 (audit.view is denied to staff), got {response.status_code}"
        )
        assert "TOTALSPENDNEEDLE" not in response.text, (
            "the owner's saved financial answer reached a staff member — this is the G17 "
            "transcript leak and the reason these four routes no longer declare ai.use"
        )

    def test_the_denial_comes_from_the_grant_we_think(self):
        """Teeth. A 403 that came from somewhere else would pass the test above for free."""
        assert MATRIX["audit.view"].staff is Grant.DENY, (
            "the /ai/sessions* routes rely on audit.view being denied to staff; if that grant "
            "changes, the transcript store reopens"
        )
        assert MATRIX["ai.use"].staff is Grant.SCOPED, (
            "ai.use must stay SCOPED for staff — the point of the fix was to move the four "
            "transcript routes off it, NOT to deny staff the assistant"
        )

    def test_staff_may_still_use_the_assistant(self, web_client_as, two_estates):
        """The over-correction guard, and the reason the fix is four routes and not a module.

        `/library/` taught this: a fix that denies more than the leak is its own regression.
        """
        client = web_client_as("staff", scoped_to=[two_estates["belle"]])
        assert client.get("/ai/ask").status_code != 403, (
            "staff lost access to the assistant itself — row 18 grants it, and D14's logic "
            "rejects removing capability to achieve confidentiality"
        )

    def test_owner_can_still_read_transcripts(self, web_client_as, two_estates):
        """Without this, a route that 500s or 404s would read as a successful denial."""
        from mihomes.models.ai_conversation import AIConversation

        def _seed(session):
            session.add(AIConversation(
                id=uuid.uuid4(), session_id="owner-readable", role="financial",
                user_message="Q", ai_response="OWNERVISIBLENEEDLE",
            ))

        web_client_as.seed(_seed)
        client = web_client_as("owner")
        response = client.get("/ai/sessions/owner-readable")

        assert response.status_code == 200, (
            f"the owner cannot read their own transcript ({response.status_code}) — the denial "
            "above may be a broken route rather than an authorization decision"
        )
        assert "OWNERVISIBLENEEDLE" in response.text


# `TestKnownBrokenCells` lived here, asserting that `/ai/` and `/ai/sessions-panel` returned 500
# for **every** role — `func.min(AIConversation.id)`, and Postgres has no `min(uuid)`. It existed
# because a 500 is indistinguishable from a denial at the HTTP layer, so this file must never book
# one as enforcement.
#
# **The bug is fixed, so the test is gone**, exactly as its own docstring instructed. Its
# replacement is `tests/integration/test_ai_sessions.py`, which asserts the page returns 200 and
# groups correctly. The lesson it encoded is worth keeping even without a cell to apply it to: a
# non-200 proves nothing about authorization until you know *why* it is not 200.
