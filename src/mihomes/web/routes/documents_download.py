"""Tenant-checked document download (G11 · A14).

**This route replaces a real cross-tenant hole.** `web/app.py` used to mount the uploads directory
as static files:

    app.mount("/uploads", SecureStaticFiles(directory=UPLOADS_DIR))

with no authentication and no tenant check. Any request that could reach the app could fetch **any**
tenant's document. Upload filenames were `uuid4().hex` — unguessable — but generated reports used
`{title-slug}-{8 hex}`, which is partly derived from user-visible text; and obscurity is not access
control in either case. One tenant learning another's URL was sufficient.

**The check is on the key's account prefix, before any bytes are touched.** `build_key` puts the
account in the key, so authorising a request needs no database round trip and cannot be skipped by a
storage backend that resolves paths differently. A key whose prefix does not match the caller's
account is a **404, not a 403**: a 403 confirms the object exists, which tells the caller something
about another tenant.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, Response

from mihomes.storage import ObjectNotFound, get_storage, key_account
from mihomes.tenancy import require_account

router = APIRouter()

# Types that may render in the browser. Everything else is forced to download, so an uploaded
# .html or .svg cannot execute script in the app's origin (the same reasoning as `SecureStaticFiles`,
# preserved here because that mount is gone).
_INLINE_TYPES = {
    "application/pdf",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/plain",
}


@router.get("/documents/file/{key:path}")
def download(key: str):
    """Stream an object, but only to the account that owns it."""
    try:
        account_id = require_account()
    except LookupError:
        # No tenant bound: nothing is authorised. 404 rather than 401 for the same reason as below —
        # this endpoint declines to confirm that any particular key exists.
        raise HTTPException(status_code=404) from None

    owner = key_account(key)
    if owner is None or owner != str(account_id):
        # Deliberately 404, not 403. A 403 would confirm the object exists and belongs to someone
        # else, which is itself a cross-tenant disclosure.
        raise HTTPException(status_code=404)

    storage = get_storage()

    # If the backend can issue a time-limited URL, redirect rather than proxying the bytes: it saves
    # the app from streaming large files, and the URL expires. The authorisation above has already
    # happened, so the redirect is only ever issued to the owning account.
    presigned = storage.url(key)
    if presigned:
        return RedirectResponse(presigned, status_code=307)

    try:
        data = storage.get(key)
    except ObjectNotFound:
        raise HTTPException(status_code=404) from None

    content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    disposition = "inline" if content_type in _INLINE_TYPES else "attachment"
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{key.rsplit("/", 1)[-1]}"',
            # Carried over from SecureStaticFiles: without nosniff, a browser may sniff an
            # uploaded file as HTML and execute it in this origin.
            "X-Content-Type-Options": "nosniff",
            # A tenant document must not sit in a shared cache.
            "Cache-Control": "private, no-store",
        },
    )
