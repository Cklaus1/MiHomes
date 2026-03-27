"""AI role definitions, system prompts, and query routing."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class AIRole:
    name: str
    display_name: str
    keywords: list[str]
    system_prompt: str
    data_categories: list[str] = field(default_factory=list)


SPACE_FRAMEWORK = """
Your decision framework uses the SPACE prioritization:
- S (Safety): Risk to people or property — always highest priority
- P (Presence): Family occupying or arriving soon at a property
- A (Asset Protection): Delay causes damage to property or assets
- C (Compliance): Legal, regulatory, or contractual deadlines
- E (Economy): Financial impact, cost of delay, budget optimization

When making recommendations:
1. State the SPACE classification for each item
2. Prioritize accordingly (S > P > A > C > E)
3. Be specific — reference actual entities, dates, and amounts from the data
4. Suggest concrete next steps
"""


def _base_prompt(role_desc: str) -> str:
    return f"""{role_desc}

{SPACE_FRAMEWORK}

Current date: {date.today().isoformat()}

Respond concisely. Use bullet points for action items. Reference specific properties, tasks, issues, and vendors by name."""


ROLES: dict[str, AIRole] = {
    "estate_manager": AIRole(
        name="estate_manager",
        display_name="Estate Manager",
        keywords=["prioritize", "overview", "status", "plan", "recommend", "what should", "summary"],
        system_prompt=_base_prompt(
            "You are the Estate Manager for a multi-home portfolio. You oversee all properties, "
            "coordinate staff and vendors, manage budgets, and ensure every home is ready when needed. "
            "You are the generalist who synthesizes information across all domains."
        ),
        data_categories=["properties", "tasks", "issues", "budgets", "alerts", "contracts", "insurance", "vendors", "staff"],
    ),
    "maintenance": AIRole(
        name="maintenance",
        display_name="Maintenance Advisor",
        keywords=["leak", "repair", "hvac", "plumbing", "roof", "appliance", "break", "fix",
                  "maintenance", "broken", "damage", "crack", "mold", "pest", "water"],
        system_prompt=_base_prompt(
            "You are the Maintenance Advisor for a multi-home portfolio. You specialize in "
            "building systems, preventive maintenance scheduling, failure prediction, and vendor matching. "
            "You know when equipment should be replaced vs repaired and can estimate costs."
        ),
        data_categories=["properties", "tasks", "issues", "vendors"],
    ),
    "financial": AIRole(
        name="financial",
        display_name="Financial Analyst",
        keywords=["budget", "cost", "spend", "expense", "price", "money", "forecast",
                  "over budget", "savings", "invoice", "payment", "financial"],
        system_prompt=_base_prompt(
            "You are the Financial Analyst for a multi-home portfolio. You specialize in "
            "budget variance analysis, cost optimization, spending patterns, and forecasting. "
            "You identify anomalies and recommend cost-saving opportunities."
        ),
        data_categories=["properties", "budgets", "contracts", "insurance"],
    ),
    "vendor_strategist": AIRole(
        name="vendor_strategist",
        display_name="Vendor Strategist",
        keywords=["vendor", "contractor", "service provider", "quote", "bid", "contract",
                  "negotiate", "hire", "fire", "replace vendor"],
        system_prompt=_base_prompt(
            "You are the Vendor Strategist for a multi-home portfolio. You specialize in "
            "vendor performance analysis, contract negotiation, and matching the right vendor "
            "to the right job. You track reliability, pricing, and service quality."
        ),
        data_categories=["vendors", "contracts", "properties"],
    ),
    "compliance": AIRole(
        name="compliance",
        display_name="Compliance Monitor",
        keywords=["insurance", "permit", "license", "expir", "renew", "compliance",
                  "regulation", "hoa", "deadline", "filing"],
        system_prompt=_base_prompt(
            "You are the Compliance Monitor for a multi-home portfolio. You track "
            "insurance policies, permits, certifications, contract renewals, and regulatory "
            "requirements. You ensure nothing lapses and flag upcoming deadlines."
        ),
        data_categories=["contracts", "insurance", "properties", "alerts"],
    ),
}


def route_query(query: str, explicit_role: str | None = None) -> list[AIRole]:
    """Determine which AI role(s) to activate for a query."""
    if explicit_role:
        role = ROLES.get(explicit_role)
        if role:
            return [role]
        # Try matching display name
        for r in ROLES.values():
            if explicit_role.lower() in r.display_name.lower():
                return [r]

    query_lower = query.lower()
    scored: list[tuple[int, AIRole]] = []

    for role in ROLES.values():
        if role.name == "estate_manager":
            continue  # fallback, don't score
        score = sum(1 for kw in role.keywords if kw in query_lower)
        if score > 0:
            scored.append((score, role))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return [ROLES["estate_manager"]]

    # Return top role; if multi-domain, add estate manager as orchestrator
    result = [scored[0][1]]
    if len(scored) > 1 and scored[1][0] >= 2:
        result.append(scored[1][1])
    if len(result) > 1:
        result.insert(0, ROLES["estate_manager"])

    return result
