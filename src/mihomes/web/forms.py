"""Helpers for parsing/validating web form input."""

from pathlib import Path

from mihomes.services.ai.file_processor import Attachment, process_upload

# parse_money/parse_date live in services.parsing (single source of truth shared
# by web + CLI). Re-exported here for existing web callers.
from mihomes.services.parsing import parse_date, parse_money  # noqa: F401

# Documents the estate legitimately attaches: photos and PDFs (invoices,
# contracts, warranties, permits). Everything else — crucially .html/.svg/.xhtml,
# which execute as same-origin script when served inline — is rejected (spec D6).
DOCUMENT_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",  # images
    ".pdf",                                      # documents
}
DOCUMENT_CONTENT_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp",
    "application/pdf",
}
MAX_DOCUMENT_BYTES = 25_000_000  # 25 MB — invoices/contracts are small


def _store_bytes(data: bytes, filename: str, *, content_type: str | None = None) -> str:
    """Put `data` in storage under a tenant-prefixed key and return the key (G11 · A14).

    The one place the web layer writes an object, so the key shape and the tenant prefix have a
    single definition. Requires an account context — `require_account()` raises without one, which
    is the correct outcome: a file with no tenant has nowhere to live and nobody who may read it.
    """
    from mihomes.storage import build_key, get_storage
    from mihomes.tenancy import require_account

    key = build_key(require_account(), "documents", filename)
    get_storage().put(key, data, content_type=content_type)
    return key


async def read_document_upload(
    file,
    *,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    extra_extensions: set[str] | None = None,
) -> str:
    """Validate + persist one uploaded document, returning its served URL path.

    Guards the four defects shared by every document route (spec D6/H34/M45):
    a ``None`` filename (→ 500), a client-controlled extension that lets an
    ``.svg``/``.html`` become a stored-XSS page, an unbounded body (OOM), and
    the package-internal write location that ``pip upgrade`` wipes. Files land
    under the user-data ``UPLOADS_DIR`` with a random name.

    Raises ``ValueError`` (routes surface it as a friendly message) on a missing
    filename, a disallowed type, or an oversized file.
    """
    filename = getattr(file, "filename", None)
    if not filename:
        raise ValueError("Please choose a file to upload.")

    allowed_ext = DOCUMENT_EXTENSIONS | (extra_extensions or set())
    suffix = Path(filename).suffix.lower()
    content_type = (getattr(file, "content_type", "") or "").lower()
    ext_ok = suffix in allowed_ext
    type_ok = content_type in DOCUMENT_CONTENT_TYPES if content_type else False
    # Require the *extension* to be safe regardless of the declared MIME type —
    # the served file's handling keys off its extension, so an .svg claiming
    # image/png must still be rejected.
    if not ext_ok or (content_type and not type_ok and suffix not in allowed_ext):
        raise ValueError(
            f"“{filename}” isn’t an allowed document type. "
            "Attach a JPG, PNG, GIF, WebP, or PDF."
        )

    data = await file.read()
    if not data:
        raise ValueError("The uploaded file is empty.")
    if len(data) > max_bytes:
        raise ValueError(f"“{filename}” is larger than {max_bytes // 1_000_000} MB.")

    # Stored through the storage provider under a tenant-prefixed key (G11 · A14), not written
    # straight to UPLOADS_DIR. The old path returned "/uploads/<name>", which was served by an
    # unauthenticated static mount — any request could fetch any tenant's file. That mount is gone,
    # so writing there now would also produce an unreachable URL.
    return _store_bytes(data, filename, content_type=content_type)


def save_document_text(base_name: str, text: str, *, suffix: str = ".md") -> str:
    """Persist generated text (e.g. a saved AI report) and return its storage key.

    **`base_name` no longer appears in the stored name.** It used to:
    `f"{base_name}-{uuid4().hex[:8]}{suffix}"`, where `base_name` came from the report's title. That
    is two problems at once — 32 bits of randomness is enumerable, and the title is *content*, so
    the key itself disclosed what the report was about to anyone who saw a URL or a log line. The
    argument is kept for call-site compatibility and used only to pick the extension.
    """
    return _store_bytes(
        text.encode("utf-8"), f"{base_name}{suffix}", content_type="text/markdown"
    )


async def read_image_uploads(files, *, max_files: int = 6, max_bytes: int = 10_000_000) -> list[Attachment]:
    """Read uploaded files into image Attachments, validating count/size/type.

    Raises ValueError (which routes surface as a friendly message) when too many
    files are sent, a file is too large, or no valid image is present.
    """
    real = [f for f in files if getattr(f, "filename", "")]
    if not real:
        raise ValueError("Please attach at least one photo.")
    if len(real) > max_files:
        raise ValueError(f"Please attach at most {max_files} photos (got {len(real)}).")
    out: list[Attachment] = []
    for f in real:
        data = await f.read()
        if not data:
            continue
        if len(data) > max_bytes:
            raise ValueError(f"“{f.filename}” is larger than {max_bytes // 1_000_000} MB.")
        att = process_upload(f.filename, data, getattr(f, "content_type", "") or "")
        if att and att.is_image:
            out.append(att)
    if not out:
        raise ValueError("No usable image found — attach a JPG, PNG, GIF, or WebP photo.")
    return out
