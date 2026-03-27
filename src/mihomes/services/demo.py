"""Demo data — sample properties, staff, vendors, tasks, issues, budgets."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import staff as staff_svc
from mihomes.services import vendor as vendor_svc
from mihomes.services import task as task_svc
from mihomes.services import issue as issue_svc
from mihomes.services import budget as budget_svc
from mihomes.services import note as note_svc
from mihomes.models.property import PropertyType, PropertyStatus
from mihomes.models.staff import StaffRole
from mihomes.models.task import TaskPriority, RecurrenceFrequency
from mihomes.models.issue import IssueSeverity
from mihomes.models.budget import BudgetPeriod


def load_demo_data(session: Session) -> None:
    """Load sample data for exploring MiHomes."""
    today = date.today()

    # === Properties ===
    beach = prop_svc.create_property(
        session, "Beach House", address="123 Ocean Drive, Malibu, CA",
        property_type=PropertyType.VACATION, climate_zone="southeast", sqft=3200,
    )
    mountain = prop_svc.create_property(
        session, "Mountain Lodge", address="456 Pine Ridge Rd, Aspen, CO",
        property_type=PropertyType.SEASONAL, climate_zone="mountain",
        status=PropertyStatus.CLOSED, sqft=4500,
    )
    city = prop_svc.create_property(
        session, "City Apartment", address="789 Park Ave, New York, NY",
        property_type=PropertyType.PRIMARY, climate_zone="northeast", sqft=1800,
    )

    # === Spaces ===
    space_svc.create_space(session, "Master Bedroom", str(beach.id), space_type="bedroom")
    space_svc.create_space(session, "Kitchen", str(beach.id), space_type="kitchen")
    space_svc.create_space(session, "Pool Area", str(beach.id), space_type="outdoor")
    space_svc.create_space(session, "Guest Suite", str(beach.id), space_type="bedroom")

    space_svc.create_space(session, "Great Room", str(mountain.id), space_type="living")
    space_svc.create_space(session, "Ski Locker", str(mountain.id), space_type="storage")
    space_svc.create_space(session, "Hot Tub Deck", str(mountain.id), space_type="outdoor")

    space_svc.create_space(session, "Living Room", str(city.id), space_type="living")
    space_svc.create_space(session, "Home Office", str(city.id), space_type="office")

    # === Staff ===
    sarah = staff_svc.create_staff(
        session, "Sarah Chen", role=StaffRole.HOUSEKEEPER,
        phone="555-0101", whatsapp_phone="+15550101",
        property_id_or_slug=str(beach.id),
    )
    staff_svc.assign_to_property(session, str(sarah.id), str(city.id))

    marco = staff_svc.create_staff(
        session, "Marco Rivera", role=StaffRole.GROUNDSKEEPER,
        phone="555-0102", whatsapp_phone="+15550102",
        property_id_or_slug=str(beach.id),
    )

    lisa = staff_svc.create_staff(
        session, "Lisa Park", role=StaffRole.PROPERTY_MANAGER,
        phone="555-0103", email="lisa@example.com",
        property_id_or_slug=str(mountain.id),
    )

    # === Vendors ===
    vendor_svc.create_vendor(
        session, "Coastal Plumbing", contact_name="Mike Torres",
        phone="555-0201", service_categories=["plumbing"],
        service_areas=["Malibu", "Santa Monica"],
    )
    vendor_svc.create_vendor(
        session, "Peak Landscaping", contact_name="Jim Walsh",
        phone="555-0202", service_categories=["landscaping", "snow-removal"],
        service_areas=["Aspen", "Snowmass"],
    )
    vendor_svc.create_vendor(
        session, "Metro HVAC", contact_name="Ana Ruiz",
        phone="555-0203", service_categories=["hvac", "electrical"],
        service_areas=["Manhattan", "Brooklyn"],
    )

    # === Tasks ===
    # Overdue tasks (for dashboard interest)
    task_svc.create_task(
        session, "HVAC filter change", str(beach.id),
        priority=TaskPriority.MEDIUM, due_date=today - timedelta(days=5),
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marco.id),
    )
    task_svc.create_task(
        session, "Pool chemical test", str(beach.id),
        priority=TaskPriority.HIGH, due_date=today - timedelta(days=2),
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marco.id),
    )

    # Upcoming tasks
    task_svc.create_task(
        session, "Deep clean kitchen", str(beach.id),
        priority=TaskPriority.MEDIUM, due_date=today + timedelta(days=3),
        assignee_id_or_slug=str(sarah.id),
    )
    task_svc.create_task(
        session, "Gutter cleaning", str(city.id),
        priority=TaskPriority.LOW, due_date=today + timedelta(days=7),
        recurrence=RecurrenceFrequency.QUARTERLY,
    )
    task_svc.create_task(
        session, "Lawn service", str(beach.id),
        priority=TaskPriority.MEDIUM, due_date=today + timedelta(days=5),
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marco.id),
    )

    # Seasonal task
    task_svc.create_task(
        session, "Winterize mountain lodge", str(mountain.id),
        priority=TaskPriority.HIGH, due_date=today + timedelta(days=30),
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="fall",
        assignee_id_or_slug=str(lisa.id),
    )

    # === Issues ===
    issue_svc.create_issue(
        session, "Roof leak in guest bathroom", str(beach.id),
        severity=IssueSeverity.HIGH,
        description="Water stain appearing on ceiling after rain. Needs inspection.",
    )
    issue_svc.create_issue(
        session, "Squeaky front door", str(city.id),
        severity=IssueSeverity.LOW,
        description="Hinge needs oiling.",
    )
    issue_svc.create_issue(
        session, "Fence panel damaged", str(beach.id),
        severity=IssueSeverity.MEDIUM,
        description="Wind damage to south fence. One panel loose.",
    )

    # A resolved issue
    resolved = issue_svc.create_issue(
        session, "Broken light fixture", str(city.id),
        severity=IssueSeverity.MEDIUM,
    )
    issue_svc.resolve_issue(session, str(resolved.id), notes="Replaced with LED fixture")

    # === Budgets ===
    year_start = date(today.year, 1, 1)
    budget_svc.set_budget(session, str(beach.id), "maintenance", BudgetPeriod.ANNUAL, 25000, year_start)
    budget_svc.set_budget(session, str(beach.id), "landscaping", BudgetPeriod.ANNUAL, 12000, year_start)
    budget_svc.set_budget(session, str(beach.id), "cleaning", BudgetPeriod.ANNUAL, 8000, year_start)
    budget_svc.set_budget(session, str(mountain.id), "maintenance", BudgetPeriod.ANNUAL, 15000, year_start)
    budget_svc.set_budget(session, str(city.id), "maintenance", BudgetPeriod.ANNUAL, 10000, year_start)
    budget_svc.set_budget(session, str(city.id), "utilities", BudgetPeriod.ANNUAL, 6000, year_start)

    # === Transactions ===
    budget_svc.add_transaction(session, 450, str(beach.id), "maintenance", today - timedelta(days=15),
                               vendor_id_or_slug="coastal-plumbing", description="Pipe repair")
    budget_svc.add_transaction(session, 1200, str(beach.id), "landscaping", today - timedelta(days=10),
                               vendor_id_or_slug="peak-landscaping", description="Monthly landscaping")
    budget_svc.add_transaction(session, 350, str(beach.id), "cleaning", today - timedelta(days=7),
                               description="Deep clean service")
    budget_svc.add_transaction(session, 800, str(city.id), "maintenance", today - timedelta(days=20),
                               vendor_id_or_slug="metro-hvac", description="HVAC inspection")
    budget_svc.add_transaction(session, 150, str(city.id), "utilities", today - timedelta(days=5),
                               description="Electric bill")

    # === Notes ===
    note_svc.add_note(session, "property:beach-house", "Neighbor at 125 Ocean Dr is Tom (555-0301) - has spare gate key")
    note_svc.add_note(session, "vendor:coastal-plumbing", "Ask for Mike - he knows our system")
    note_svc.add_note(session, "staff:sarah-chen", "Available Mon-Fri, prefers morning shifts")
