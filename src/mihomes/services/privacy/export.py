"""`build_export` — every row an account owns, assembled from the ORM (SPEC-005 §5.4, D14).

## What this must NOT be built on (N4)

Two functions in this tree already look like "export", and both are cross-tenant by construction:

* **`csv_io.export_csv` covers 5 of 28 model modules** (F4) — `property`, `staff`, `vendor`,
  `task`, `issue` — and calls `session.query(model).all()` with **no account filter**. A GDPR
  export built on it silently omits roughly 82% of the estate while appearing to succeed.
* **`backup.create_backup` tars the whole database and the whole media directory** (F5), with no
  account parameter anywhere in its signature. Under multitenancy, routing that to a customer's
  "download my data" button is a total cross-tenant breach wearing the name of a feature.

Neither is fixable in place, which is why D14 states the rule as *"assembled from the ORM under
the scoped session"* rather than *"filter the existing exporter"*.

## Tables are enumerated, never listed

From `tenancy.registry.TENANT_TABLES`, which is the exhaustive set — including the two Core
association tables (`staff_properties`, `vendor_properties`) that have **no declarative class**.
`Base.registry.mappers` misses those, and an ORM-only sweep would silently omit them; the registry
exists precisely because that check reported green over a real gap once already (SPEC-002 A1/A21).

A hand-written list rots the first time someone adds a model, and it rots silently: the export
still succeeds, just without the new table. A27 asserts the enumeration covers every table.

## Documents are references, not bytes

`StorageProvider.url()` yields a time-limited link. An estate's media does not belong inlined in a
JSON blob — a household with a few hundred photographs would produce a file no browser will open,
assembled by holding every byte in memory at once. `url()` returning `None` (the filesystem
backend has no URLs) is not a failure: the reference records the key, and the operator retrieves
it out of band.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models import Base
from mihomes.tenancy.registry import TENANT_TABLES, tenant_models

__all__ = ["ExportBundle", "build_export", "exportable_tables"]

logger = logging.getLogger(__name__)

#: Presigned link lifetime. Fifteen minutes: long enough to click through from the export,
#: short enough that a bundle forwarded to someone else is already dead.
URL_TTL_SECONDS = 900


@dataclass
class ExportBundle:
    """One account's data, table by table.

    `tables` maps table name -> list of row dicts. Every `TENANT_TABLES` entry appears as a
    key **even when empty** — an absent key and an empty list mean very different things, and
    only the second one can be distinguished from a table the export forgot.
    """

    account_id: str
    generated_at: datetime
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    documents: list[dict[str, Any]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return sum(len(rows) for rows in self.tables.values())


def exportable_tables() -> list[str]:
    """Every account-scoped table, from the registry.

    `TENANT_TABLES` rather than `tenant_models()`: the latter walks `Base.registry.mappers` and
    therefore cannot see the two association tables, which carry `account_id` with no class.
    """
    return sorted(TENANT_TABLES)


def _serialize(value: Any) -> Any:
    """JSON-safe, and lossless for the types this schema actually uses."""
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "hex") and hasattr(value, "int"):  # uuid.UUID
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    if hasattr(value, "isoformat"):  # date, time
        return value.isoformat()
    if hasattr(value, "quantize"):  # Decimal
        return str(value)
    if isinstance(value, enum.Enum):
        # `.value`, not `str(value)`: this schema's enums subclass `str`, so the repr would be
        # `PropertyType.PRIMARY` in the export a customer receives rather than `primary`.
        return value.value
    return value


def build_export(session: Session, account_id) -> ExportBundle:
    """Every row this account owns, assembled under the scoped session.

    **Mapped classes go through the ORM, association tables are filtered explicitly**, and the
    difference is not stylistic — the first version of this function used `select(table)` on the
    Core `Table` for everything and **returned three accounts' properties**. A6 caught it.

    `tenancy/session.py` applies its tenant filter through `with_loader_criteria`, which takes a
    *mapped class*: a Core `Table` select is invisible to it. The remaining protection is RLS,
    and the test suite connects as `postgres` — a superuser, which bypasses RLS unconditionally,
    even under `FORCE ROW LEVEL SECURITY`. So a Core-table export is unfiltered in exactly the
    environment where nothing would say so. That is what D14 means by *"assembled from the ORM
    under the scoped session"*, read as an instruction rather than a description.

    The two association tables have no class and so cannot go through the ORM. They are filtered
    on `account_id` here, explicitly, because for them the alternative is not "RLS covers it" but
    "nothing covers it" — `tenancy/session.py`'s own docstring calls this out as the blind spot
    the registry exists for, showing up a third time.

    Requires a bound account context; the caller (an owner-only route) establishes it.
    """
    bundle = ExportBundle(
        account_id=str(account_id),
        generated_at=datetime.now(UTC),
    )

    models = {model.__tablename__: model for model in tenant_models()}

    for table_name in exportable_tables():
        model = models.get(table_name)
        if model is not None:
            # Mapped: `with_loader_criteria` filters this to the bound account.
            #
            # **Measured, and worth stating precisely**: forcing every table down the `else`
            # branch below is *also* correct, because that branch filters explicitly. The
            # mutation "read mapped classes via the Core table again" is therefore inert, and
            # the earlier leak was not caused by using Core tables — it was caused by using
            # them **with no filter at all**. The branch stands because D14 says "assembled
            # from the ORM", and because a mapped read that silently stopped being filtered is
            # a bug in the app-wide guarantee, which the export should surface rather than
            # paper over with a second `WHERE` of its own.
            rows = [
                {c.name: getattr(obj, c.name) for c in model.__table__.columns}
                for obj in session.execute(select(model)).scalars()
            ]
        else:
            # Core association table — no class, so no ORM filter. Explicit, or nothing.
            table = Base.metadata.tables[table_name]
            rows = session.execute(
                select(table).where(table.c.account_id == account_id)
            ).mappings().all()

        bundle.tables[table_name] = [
            {key: _serialize(value) for key, value in dict(row).items()} for row in rows
        ]

    bundle.documents = _document_references(session)
    logger.info(
        "export built: account=%s tables=%d rows=%d documents=%d",
        bundle.account_id, len(bundle.tables), bundle.row_count, len(bundle.documents),
    )
    return bundle


def _document_references(session: Session) -> list[dict[str, Any]]:
    """Presigned references for this account's stored files — never the bytes (§5.4).

    A document whose `file_path` is free text rather than a storage key is included with a
    `null` url and its path preserved: those are pre-SPEC-002 rows pointing at somewhere on the
    operator's disk, and dropping them from the export would quietly under-report what is held.
    """
    from mihomes.models.document import Document
    from mihomes.storage import get_storage, is_storage_key

    try:
        storage = get_storage()
    except Exception:
        # An export that cannot reach storage still lists what exists. Failing the whole
        # bundle because links cannot be minted would withhold the data the request is for.
        logger.exception("export: storage unavailable; documents listed without urls")
        storage = None

    references = []
    for doc in session.execute(select(Document)).scalars():
        key = doc.file_path
        url = None
        if storage is not None and key and is_storage_key(key):
            try:
                url = storage.url(key, expires_in=URL_TTL_SECONDS)
            except Exception:
                logger.exception("export: could not sign %s", key)
        references.append(
            {
                "id": str(doc.id),
                "title": getattr(doc, "title", None),
                "file_path": key,
                "url": url,
                "url_expires_in": URL_TTL_SECONDS if url else None,
            }
        )
    return references
