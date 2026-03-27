"""Shared helper for safe attribute updates — prevents relationship overwrites."""


def safe_update(instance, kwargs: dict) -> None:
    """Apply kwargs to an ORM instance, only setting actual column attributes.

    Prevents accidental overwrite of relationships (e.g., passing spaces=[])
    which would cause data loss.
    """
    column_names = {c.name for c in instance.__table__.columns}
    for key, value in kwargs.items():
        if key in column_names:
            setattr(instance, key, value)
