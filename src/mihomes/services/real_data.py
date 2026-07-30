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
    # L3: guard against a double-load duplicating the whole estate (mirrors
    # load_demo_data). The Belle Estate slug is the canonical marker.
    from mihomes.models.property import Property
    if session.query(Property).filter(Property.slug == "belle-estate").first():
        raise ValueError("Real data already loaded. Delete the database and reinitialize to reload.")

    today = date.today()

    def create_task(*args, **kwargs):
        # L3: recurring tasks need a due_date seed, otherwise the schedule's
        # next_due is never computed (calculate_next_due only runs when a seed
        # date is present) and the task never surfaces on the calendar. Default
        # the seed to today for any recurring task that doesn't specify one.
        if (
            kwargs.get("recurrence", RecurrenceFrequency.ONCE) != RecurrenceFrequency.ONCE
            and kwargs.get("due_date") is None
        ):
            kwargs["due_date"] = today
        return task_svc.create_task(*args, **kwargs)

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

    # --- Daily operational tasks (tracked as weekly recurring; performed every day) ---

    create_task(session, "Daily: Check all rooms — bedrooms, 2nd floor, closets & lights",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Daily: Check all bedrooms/kitchen/family area. Check entire 2nd floor. Clean all areas. Maintain closets — straighten clothes, empty laundry baskets, wipe shelves. Turn off lights, ensure front door is unlocked.")

    create_task(session, "Daily: Bathroom care — clean, disinfect & stock",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Daily: Wipe all surfaces/mirrors. Disinfect toilet/face bowl/handles. Empty waste baskets. Stock paper products/cleaners/towels. Sweep floor/mop tile as needed. Check/clean bathroom by Library. Turn lights on/off.")

    create_task(session, "Daily: Kitchen & butler pantry maintenance",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Daily: Wipe surfaces/counters/ledges/molding/lights. Clean appliances. Wash dishes & put away. Empty trash cans x3. Clean out fridge/freezer as needed. Check for spoiled food. Disinfect phones/handles/switches/countertops. Refill water coolers x2. Check dishwashers x2/put away dishes. Check/clean microwaves x2. Fill paper products/cups/plates/utensils. Add water to hot chocolate machine as needed. Check/stock fridges — kitchen & butler pantry. Turn lights on/off.")

    create_task(session, "Daily: Family room & back staircase",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Daily: Dust surfaces/tables/windows/fans. Disinfect handles/remotes/switches. Clean back hidden staircase windows and stairs. Make sure all doors are locked. Turn lights on/off.")

    create_task(session, "Daily: Pet care — dog water & in/out",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Daily: Check and clean dog water bowls. Let dogs in/out as needed.")

    create_task(session, "Daily: General checks — packages, lightbulbs, vacuums, deliveries",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Daily: Check front door for packages. Check for burned out lightbulbs in all areas. Put away Instacart orders. Walls/doors — remove scuffs/scratches as needed. Clean all vacuums/hand vacs. Put dirty rags in proper areas to be washed. Check calendar for Crystal Springs water updates.")

    # --- Laundry days: Mon & Wed ---

    create_task(session, "Laundry — all household linens (Mon & Wed)",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Bring all laundry from all rooms to laundry room. Sort towels/sheets/darks/lights/delicates/dry clean. Inform of dry cleaning needs. Iron as needed. All laundry except rags & basement laundry.")

    # --- Weekly deep-clean by area ---

    create_task(session, "Weekly: Upstairs laundry room deep clean",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Dust surfaces/baseboards/washer & dryer. Organize supplies. Sweep & mop. Stock. Clean vacuum filters on Friday.")

    create_task(session, "Weekly: Bedrooms deep clean",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Disinfect doorknobs and light switches. Wipe electronics down with wipes. Vacuum/mop. Deep dust/wipe down all items. Organize/stock.")

    create_task(session, "Weekly: Bathrooms deep clean",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Vacuum/mop all areas. Clean showers (Wednesday). Organize/stock. Dust all areas.")

    create_task(session, "Weekly: Kitchen, butler pantry & back stairwell deep clean",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Deep dust/wipe down all items. Vacuum/mop all areas 2x weekly. Check all food dates in kitchen/fridge/freezer.")

    create_task(session, "Weekly: Family room deep clean",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Deep dust/wipe down all items. Dust baseboards. Vacuum/mop all areas 2x weekly. Mop stone 2x weekly.")

    create_task(session, "Weekly: Guest bedroom service",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="Vacuum. Wipe down bathroom/mirror/counters/toilet. Mop floor. Dust. Stock as needed. Change sheets as needed.")

    # --- Biweekly ---

    create_task(session, "Change bed sheets",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.BIWEEKLY,
        assignee_id_or_slug=str(marcia.id),
        description="All bedrooms. Wednesday.")

    create_task(session, "Sanitize toothbrushes",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.BIWEEKLY,
        assignee_id_or_slug=str(marcia.id))

    create_task(session, "Clean interior windows, sills & shutters",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.BIWEEKLY,
        assignee_id_or_slug=str(marcia.id))

    # --- Monthly ---

    create_task(session, "Clean inside of all windows",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id))

    create_task(session, "Deep clean all fridges & freezers",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id),
        description="Wipe down inside of fridge & freezers x3. Pool house, garage, kitchen, nanny suite, butler pantry, basement fridges and freezers.")

    create_task(session, "Clean upstairs washing machine",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id))

    create_task(session, "Run unused dishwashers with soap",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id),
        description="Basement dishwasher and pool house kitchen dishwasher")

    create_task(session, "Vacuum all upholstered furniture",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id))

    create_task(session, "Deep dust shelves, pictures & sconces",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(marcia.id))

    # --- As-needed ---

    create_task(session, "Decorate for holidays",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.ONCE,
        assignee_id_or_slug=str(marcia.id),
        description="Decorate common areas and relevant rooms for upcoming holidays as needed.")

    create_task(session, "Prepare common areas for special events",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.ONCE,
        assignee_id_or_slug=str(marcia.id),
        description="Set up and prepare all common areas before scheduled special events.")

    # =========================================================================
    # TASKS — Diego (Belle Estate grounds/maintenance)
    # =========================================================================

    # Weekly
    create_task(session, "Pool house & gym cleaning",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id),
        description="Clean pool house kitchen/bathroom x2 weekly. Clean/disinfect gyms x2 weekly.")

    create_task(session, "Grounds & outdoor areas check",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id),
        description="Check pool, portico, arbor, steps, porches, grill. Decobb all porches/arbor/portico. Remove leaves/debris.")

    create_task(session, "Golf cart clean & gas check",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id))

    create_task(session, "Garbage to street",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id),
        description="Wednesday and Friday")

    create_task(session, "Water plants (seasonal)",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.WEEKLY,
        assignee_id_or_slug=str(diego.id),
        description="All outside plants — frequency adjusts by season")

    # Biweekly
    create_task(session, "Clean cobwebs — all outside structures",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.BIWEEKLY,
        assignee_id_or_slug=str(diego.id))

    # Monthly — Diego
    create_task(session, "Deep clean fish house",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id))

    create_task(session, "Bug stop — exterior perimeter",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id),
        description="March through October only")

    create_task(session, "Check attics x3",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id),
        description="Two attics above 3rd floor, one above garage laundry")

    create_task(session, "Clean elevator interior & button panels",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id))

    create_task(session, "Clean theatre leather seating",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.MONTHLY,
        assignee_id_or_slug=str(diego.id))

    # =========================================================================
    # TASKS — Quarterly (Belle Estate)
    # =========================================================================

    create_task(session, "Clean appliance vents",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Ice makers, refrigerators, freezers, cooler drawers")

    create_task(session, "Clean wine cellar",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Vacuum, mop, dust cobwebs, wipe vents")

    create_task(session, "Deep clean library",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(marcia.id),
        description="Dust all shelves, inside closed book shelves")

    create_task(session, "Elevator quarterly service — Thyssenkrupp",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.QUARTERLY,
        description="Schedule tech visit. Contact Arty 678-589-1157 or Samuel 813-260-0820")

    create_task(session, "Clean all interior doors (both sides)",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Garage area doors and kitchen/family area/butler pantry doors")

    create_task(session, "Clean all ceiling fans",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Dust all fans, cover surfaces beneath. Mark off purple clipboard.")

    create_task(session, "Clean chandeliers",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(marcia.id),
        description="Check for dust and streaks. 2-person job.")

    create_task(session, "Change water filter — craft room",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Located in craft room behind purple room")

    create_task(session, "Change wine cellar filter",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Located in wall in billiards room")

    create_task(session, "Clean sub pumps",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Located outside stone hallway and billiards room")

    create_task(session, "Vacuum drapes",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(marcia.id),
        description="Family room, kitchen area, bedrooms, theatre")

    create_task(session, "Condition leather furniture",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(marcia.id),
        description="Theatre, library, master bedroom, entire estate")

    create_task(session, "Pest control — interior",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.QUARTERLY,
        description="Schedule with Arrow or Orkin. Full interior house.")

    create_task(session, "Clean roof drain",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.QUARTERLY,
        assignee_id_or_slug=str(diego.id),
        description="Located on roof above kitchen windows by portico")

    # =========================================================================
    # TASKS — Seasonal (Diego, Belle Estate)
    # =========================================================================

    create_task(session, "Winterize outside bar",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="fall",
        assignee_id_or_slug=str(diego.id),
        description="Shut off water valve located in garage. ~December.")

    create_task(session, "Winterize irrigation system",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="fall",
        assignee_id_or_slug=str(diego.id),
        description="Shut off irrigation clock. ~December.")

    create_task(session, "Winterize lake pump",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="fall",
        assignee_id_or_slug=str(diego.id),
        description="Take pump out of water, backwash line and tank. ~December.")

    create_task(session, "Cover patio furniture & umbrellas",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="fall",
        assignee_id_or_slug=str(diego.id),
        description="Clean all furniture and covers before covering. Put wicker chairs/tables in screen porch. ~November.")

    create_task(session, "Pressure wash property",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id),
        description="Full property per purple clipboard list. Schedule Window Cleaning Experts if needed.")

    create_task(session, "Check & restart irrigation system",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id),
        description="Check irrigation heads, replace/clean as needed. Check city water is running. Check lake pump and water tank.")

    create_task(session, "Uncover & clean patio furniture",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id),
        description="Uncover all furniture, wipe down surfaces. Clean and store covers. Take out wicker furniture. Clean cushions, wipe swing.")

    create_task(session, "Clean outside lighting fixtures",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id))

    create_task(session, "Knock down bird nests",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring",
        assignee_id_or_slug=str(diego.id),
        description="Back porch bird nests above curtains")

    # =========================================================================
    # TASKS — Annual
    # =========================================================================

    create_task(session, "Replace kitchen water filters x2",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.ANNUAL,
        assignee_id_or_slug=str(diego.id),
        description="Check and change kitchen sink water filters x2. ~April.")

    create_task(session, "Outdoor lighting annual inspection",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.ANNUAL,
        description="Schedule Outdoor Lights (770-844-1760) — ensure tech inspects all low/high voltage. ~August.")

    create_task(session, "Renew Arrow exterminator contract",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.ANNUAL,
        description="Termite and exterminator annual contract renewal. ~January.")

    create_task(session, "Renew Cool Air Mechanical HVAC contract",
        str(belle.id), priority=TaskPriority.HIGH,
        recurrence=RecurrenceFrequency.ANNUAL,
        description="Annual maintenance contract for all 10 HVAC units. ~April.")

    create_task(session, "Check & clean gutters and downspouts",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.ANNUAL,
        assignee_id_or_slug=str(diego.id),
        description="Check all gutters and downspouts for debris. ~January. Schedule Window Cleaning Experts if needed.")

    create_task(session, "Tree trimming — all trees above 10 ft",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.ANNUAL,
        description="Schedule tree service. Contact 404-CUT-TREES or Bates Hite (770-378-4701).")

    create_task(session, "Clean teak wood — back porch & pool house",
        str(belle.id), priority=TaskPriority.LOW,
        recurrence=RecurrenceFrequency.ANNUAL,
        assignee_id_or_slug=str(diego.id))

    create_task(session, "Inspect & repair fenceline",
        str(belle.id), priority=TaskPriority.MEDIUM,
        recurrence=RecurrenceFrequency.ANNUAL,
        assignee_id_or_slug=str(diego.id),
        description="Entire property from gate around back and around yard. Contact OJ Construction (770-765-0605) if repairs needed.")

    # =========================================================================
    # TASKS — Investment properties (Diego)
    # =========================================================================

    for inv_prop in [founders, creators, builders]:
        create_task(session, "Property check & maintenance walk",
            str(inv_prop.id), priority=TaskPriority.MEDIUM,
            recurrence=RecurrenceFrequency.WEEKLY,
            assignee_id_or_slug=str(diego.id),
            description="General inspection — check for issues, maintenance needs, grounds condition")
