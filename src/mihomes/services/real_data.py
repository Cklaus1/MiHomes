"""Real estate data loader for MiHomes — Belle Estate and investment properties."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import staff as staff_svc
from mihomes.services import vendor as vendor_svc
from mihomes.services import task as task_svc
from mihomes.models.property import PropertyType, PropertyStatus
from mihomes.models.staff import StaffRole
from mihomes.models.task import TaskPriority, RecurrenceFrequency


def load_real_data(session: Session) -> None:
    today = date.today()

    # =========================================================================
    # PROPERTIES
    # =========================================================================

    belle = prop_svc.create_property(
        session, "Belle Estate",
        address="1330 Monte Carlo Dr NW",
        property_type=PropertyType.ESTATE,
        climate_zone="southeast",
    )
    founders = prop_svc.create_property(
        session, "Founders",
        address="4381 Harris Valley Rd",
        property_type=PropertyType.INVESTMENT,
    )
    creators = prop_svc.create_property(
        session, "Creators",
        address="341 Harris Valley Rd",
        property_type=PropertyType.INVESTMENT,
    )
    builders = prop_svc.create_property(
        session, "Builders",
        address="1298 Swims Valley Dr",
        property_type=PropertyType.INVESTMENT,
    )

    # =========================================================================
    # SPACES (Belle Estate)
    # =========================================================================

    for name, stype in [
        ("Master Bedroom (Up)", "bedroom"),
        ("Master Bedroom (Down)", "bedroom"),
        ("Guest Bedrooms (3rd Floor)", "bedroom"),
        ("Kitchen", "kitchen"),
        ("Butler Pantry", "kitchen"),
        ("Family Room", "living"),
        ("Library", "office"),
        ("Theatre", "entertainment"),
        ("Wine Cellar", "storage"),
        ("Basement", "other"),
        ("Pool House", "outdoor"),
        ("Fish House", "outdoor"),
        ("RQ Court", "entertainment"),
        ("Garage", "storage"),
        ("Nanny Suite", "bedroom"),
        ("Elevator", "other"),
    ]:
        space_svc.create_space(session, name, str(belle.id), space_type=stype)

    # =========================================================================
    # STAFF
    # =========================================================================

    marcia = staff_svc.create_staff(
        session, "Marcia Johnson",
        role=StaffRole.HOUSEKEEPER,
        phone="770-276-6249",
        property_id_or_slug=str(belle.id),
    )

    diego = staff_svc.create_staff(
        session, "Diego Regalado",
        role=StaffRole.OTHER,
        phone="770-241-4567",
        property_id_or_slug=str(belle.id),
    )
    # Diego also covers the investment properties
    for prop in [founders, creators, builders]:
        staff_svc.assign_to_property(session, str(diego.id), str(prop.id))

    # =========================================================================
    # VENDORS
    # =========================================================================

    vendor_svc.create_vendor(session, "Cool Air Mechanical",
        contact_name="Cool Air", phone="770-266-5247",
        service_categories=["hvac", "heating", "cooling"],
        notes="Ongoing maintenance contract — 10 HVAC units. Renew annually.")

    vendor_svc.create_vendor(session, "Andy's Pool Service",
        phone="404-355-8218",
        service_categories=["pool"],
        service_areas=["Atlanta", "Smyrna"])

    vendor_svc.create_vendor(session, "Conserva Irrigation",
        contact_name="Dakota", phone="678-671-2020",
        service_categories=["irrigation"])

    vendor_svc.create_vendor(session, "Arrow Exterminator",
        phone="404-231-1342",
        service_categories=["pest-control", "exterminator"],
        notes="Yearly contract — termites/exterminator. Renew January.")

    vendor_svc.create_vendor(session, "Orkin Pest Control",
        phone="470-737-9665",
        service_categories=["pest-control"])

    vendor_svc.create_vendor(session, "Thyssenkrupp Elevator",
        contact_name="Arty", phone="770-916-0555",
        service_categories=["elevator"],
        notes="Quarterly elevator service. Contact: Samuel Longanecker 813-260-0820")

    vendor_svc.create_vendor(session, "Anderson Power Services",
        phone="770-222-1315",
        service_categories=["generator", "electrical"])

    vendor_svc.create_vendor(session, "Meer Electric",
        phone="770-993-8028",
        service_categories=["electrical", "security-cameras"],
        service_areas=["Alpharetta", "Atlanta"])

    vendor_svc.create_vendor(session, "Plumbworks",
        contact_name="Jamey", phone="404-524-1825",
        service_categories=["plumbing", "filtration"])

    vendor_svc.create_vendor(session, "Cumberland Landscaping",
        phone="404-567-1074",
        service_categories=["landscaping"])

    vendor_svc.create_vendor(session, "Frasers Roofing",
        contact_name="Travis Stahl", phone="404-341-7663",
        service_categories=["roofing"])

    vendor_svc.create_vendor(session, "Window Cleaning Experts",
        phone="770-355-0669",
        service_categories=["window-cleaning", "gutter-cleaning"])

    vendor_svc.create_vendor(session, "National Chimney Service",
        contact_name="Jerry", phone="770-772-4038",
        service_categories=["chimney"])

    vendor_svc.create_vendor(session, "EMC Security",
        phone="770-963-0305",
        service_categories=["security", "alarm"])

    vendor_svc.create_vendor(session, "Hughes Dry Professionals",
        contact_name="Lorie", phone="678-494-4884",
        service_categories=["carpet-cleaning", "upholstery"])

    vendor_svc.create_vendor(session, "OJ Construction",
        contact_name="Oleg Geydman", phone="770-765-0605",
        service_categories=["fencing", "construction"])

    vendor_svc.create_vendor(session, "All Weather Renovations",
        phone="678-540-7094",
        service_categories=["wood-repair", "painting"])

    vendor_svc.create_vendor(session, "Floodmasters",
        contact_name="Mitch Turner", phone="678-682-9750",
        service_categories=["water-damage", "restoration"])

    vendor_svc.create_vendor(session, "Suncatchers",
        phone="770-514-7564",
        service_categories=["gas", "solar"])

    vendor_svc.create_vendor(session, "Outdoor Lights",
        phone="770-844-1760",
        service_categories=["outdoor-lighting"])

    vendor_svc.create_vendor(session, "Crystal Springs",
        phone="800-728-5508",
        service_categories=["water-delivery"])

    # =========================================================================
    # TASKS — Marcia (Belle Estate housekeeping)
    # =========================================================================

    # Weekly
    task_svc.create_task(session, "Full house clean — all floors & common areas",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="All bedrooms, bathrooms, kitchen, butler pantry, family room, library, salon, formal living/dining room")

    task_svc.create_task(session, "Laundry — all household linens",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Sort and wash towels/sheets/darks/lights/delicates. Mon & Wed. Inform of dry cleaning needs.")

    task_svc.create_task(session, "Restock bathrooms & paper products",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id))

    # Biweekly
    task_svc.create_task(session, "Change bed sheets",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.BIWEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="All bedrooms. Wednesday.")

    task_svc.create_task(session, "Clean interior windows, sills & shutters",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.BIWEEKLY,
        assignee_id_or_slug=str(marcia.id))

    # Monthly — Marcia
    task_svc.create_task(session, "Deep clean all fridges & freezers",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id),
        description="Pool house, garage, kitchen, nanny suite, butler pantry, basement fridges and freezers")

    task_svc.create_task(session, "Vacuum all upholstered furniture",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id))

    task_svc.create_task(session, "Deep dust shelves, pictures & sconces",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id))

    task_svc.create_task(session, "Run unused dishwashers with soap",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id),
        description="Basement dishwasher and pool house kitchen dishwasher")

    # =========================================================================
    # TASKS — Diego (Belle Estate grounds/maintenance)
    # =========================================================================

    # Weekly
    task_svc.create_task(session, "Pool house & gym cleaning",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id),
        description="Clean pool house kitchen/bathroom x2 weekly. Clean/disinfect gyms x2 weekly.")

    task_svc.create_task(session, "Grounds & outdoor areas check",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id),
        description="Check pool, portico, arbor, steps, porches, grill. Decobb all porches/arbor/portico. Remove leaves/debris.")

    task_svc.create_task(session, "Golf cart clean & gas check",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id))

    task_svc.create_task(session, "Garbage to street",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id),
        description="Wednesday and Friday")

    task_svc.create_task(session, "Water plants (seasonal)",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id),
        description="All outside plants — frequency adjusts by season")

    # Biweekly
    task_svc.create_task(session, "Clean cobwebs — all outside structures",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.BIWEEKLY,
        assignee_id_or_slug=str(diego.id))

    # Monthly — Diego
    task_svc.create_task(session, "Deep clean fish house",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id))

    task_svc.create_task(session, "Bug stop — exterior perimeter",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id),
        description="March through October only")

    task_svc.create_task(session, "Check attics x3",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id),
        description="Two attics above 3rd floor, one above garage laundry")

    task_svc.create_task(session, "Clean elevator interior & button panels",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id))

    task_svc.create_task(session, "Clean theatre leather seating",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id))

    # =========================================================================
    # TASKS — Quarterly (Belle Estate)
    # =========================================================================

    task_svc.create_task(session, "Clean appliance vents",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Ice makers, refrigerators, freezers, cooler drawers")

    task_svc.create_task(session, "Clean wine cellar",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Vacuum, mop, dust cobwebs, wipe vents")

    task_svc.create_task(session, "Deep clean library",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(marcia.id),
        description="Dust all shelves, inside closed book shelves")

    task_svc.create_task(session, "Elevator quarterly service — Thyssenkrupp",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.QUARTERLY,
        description="Schedule tech visit. Contact Arty 678-589-1157 or Samuel 813-260-0820")

    task_svc.create_task(session, "Clean all interior doors (both sides)",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Garage area doors and kitchen/family area/butler pantry doors")

    task_svc.create_task(session, "Clean all ceiling fans",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Dust all fans, cover surfaces beneath. Mark off purple clipboard.")

    task_svc.create_task(session, "Clean chandeliers",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(marcia.id),
        description="Check for dust and streaks. 2-person job.")

    task_svc.create_task(session, "Change water filter — craft room",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Located in craft room behind purple room")

    task_svc.create_task(session, "Change wine cellar filter",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Located in wall in billiards room")

    task_svc.create_task(session, "Clean sub pumps",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Located outside stone hallway and billiards room")

    task_svc.create_task(session, "Vacuum drapes",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(marcia.id),
        description="Family room, kitchen area, bedrooms, theatre")

    task_svc.create_task(session, "Condition leather furniture",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(marcia.id),
        description="Theatre, library, master bedroom, entire estate")

    task_svc.create_task(session, "Pest control — interior",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.QUARTERLY,
        description="Schedule with Arrow or Orkin. Full interior house.")

    task_svc.create_task(session, "Clean roof drain",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Located on roof above kitchen windows by portico")

    # =========================================================================
    # TASKS — Seasonal (Diego, Belle Estate)
    # =========================================================================

    task_svc.create_task(session, "Winterize outside bar",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="fall",
        assignee_id_or_slug=str(diego.id),
        description="Shut off water valve located in garage. ~December.")

    task_svc.create_task(session, "Winterize irrigation system",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="fall",
        assignee_id_or_slug=str(diego.id),
        description="Shut off irrigation clock. ~December.")

    task_svc.create_task(session, "Winterize lake pump",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="fall",
        assignee_id_or_slug=str(diego.id),
        description="Take pump out of water, backwash line and tank. ~December.")

    task_svc.create_task(session, "Cover patio furniture & umbrellas",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="fall",
        assignee_id_or_slug=str(diego.id),
        description="Clean all furniture and covers before covering. Put wicker chairs/tables in screen porch. ~November.")

    task_svc.create_task(session, "Pressure wash property",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id),
        description="Full property per purple clipboard list. Schedule Window Cleaning Experts if needed.")

    task_svc.create_task(session, "Check & restart irrigation system",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id),
        description="Check irrigation heads, replace/clean as needed. Check city water is running. Check lake pump and water tank.")

    task_svc.create_task(session, "Uncover & clean patio furniture",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id),
        description="Uncover all furniture, wipe down surfaces. Clean and store covers. Take out wicker furniture. Clean cushions, wipe swing.")

    task_svc.create_task(session, "Clean outside lighting fixtures",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id))

    task_svc.create_task(session, "Knock down bird nests",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id),
        description="Back porch bird nests above curtains")

    # =========================================================================
    # TASKS — Annual
    # =========================================================================

    task_svc.create_task(session, "Replace kitchen water filters x2",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.ANNUAL,
        assignee_id_or_slug=str(diego.id),
        description="Check and change kitchen sink water filters x2. ~April.")

    task_svc.create_task(session, "Outdoor lighting annual inspection",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.ANNUAL,
        description="Schedule Outdoor Lights (770-844-1760) — ensure tech inspects all low/high voltage. ~August.")

    task_svc.create_task(session, "Renew Arrow exterminator contract",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.ANNUAL,
        description="Termite and exterminator annual contract renewal. ~January.")

    task_svc.create_task(session, "Renew Cool Air Mechanical HVAC contract",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.ANNUAL,
        description="Annual maintenance contract for all 10 HVAC units. ~April.")

    task_svc.create_task(session, "Check & clean gutters and downspouts",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.ANNUAL,
        assignee_id_or_slug=str(diego.id),
        description="Check all gutters and downspouts for debris. ~January. Schedule Window Cleaning Experts if needed.")

    task_svc.create_task(session, "Tree trimming — all trees above 10 ft",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.ANNUAL,
        description="Schedule tree service. Contact 404-CUT-TREES or Bates Hite (770-378-4701).")

    task_svc.create_task(session, "Clean teak wood — back porch & pool house",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.ANNUAL,
        assignee_id_or_slug=str(diego.id))

    task_svc.create_task(session, "Inspect & repair fenceline",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.ANNUAL,
        assignee_id_or_slug=str(diego.id),
        description="Entire property from gate around back and around yard. Contact OJ Construction (770-765-0605) if repairs needed.")

    # =========================================================================
    # TASKS — Investment properties (Diego)
    # =========================================================================

    for inv_prop in [founders, creators, builders]:
        task_svc.create_task(session, "Property check & maintenance walk",
            str(inv_prop.id), priority=TaskPriority.MEDIUM,
            recurrence=RecurrenceFrequency.WEEKLY,
            assignee_id_or_slug=str(diego.id),
            description="General inspection — check for issues, maintenance needs, grounds condition")
