"""Import from a spreadsheet (2026-11-05): homes, staff, vendors, tasks and issues from CSV.

Before this, `services/csv_io.py` could import, but only from the CLI, which a new customer moving
off a spreadsheet will never see. The flow is: pick what to import → download a template →
upload → **preview** (every row checked, nothing saved) → import → per-row results.

**Preview rolls back the whole transaction**, not a savepoint. It runs exactly the code the
import runs, so what it says is what will happen. An outermost rollback is also what clears the
calendar-sync marker (`personal_calendar/trigger.py`), so a preview queues nothing.

The CSV travels from preview to import in a hidden field rather than being stored. That is why
`csv_io.MAX_IMPORT_BYTES` sits under the form parser's 1 MB per-field limit. The import step
re-runs every row and reports its own results; it does not trust the preview.

Owner/admin only, the same people who can export the account (`export.data`): an import creates
homes and staff records, which staff cannot. Plan limits still apply, because
`create_property` checks the home limit itself, so a row over the limit is refused with the plan's
reason.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.services import csv_io
from mihomes.web.deps import get_db, templates

router = APIRouter()

#: Data portability, both directions: whoever may take the account's data out may bring it in.
IMPORT_ACTION = "export.data"

#: Rows shown in the preview and results tables. The counts always cover every row.
SHOWN_ROWS = 200


def _page(request: Request, entity_type: str, **ctx) -> HTMLResponse:
    if entity_type not in csv_io.IMPORT_COLUMNS:
        entity_type = "task"
    return templates.TemplateResponse(request, "data_import.html", {
        "page": "import",
        "entity_type": entity_type,
        "types": csv_io.LABELS,
        "columns": csv_io.IMPORT_COLUMNS[entity_type],
        "list_page": csv_io.LIST_PAGES[entity_type],
        "max_rows": csv_io.MAX_IMPORT_ROWS,
        "max_kb": csv_io.MAX_IMPORT_BYTES // 1000,
        "shown_rows": SHOWN_ROWS,
        **ctx,
    })


@router.get("/", response_class=HTMLResponse)
@declares(IMPORT_ACTION, Access.ACCOUNT)
def import_page(request: Request, type: str = "task"):
    return _page(request, type)


@router.get("/template/{entity_type}.csv")
@declares(IMPORT_ACTION, Access.ACCOUNT)
def import_template(entity_type: str):
    if entity_type not in csv_io.IMPORT_COLUMNS:
        return Response("Unknown import type.", status_code=404, media_type="text/plain")
    name = csv_io.LABELS[entity_type].replace(" ", "-")
    return Response(
        csv_io.import_template(entity_type), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="mihomes-{name}-template.csv"'},
    )


@router.post("/preview", response_class=HTMLResponse)
@declares(IMPORT_ACTION, Access.ACCOUNT)
def import_preview(
    request: Request,
    entity_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if entity_type not in csv_io.IMPORT_COLUMNS:
        return _page(request, "task", error="Choose what to import.")
    try:
        text = csv_io.decode_csv(file.file.read(csv_io.MAX_IMPORT_BYTES + 1))
        result = csv_io.run_import(db, entity_type, text)
    except csv_io.RowProblem as exc:
        db.rollback()
        return _page(request, entity_type, error=str(exc))
    finally:
        # Nothing from a preview survives, including the calendar-sync marker (see module doc).
        db.rollback()
    if not result.rows:
        return _page(request, entity_type, error="The file has no rows under its header line.")
    return _page(request, entity_type, result=result, csv_text=text, mode="preview",
                 filename=file.filename or "your file")


@router.post("/apply", response_class=HTMLResponse)
@declares(IMPORT_ACTION, Access.ACCOUNT)
def import_apply(
    request: Request,
    entity_type: str = Form(...),
    csv_text: str = Form(...),
    db: Session = Depends(get_db),
):
    if entity_type not in csv_io.IMPORT_COLUMNS:
        return _page(request, "task", error="Choose what to import.")
    try:
        result = csv_io.run_import(db, entity_type, csv_text)
    except csv_io.RowProblem as exc:
        db.rollback()
        return _page(request, entity_type, error=str(exc))
    db.commit()
    return _page(request, entity_type, result=result, mode="done")


# ── Export as a spreadsheet (2026-11-05) ──────────────────────────────────────────────────
#
# The other half of the import: the same columns, so a file can go out, be edited in Excel and
# come back. Before this the only web export was the owner's full-account JSON (`/privacy/
# export`), which no one opens in a spreadsheet. What it contains is what the viewer can see:
# RLS and the property scope apply to the query like any other page.

export_router = APIRouter()


@export_router.get("/{entity_type}.csv")
@declares(IMPORT_ACTION, Access.ACCOUNT)
def export_spreadsheet(entity_type: str, db: Session = Depends(get_db)):
    from mihomes.services.localtime import today

    if entity_type not in csv_io.IMPORT_COLUMNS:
        return Response("Unknown export type.", status_code=404, media_type="text/plain")
    name = f"mihomes-{csv_io.LABELS[entity_type].replace(' ', '-')}-{today().isoformat()}.csv"
    # A UTF-8 BOM first: without it Excel reads the file as Windows-1252 and "José" turns to mojibake.
    return Response(
        "﻿" + csv_io.export_for_spreadsheet(db, entity_type), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ── Export all (2026-11-06) ───────────────────────────────────────────────────────────────
#
# Every list in one download: a ZIP of the per-type spreadsheets above, so each file inside
# re-imports as it is. The owner's full-account JSON (`/privacy/export`) is still the complete
# record for leaving MiHomes; this is the version a person opens in Excel.

_EXPORT_ALL_README = """MiHomes export — {estate}, {day}

One spreadsheet per list, each with the same columns as its import template:

{files}

Open any of them in Excel, Numbers or Google Sheets. To bring edits back in, use
Settings → Estate → Import data and pick the same type: rows already in MiHomes are updated
from the file (blank cells leave values unchanged), new rows are added.

What you see is what your role can see: staff limited to some properties get those only.
"""


@export_router.get("/all.zip")
@declares(IMPORT_ACTION, Access.ACCOUNT)
def export_all(db: Session = Depends(get_db)):
    import io
    import zipfile

    from mihomes.models.account import Account
    from mihomes.services.localtime import today
    from mihomes.tenancy import current_account

    day = today().isoformat()
    account = db.get(Account, current_account.get(None)) if current_account.get(None) else None
    buffer = io.BytesIO()
    names = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for entity_type, label in csv_io.LABELS.items():
            name = f"{label.replace(' ', '-')}.csv"
            # The same UTF-8 BOM as the single exports, so Excel reads accents correctly.
            zf.writestr(name, "﻿" + csv_io.export_for_spreadsheet(db, entity_type))
            names.append(f"  {name:<24} import as: {label}")
        zf.writestr("README.txt", _EXPORT_ALL_README.format(
            estate=account.name if account else "your estate", day=day, files="\n".join(names)))
    return Response(
        buffer.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="mihomes-export-{day}.zip"'},
    )
