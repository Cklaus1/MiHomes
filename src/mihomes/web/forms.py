"""Helpers for parsing/validating web form input."""

from mihomes.services.ai.file_processor import Attachment, process_upload


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


def parse_money(value: str, field: str = "Value") -> float | None:
    """Parse a money/number form field tolerantly.

    Returns None for empty input. Strips ``$``, commas, and surrounding
    whitespace. Raises ``ValueError`` with a user-friendly message if the
    remaining text isn't a number, so routes can surface it instead of a 500.
    """
    if value is None or str(value).strip() == "":
        return None
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        raise ValueError(f"{field} must be a number (got “{value}”).") from None
