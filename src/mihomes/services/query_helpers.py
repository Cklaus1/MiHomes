"""Shared query helpers for safe text matching.

M10: several call sites used `ilike("%name%")` / `ilike(name)` with unescaped
`%`/`_` wildcards and `.first()`, so a value containing a wildcard (or a short
value that is a prefix of many rows) silently matched the wrong row. These
helpers make the intent explicit:

- `exact_ci(col, value)` — a case-insensitive *exact* match (the common case
  where an exact name/identifier is meant).
- `escape_like(value)` — escape LIKE wildcards when a genuine substring search
  is intended, so user data can't inject `%`/`_` metacharacters.
"""

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

_LIKE_ESCAPE_CHAR = "\\"


def exact_ci(col, value: str) -> ColumnElement[bool]:
    """Case-insensitive exact match: ``lower(col) == lower(value)``."""
    return func.lower(col) == value.lower()


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards (``%`` ``_``) and the escape char itself.

    Use with an explicit ``escape="\\"`` on the ``like``/``ilike`` clause, e.g.::

        col.ilike(f"%{escape_like(term)}%", escape="\\\\")
    """
    return (
        value.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
        .replace("%", _LIKE_ESCAPE_CHAR + "%")
        .replace("_", _LIKE_ESCAPE_CHAR + "_")
    )
