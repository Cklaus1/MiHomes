"""G16 · §6 Step 16 — the three feature gates (A22, A23, A24).

**A22 is two requirements in one sentence**: *"Free denies ratings at every F6 surface **and** both
dashboard pages still load."* The conjunction is the criterion. A gate on the route would satisfy
the first half completely and break the second — which is exactly what N11 forbids:

> Do not 403 the dashboard to enforce the ratings gate. `dashboard.html` and `property_detail.html`
> render ratings but must remain loadable on Free. Gate at context assembly, not at the route.

So the gate returns empty data rather than raising, and the page renders without the panel. A test
that only checked "Free gets no ratings" would pass against a 403 and miss the whole point.

**A24 is a negative**: the Telegram path must be *unaffected*. F5 measured why — `responder.py`
passes no `due_date`, so the gate never fires there. Asserted rather than assumed, because the
statement that makes it safe is a fact about someone else's code that could change.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mihomes.models.account import Account
from mihomes.services import vendor as vendor_svc
from mihomes.services.property import EntitlementError, create_property


@pytest.fixture
def free_account(session, account_a) -> Account:
    account = session.get(Account, account_a)
    account.plan = "free"
    account.subscription_status = None
    session.commit()
    return account


@pytest.fixture
def pro_account(session, account_a) -> Account:
    account = session.get(Account, account_a)
    account.plan = "pro"
    account.subscription_status = "active"
    session.commit()
    return account


@pytest.fixture
def a_rated_vendor(session):
    """A vendor with one rating, so "no ratings" is a gate rather than an empty table."""
    from mihomes.models.vendor_rating import VendorRating

    vendor = vendor_svc.create_vendor(session, "Acme Pest", service_categories=["Pest"])
    session.add(
        VendorRating(
            vendor_id=vendor.id,
            quality_score=5,
            reliability_score=4,
            cost_score=4,
            communication_score=5,
            overall_score=4.5,
            rated_date=datetime.now(UTC).date(),
        )
    )
    session.commit()
    return vendor


class TestRatingsAreGated:
    def test_free_gets_no_ratings(self, session, free_account, a_rated_vendor):
        """D12 — `vendor_ratings` is `false` on Free.

        SPEC-003's N8 forbade enforcing this, reasoning that it would *"delete working
        functionality from every user"*. D12 supersedes it: there are no hosted users to
        grandfather, so it is a pricing question the PRD already answered.
        """
        result = vendor_svc.get_vendor_ratings(session, a_rated_vendor.slug)

        assert result["ratings"] == []
        assert result["averages"] is None
        assert result["entitled"] is False

    def test_pro_gets_the_ratings(self, session, pro_account, a_rated_vendor):
        """**The control, and A22 is vacuous without it.**

        A gate that returned nothing on *every* plan would satisfy the Free assertion completely
        while making the feature Pro pays for invisible — which is a worse defect than the
        paywall being absent, because it fails silently for paying customers.
        """
        result = vendor_svc.get_vendor_ratings(session, a_rated_vendor.slug)

        assert len(result["ratings"]) == 1
        assert result["averages"] is not None

    def test_the_vendor_is_still_returned_on_free(self, session, free_account, a_rated_vendor):
        """N11 in miniature: the *vendor* is not the paid feature, the ratings are.

        Returning `None` for the vendor would break every page that renders a vendor alongside
        its ratings, which is the route-403 failure mode arriving through a different door.
        """
        result = vendor_svc.get_vendor_ratings(session, a_rated_vendor.slug)
        assert result["vendor"] is not None
        assert result["vendor"].slug == a_rated_vendor.slug

    def test_the_shape_is_identical_on_both_plans(self, session, account_a, a_rated_vendor):
        """Six templates render this. A caller that got a different *shape* on Free would need a
        branch at every one of them — and the first forgotten branch is a 500 on a page the gate
        exists to keep loading."""
        account = session.get(Account, account_a)

        account.plan = "free"
        session.commit()
        free_keys = set(vendor_svc.get_vendor_ratings(session, a_rated_vendor.slug))

        account.plan = "pro"
        account.subscription_status = "active"
        session.commit()
        pro_keys = set(vendor_svc.get_vendor_ratings(session, a_rated_vendor.slug))

        assert free_keys >= pro_keys - {"entitled"} or pro_keys <= free_keys


class TestBothDashboardPagesStillLoad:
    def test_ratings_gated_pages_load(self, web_client_as, _pg_engine, account_a):
        """**A22's second half, and the one N11 is about.**

        The dashboard and vendors page both render ratings among many other things. Gating at the
        route would 403 the whole page to withhold one panel — a worse product than the paywall
        it enforces, and the specific mistake N11 names.

        **A vendor is seeded through the client's own session, and the first version of this test
        did not do that.** `web_client_as` runs on a different connection from the `session`
        fixture, so a vendor created there is invisible to the request — `/vendors/` built an
        empty ratings dict, the comprehension never called the gated function, and the test
        passed *vacuously*. Mutating the gate to raise proved it: three other tests went red and
        this one stayed green, which is precisely the false-green shape it exists to catch.
        """
        from sqlalchemy import text
        from sqlalchemy.orm import Session as OrmSession

        from mihomes.models.vendor_rating import VendorRating
        from mihomes.services import vendor as vs
        from mihomes.tenancy import account_context

        client = web_client_as("owner")

        # Committed on the engine, not the rolled-back `session` fixture, so the request's own
        # connection can see it. `account_context` because the insert stamps `account_id`.
        #
        # **The plan is set here too**, and that is the third thing this test needed before it
        # meant anything: `DEFAULT_FIXTURE_PLAN` is `estate` (Step 8), so the client's account is
        # *entitled* by default and the gate would never fire no matter what it did.
        with account_context(account_a), OrmSession(_pg_engine) as db:
            db.execute(
                text("UPDATE accounts SET plan = 'free', subscription_status = NULL "
                     "WHERE id = :id"),
                {"id": account_a},
            )
            vendor = vs.create_vendor(db, "Rated Co", service_categories=["Pest"])
            db.add(
                VendorRating(
                    vendor_id=vendor.id, quality_score=5, reliability_score=4,
                    cost_score=4, communication_score=5, overall_score=4.5,
                    rated_date=datetime.now(UTC).date(),
                )
            )
            db.commit()
            seeded_vendor_id = vendor.id

        for path in ("/", "/vendors/"):
            response = client.get(path)
            assert response.status_code == 200, (
                f"{path} must load on Free — N11 forbids 403-ing a page to withhold a panel "
                f"(got {response.status_code})"
            )

        # **The guard that makes the assertion above non-vacuous.** The gated function only runs
        # if the request actually sees a vendor; without this, an empty vendor list would make
        # `/vendors/` build an empty ratings dict, never call the gate, and pass regardless of
        # what the gate does. Confirmed necessary by mutation: raising in the gate left this test
        # green until the seed was proven to arrive.
        assert "Rated Co" in client.get("/vendors/").text, (
            "the seeded vendor must reach the request, or this test proves nothing about the gate"
        )

        # Clean up: committed outside the rolled-back fixture, so it would leak into other tests.
        with account_context(account_a), OrmSession(_pg_engine) as db:
            db.query(VendorRating).filter(
                VendorRating.vendor_id == seeded_vendor_id
            ).delete()
            from mihomes.models.vendor import Vendor

            db.query(Vendor).filter(Vendor.id == seeded_vendor_id).delete()
            db.execute(
                text("UPDATE accounts SET plan = 'estate', subscription_status = 'active' "
                     "WHERE id = :id"),
                {"id": account_a},
            )
            db.commit()


class TestTheDeadModuleIsGatedToo:
    @pytest.mark.parametrize("func_name", ["get_vendor_scores", "compare_vendors"])
    def test_the_zero_caller_functions_refuse_on_free(self, session, free_account,
                                                      a_rated_vendor, func_name):
        """F6 — gate the dead module *"so a future caller inherits it"*.

        All three functions here have zero callers today. A module left ungated because nothing
        calls it is a paywall with a scheduled expiry date: the day someone wires up a comparison
        screen, the hole reopens silently.

        These **raise** rather than returning empty, unlike the read path — a caller explicitly
        asking to compare vendors has no partial answer to render, and silently doing nothing
        would be worse than a clear refusal.
        """
        from mihomes.services import vendor_rating as vr

        args = ([a_rated_vendor.slug],) if func_name == "compare_vendors" else (a_rated_vendor.slug,)
        with pytest.raises(EntitlementError):
            getattr(vr, func_name)(session, *args)

    def test_create_rating_refuses_on_free(self, session, free_account, a_rated_vendor):
        from mihomes.services import vendor_rating as vr

        with pytest.raises(EntitlementError):
            vr.create_rating(session, a_rated_vendor.slug, 5, 5, 5, 5)


class TestDueDateGate:
    def test_due_date_gate(self, session, free_account):
        """**A23** — `due_date` is denied on Free; an **undated** work order succeeds.

        Both halves. D13 scopes `work_order_scheduling` to exactly this one capability, so a gate
        that refused work orders outright would be enforcing a key the PRD does not sell — and
        would break the ordinary "log a job" flow for every Free user.
        """
        from mihomes.services.work_order import create_work_order

        prop = create_property(session, "Gate House")

        undated = create_work_order(session, "Fix the gate", prop.slug)
        assert undated.due_date is None

        with pytest.raises(EntitlementError) as exc:
            create_work_order(
                session, "Fix it by Friday", prop.slug,
                due_date=datetime(2026, 9, 1, tzinfo=UTC),
            )
        assert exc.value.decision.upgrade_target is not None
        assert "without a due date" in exc.value.decision.reason, (
            "the refusal must name the thing that still works — otherwise the user reads it as "
            "'work orders are a paid feature'"
        )

    def test_pro_may_set_a_due_date(self, session, pro_account):
        """The control: the gate is about the plan, not about due dates being unsupported."""
        from mihomes.services.work_order import create_work_order

        prop = create_property(session, "Pro House")
        wo = create_work_order(
            session, "Scheduled job", prop.slug,
            due_date=datetime(2026, 9, 1, tzinfo=UTC),
        )
        assert wo.due_date is not None

    def test_an_edit_that_does_not_touch_the_due_date_is_allowed(self, session, free_account):
        """Editing a work order is not scheduling.

        A gate on `update_work_order` as a whole would make every Free user unable to rename a
        job — enforcing far more than `PRICING:27` sells, which is the same over-reach D13
        rejects for `Appointment`.
        """
        from mihomes.services.work_order import create_work_order, update_work_order

        prop = create_property(session, "Edit House")
        wo = create_work_order(session, "Original", prop.slug)

        update_work_order(session, str(wo.id), title="Renamed")
        assert wo.title == "Renamed"


class TestTheBotPathIsUnaffected:
    def test_bot_path_ungated(self):
        """**A24** — the Telegram work-order path never trips the scheduling gate.

        F5 measured why: `responder.py` creates work orders and **passes no `due_date`**. That is
        a fact about someone else's code, so it is asserted rather than assumed — statically,
        because the alternative is standing up a bot conversation to prove a negative.

        N10 is the reason it matters: a gate that fired on a bot message would deny a user
        something they did not choose to do, and would present as the bot being broken rather
        than as an upgrade prompt.
        """
        import ast
        from pathlib import Path

        responder = (
            Path(__file__).resolve().parents[2]
            / "src" / "mihomes" / "services" / "gateways" / "telegram" / "responder.py"
        )
        tree = ast.parse(responder.read_text(encoding="utf-8"))

        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "create_work_order"
            and any(kw.arg == "due_date" for kw in node.keywords)
        ]
        assert not offenders, (
            "the Telegram responder must not pass due_date — doing so would put a plan gate on "
            f"a bot message (N10/A24). Lines: {offenders}"
        )
