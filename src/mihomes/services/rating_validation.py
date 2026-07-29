"""Shared 1–5 rating-score validation.

M5: two parallel rating creators (`vendor.rate_vendor` and
`vendor_rating.create_rating`) each need the same 1–5 bounds check. Centralize
it here so the rule can never drift between them.
"""


def validate_score(name: str, value: int | None, *, required: bool = True) -> None:
    """Raise ValueError unless `value` is an integer in the inclusive 1–5 range.

    When `required` is False, `None` is accepted (an optional score left blank).
    """
    if value is None:
        if required:
            raise ValueError(f"{name} score is required")
        return
    if not 1 <= value <= 5:
        raise ValueError(f"{name} score must be between 1 and 5")


def validate_scores(scores: dict[str, tuple[int | None, bool]]) -> None:
    """Validate a batch of scores.

    `scores` maps a score name to `(value, required)`.
    """
    for name, (value, required) in scores.items():
        validate_score(name, value, required=required)
