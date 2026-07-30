"""File upload processor for AI attachments."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

IMAGE_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".html", ".xml", ".yaml", ".yml", ".log"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# M37: cap upload sizes so a giant file (e.g. a 200MB log) can't be forwarded to
# the model verbatim. Text is truncated with a visible marker; an oversized image
# is rejected outright (it can't be meaningfully truncated).
MAX_TEXT_CHARS = 200_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MiB
_TEXT_TRUNCATION_MARKER = "\n\n[... file truncated — exceeded {limit:,} character upload cap ...]"


@dataclass
class Attachment:
    filename: str
    is_image: bool
    # For text files
    text_content: str = ""
    # For images
    base64_data: str = ""
    media_type: str = ""


def process_upload(filename: str, content_bytes: bytes, content_type: str = "") -> Attachment | None:
    """Process a single uploaded file into an Attachment. Returns None for unsupported types."""
    import os
    ext = os.path.splitext(filename)[1].lower()

    if ext in IMAGE_EXTENSIONS or content_type in IMAGE_TYPES:
        # M37: reject an oversized image — it can't be truncated meaningfully and
        # would balloon the request payload.
        if len(content_bytes) > MAX_IMAGE_BYTES:
            return None
        media_type = IMAGE_TYPES.get(content_type) or IMAGE_TYPES.get(f"image/{ext.lstrip('.')}", "image/jpeg")
        return Attachment(
            filename=filename,
            is_image=True,
            base64_data=base64.standard_b64encode(content_bytes).decode(),
            media_type=media_type,
        )

    if ext in TEXT_EXTENSIONS or content_type.startswith("text/"):
        try:
            text = content_bytes.decode("utf-8", errors="replace")
        except Exception:
            text = content_bytes.decode("latin-1", errors="replace")
        return Attachment(filename=filename, is_image=False, text_content=_cap_text(text))

    if ext == ".pdf" or content_type == "application/pdf":
        text = _extract_pdf_text(content_bytes)
        return Attachment(filename=filename, is_image=False, text_content=_cap_text(text))

    return None


def _cap_text(text: str) -> str:
    """M37: truncate over-long extracted text with a visible marker."""
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[:MAX_TEXT_CHARS] + _TEXT_TRUNCATION_MARKER.format(limit=MAX_TEXT_CHARS)


def _extract_pdf_text(content_bytes: bytes) -> str:
    try:
        import pdfplumber
        import io
        with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n\n".join(p for p in pages if p.strip())
    except ImportError:
        return "[PDF uploaded — install pdfplumber to extract text: pip install pdfplumber]"
    except Exception as e:
        return f"[PDF extraction failed: {e}]"


def attachments_to_text_block(attachments: list[Attachment]) -> str:
    """Return a text representation of non-image attachments for providers that don't support vision."""
    parts = []
    for att in attachments:
        if not att.is_image and att.text_content:
            parts.append(f"--- Attached file: {att.filename} ---\n{att.text_content}\n---")
        elif att.is_image:
            parts.append(f"[Image attached: {att.filename}]")
    return "\n\n".join(parts)
