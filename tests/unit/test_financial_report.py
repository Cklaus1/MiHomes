"""Tests for financial report service — spending analysis and forecasting."""

from datetime import date, timedelta

import pytest

from mihomes.models.budget import Transaction
from mihomes.models.property import Property, PropertyType
from mihomes.models.vendor import Vendor
from mihomes.services.financial_report import (
    forecast,
    property_comparison,
    spending_by_category,
    spending_by_vendor,
)


@pytest.fixture
def prop(session):
    p = Property(name="Report House", slug="report-house", property_type=PropertyType.PRIMARY, currency="USD")
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def vendor(session):
    v = Vendor(company_name="Fix-It Co", slug="fix-it-co")
    session.add(v)
    session.flush()
    return v


@pytest.fixture
def transactions(session, prop, vendor):
    today = date.today()
    txs = [
        Transaction(amount=500.0, currency="USD", property_id=prop.id, vendor_id=vendor.id,
                    category="plumbing", description="Pipe repair", date=today - timedelta(days=10)),
        Transaction(amount=200.0, currency="USD", property_id=prop.id, vendor_id=vendor.id,
                    category="plumbing", description="Faucet fix", date=today - timedelta(days=5)),
        Transaction(amount=300.0, currency="USD", property_id=prop.id,
                    category="electrical", description="Wiring", date=today - timedelta(days=3)),
    ]
    for tx in txs:
        session.add(tx)
    session.flush()
    return txs


class TestSpendingByVendor:
    def test_returns_vendor_totals(self, session, prop, transactions, vendor):
        start = date.today() - timedelta(days=30)
        end = date.today()
        results = spending_by_vendor(session, str(prop.id), start, end)
        assert len(results) == 2  # vendor + unassigned
        vendor_row = next(r for r in results if r["vendor"] == "Fix-It Co")
        assert vendor_row["total"] == 700.0
        assert vendor_row["transaction_count"] == 2

    def test_unassigned_vendor(self, session, prop, transactions):
        start = date.today() - timedelta(days=30)
        end = date.today()
        results = spending_by_vendor(session, str(prop.id), start, end)
        unassigned = next(r for r in results if r["vendor"] == "Unassigned")
        assert unassigned["total"] == 300.0

    def test_sorted_descending(self, session, prop, transactions):
        start = date.today() - timedelta(days=30)
        end = date.today()
        results = spending_by_vendor(session, str(prop.id), start, end)
        totals = [r["total"] for r in results]
        assert totals == sorted(totals, reverse=True)

    def test_date_filter_excludes_outside_range(self, session, prop, transactions):
        # Use a very narrow range that excludes all transactions
        start = date.today() - timedelta(days=1)
        end = date.today() - timedelta(days=1)
        results = spending_by_vendor(session, str(prop.id), start, end)
        assert results == []

    def test_lookup_by_slug(self, session, prop, transactions):
        start = date.today() - timedelta(days=30)
        end = date.today()
        results = spending_by_vendor(session, prop.slug, start, end)
        assert len(results) >= 1

    def test_empty_property_returns_empty(self, session):
        p2 = Property(name="Empty Property", slug="empty-property", property_type=PropertyType.PRIMARY, currency="USD")
        session.add(p2)
        session.flush()
        start = date.today() - timedelta(days=30)
        end = date.today()
        results = spending_by_vendor(session, str(p2.id), start, end)
        assert results == []


class TestSpendingByCategory:
    def test_groups_by_category(self, session, prop, transactions):
        start = date.today() - timedelta(days=30)
        end = date.today()
        results = spending_by_category(session, str(prop.id), start, end)
        assert len(results) == 2
        plumbing = next(r for r in results if r["category"] == "plumbing")
        assert plumbing["total"] == 700.0
        assert plumbing["transaction_count"] == 2

    def test_sorted_descending(self, session, prop, transactions):
        start = date.today() - timedelta(days=30)
        end = date.today()
        results = spending_by_category(session, str(prop.id), start, end)
        totals = [r["total"] for r in results]
        assert totals == sorted(totals, reverse=True)

    def test_electrical_category(self, session, prop, transactions):
        start = date.today() - timedelta(days=30)
        end = date.today()
        results = spending_by_category(session, str(prop.id), start, end)
        electrical = next(r for r in results if r["category"] == "electrical")
        assert electrical["total"] == 300.0

    def test_empty_returns_empty(self, session):
        p2 = Property(name="Empty Prop2", slug="empty-prop2", property_type=PropertyType.PRIMARY, currency="USD")
        session.add(p2)
        session.flush()
        start = date.today() - timedelta(days=30)
        end = date.today()
        assert spending_by_category(session, str(p2.id), start, end) == []


class TestForecast:
    def test_returns_structure(self, session, prop, transactions):
        result = forecast(session, str(prop.id))
        assert result["property"] == "Report House"
        assert result["property_slug"] == "report-house"
        assert result["forecast_months"] == 6
        assert "monthly_average" in result
        assert "forecast_total" in result
        assert "by_category" in result

    def test_forecast_total_is_monthly_avg_times_months(self, session, prop, transactions):
        result = forecast(session, str(prop.id), months=3)
        assert abs(result["forecast_total"] - result["monthly_average"] * 3) < 0.01

    def test_empty_property_zero_forecast(self, session):
        p2 = Property(name="Zero Prop", slug="zero-prop", property_type=PropertyType.PRIMARY, currency="USD")
        session.add(p2)
        session.flush()
        result = forecast(session, str(p2.id))
        assert result["monthly_average"] == 0.0
        assert result["forecast_total"] == 0.0
        assert result["by_category"] == []

    def test_custom_months(self, session, prop, transactions):
        r6 = forecast(session, str(prop.id), months=6)
        r12 = forecast(session, str(prop.id), months=12)
        assert r12["forecast_total"] == pytest.approx(r6["forecast_total"] * 2, rel=0.01)


class TestPropertyComparison:
    def test_compares_all_properties(self, session, prop, transactions):
        result = property_comparison(session, date.today() - timedelta(days=30), date.today())
        slugs = [r["property_slug"] for r in result]
        assert "report-house" in slugs

    def test_sorted_descending_by_spending(self, session, prop, transactions):
        # Add a second property with less spending
        p2 = Property(name="Small Property", slug="small-property", property_type=PropertyType.PRIMARY, currency="USD")
        session.add(p2)
        session.flush()
        session.add(Transaction(amount=50.0, currency="USD", property_id=p2.id,
                                category="misc", date=date.today()))
        session.flush()
        results = property_comparison(session, date.today() - timedelta(days=30), date.today())
        totals = [r["total_spending"] for r in results]
        assert totals == sorted(totals, reverse=True)

    def test_includes_transaction_count(self, session, prop, transactions):
        results = property_comparison(session, date.today() - timedelta(days=30), date.today())
        report_house = next(r for r in results if r["property_slug"] == "report-house")
        assert report_house["transaction_count"] == 3

    def test_zero_spending_property_included(self, session, prop):
        p2 = Property(name="No Spend", slug="no-spend", property_type=PropertyType.PRIMARY, currency="USD")
        session.add(p2)
        session.flush()
        results = property_comparison(session, date.today() - timedelta(days=30), date.today())
        no_spend = next((r for r in results if r["property_slug"] == "no-spend"), None)
        assert no_spend is not None
        assert no_spend["total_spending"] == 0.0


class TestVendorSpendingNoDoubleCount:
    """H15 — completing a work order books a `source='work_order'` transaction.
    The vendor spending report must not count both that transaction leg AND the
    work order's actual_cost, or every WO-driven expense is doubled."""

    def test_no_double_count(self, session, prop, vendor):
        from mihomes.services.financial_report import vendor_spending_report
        from mihomes.services.work_order import create_work_order, approve, complete

        wo = create_work_order(
            session, "Boiler swap", str(prop.id),
            vendor_id_or_slug=str(vendor.id), estimated_cost=1000.0,
        )
        approve(session, str(wo.id))
        # complete() sets actual_cost AND books a source='work_order' transaction
        complete(session, str(wo.id), actual_cost=1200.0)
        session.flush()

        rows = vendor_spending_report(
            session, date.today() - timedelta(days=1), date.today() + timedelta(days=1),
        )
        row = next(r for r in rows if r["vendor_id"] == vendor.id)
        # The completed work should be counted exactly once (1200), not 2400.
        assert row["combined_total"] == pytest.approx(1200.0), (
            f"double-counted WO expense: combined_total={row['combined_total']}"
        )

    def test_wo_leg_filtered_by_completion_date(self, session, prop, vendor):
        """A work order completed outside the window must not appear even if its
        row was touched (updated_at) inside the window."""
        from mihomes.services.financial_report import vendor_spending_report
        from mihomes.services.work_order import create_work_order, approve, complete

        wo = create_work_order(
            session, "Old job", str(prop.id),
            vendor_id_or_slug=str(vendor.id), estimated_cost=300.0,
        )
        approve(session, str(wo.id))
        complete(session, str(wo.id), actual_cost=300.0)
        session.flush()

        # Window entirely in the future — nothing completed then.
        rows = vendor_spending_report(
            session, date.today() + timedelta(days=10), date.today() + timedelta(days=20),
        )
        assert all(r["vendor_id"] != vendor.id or r["combined_total"] == 0.0 for r in rows)
