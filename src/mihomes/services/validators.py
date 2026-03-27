"""Shared input validators for service layer."""


def validate_name(name: str, entity_type: str = "entity", max_length: int = 200) -> str:
    """Validate a name/title field. Returns stripped name or raises ValueError."""
    name = name.strip()
    if not name:
        raise ValueError(f"{entity_type.title()} name cannot be empty")
    if len(name) > max_length:
        raise ValueError(f"{entity_type.title()} name too long (max {max_length} chars, got {len(name)})")
    return name


def validate_positive_amount(amount: float, field_name: str = "amount") -> float:
    """Validate that a monetary amount is positive."""
    if amount < 0:
        raise ValueError(f"{field_name} cannot be negative (got {amount})")
    return amount
