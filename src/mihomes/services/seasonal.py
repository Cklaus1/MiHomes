"""Seasonal service — built-in seasonal maintenance templates and recommendations."""

from datetime import date

from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.template import Template
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.template import create_template

BUILTIN_TEMPLATES: dict[str, dict] = {
    "spring-opening": {
        "name": "Spring Property Opening",
        "description": "Comprehensive spring opening checklist for seasonal properties",
        "steps": [
            "Turn on water supply and check for leaks",
            "Inspect HVAC system and replace filters",
            "Clean gutters and downspouts",
            "Check exterior for winter damage",
            "Test smoke and CO detectors",
            "Deep clean interior",
            "Inspect and service pool/spa",
            "Lawn and garden spring prep",
        ],
    },
    "fall-closing": {
        "name": "Fall Property Closing",
        "description": "Winterization checklist for seasonal properties",
        "steps": [
            "Drain and winterize plumbing",
            "Service and shut down HVAC",
            "Clean and cover pool/spa",
            "Secure exterior furniture and equipment",
            "Install storm windows",
            "Set thermostats to minimum safe temperature",
            "Arrange for snow removal service",
            "Final security check and lockup",
        ],
    },
    "hurricane-prep": {
        "name": "Hurricane Preparation",
        "description": "Emergency preparation for hurricane season",
        "steps": [
            "Install hurricane shutters or board windows",
            "Secure outdoor furniture and loose items",
            "Check generator and fuel supply",
            "Stock emergency supplies (water, food, batteries)",
            "Review insurance documentation",
            "Document property condition with photos",
            "Test sump pumps and drainage",
            "Create evacuation plan and emergency contacts list",
        ],
    },
    "winter-check": {
        "name": "Winter Property Check",
        "description": "Mid-winter inspection for unoccupied properties",
        "steps": [
            "Check heating system is functioning",
            "Inspect for ice dams on roof",
            "Verify pipes are not frozen",
            "Check for snow/ice damage",
            "Test security systems",
            "Check for pest intrusion",
        ],
    },
    "summer-maintenance": {
        "name": "Summer Maintenance",
        "description": "Regular summer maintenance tasks",
        "steps": [
            "Service air conditioning system",
            "Inspect and clean outdoor areas",
            "Check irrigation systems",
            "Treat for pests and insects",
            "Inspect roof and exterior paint",
            "Service pool equipment",
        ],
    },
    "annual-inspection": {
        "name": "Annual Property Inspection",
        "description": "Comprehensive annual property inspection",
        "steps": [
            "Structural inspection (foundation, walls, roof)",
            "Electrical system check",
            "Plumbing system check",
            "HVAC full service",
            "Fire safety equipment inspection",
            "Pest inspection",
            "Appliance condition assessment",
            "Exterior and landscape review",
        ],
    },
    "guest-turnover": {
        "name": "Guest Turnover",
        "description": "Between-guest cleaning and preparation",
        "steps": [
            "Deep clean all rooms",
            "Launder all linens and towels",
            "Restock consumables",
            "Check and replace damaged items",
            "Test all appliances and electronics",
            "Set welcome amenities",
        ],
    },
    "fire-safety": {
        "name": "Fire Safety Audit",
        "description": "Annual fire safety inspection and maintenance",
        "steps": [
            "Test all smoke detectors and replace batteries",
            "Test CO detectors",
            "Inspect fire extinguishers",
            "Check sprinkler system",
            "Review and update evacuation plan",
            "Clear brush and debris from property perimeter",
        ],
    },
}


def seed_templates(session: Session) -> list[Template]:
    """Seed the database with all built-in seasonal templates."""
    created = []
    for key, data in BUILTIN_TEMPLATES.items():
        # Skip if already exists
        existing = session.query(Template).filter(Template.slug == key).first()
        if existing:
            continue
        tmpl = create_template(
            session, data["name"],
            description=data["description"],
            steps=data["steps"],
            slug=key,
        )
        created.append(tmpl)
    return created


def recommend_seasonal(session: Session, property_slug: str) -> list[dict]:
    """Recommend seasonal templates based on property attributes and current date."""
    prop = resolve_identifier(session, Property, property_slug)
    today = date.today()
    month = today.month
    recommendations = []

    # Season-based recommendations
    if month in (3, 4):  # March-April
        recommendations.append({
            "template": "spring-opening",
            "reason": "Spring season — time to open and prepare the property",
        })
    elif month in (9, 10):  # September-October
        recommendations.append({
            "template": "fall-closing",
            "reason": "Fall season — time to winterize the property",
        })
    elif month in (6, 7, 8):  # June-August
        recommendations.append({
            "template": "summer-maintenance",
            "reason": "Summer season — regular maintenance recommended",
        })
        # Hurricane prep for coastal/tropical zones
        if prop.climate_zone and any(
            z in (prop.climate_zone or "").lower()
            for z in ("tropical", "coastal", "subtropical", "hurricane")
        ):
            recommendations.append({
                "template": "hurricane-prep",
                "reason": f"Hurricane season for {prop.climate_zone} climate zone",
            })
    elif month in (12, 1, 2):  # December-February
        recommendations.append({
            "template": "winter-check",
            "reason": "Winter — check unoccupied property status",
        })

    # Annual inspection is always a good idea
    recommendations.append({
        "template": "annual-inspection",
        "reason": "Annual inspection recommended for all properties",
    })

    # Fire safety in dry months
    if month in (5, 6, 7, 8, 9):
        recommendations.append({
            "template": "fire-safety",
            "reason": "Fire season — safety audit recommended",
        })

    return recommendations
