"""Security-hardened static file serving for user uploads (spec D6).

Starlette's plain ``StaticFiles`` sends neither ``X-Content-Type-Options`` nor
``Content-Disposition``, so a file that slips past upload validation and is
served inline can execute as same-origin script. This subclass:

* always sends ``X-Content-Type-Options: nosniff`` (the browser must honour the
  declared type, not sniff an ``image/*`` as HTML), and
* forces ``Content-Disposition: attachment`` for anything that isn't a
  known inline-safe image or PDF — those two render inline (thumbnails, PDF
  preview), everything else downloads instead of rendering.
"""

from starlette.staticfiles import StaticFiles

# Types the UI renders inline; everything else is served as an attachment.
_INLINE_SAFE = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf",
}


class SecureStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in _INLINE_SAFE:
            # Don't let an unexpected type render in the page context.
            existing = response.headers.get("content-disposition", "")
            if "attachment" not in existing:
                response.headers["content-disposition"] = "attachment"
        return response
