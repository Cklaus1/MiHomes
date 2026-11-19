"""G11 · §6 Step 11 — `StorageProvider`: where tenant files live (A14).

**The security property this exists for.** Before SPEC-002 the web app mounted the uploads
directory as static files:

    app.mount("/uploads", SecureStaticFiles(directory=UPLOADS_DIR))

with no authentication and no tenant check — so any request that could reach the app could fetch
**any** tenant's document, and one tenant knowing another's URL was enough. Filenames were
`uuid4().hex` (unguessable) for uploads but `{title-slug}-{8 hex}` for generated reports, which is
partially guessable from the title. Neither is access control.

A14's requirement is therefore two things at once, and both are enforced here:

1. **Keys are tenant-prefixed** — `{account_id}/{category}/{opaque}`. The account is part of the
   key, so a request for a key can be checked against the caller's account *before* any bytes are
   read, without a database lookup.
2. **Tenant files are never world-readable.** S3 objects are private; access is a presigned URL
   with a short expiry. The filesystem backend has no URL at all and must be served through a
   tenant-checked route — the static mount is removed.

**Why a key is opaque rather than derived from the title.** A key that embeds user-controlled text
leaks content (a filename is metadata: *"2026-divorce-settlement.pdf"*), and a short random suffix
invites enumeration. `uuid4().hex` is 122 bits of unguessable name, and the original extension is
preserved separately for content-type purposes rather than by trusting the path.

**`Document.file_path` now holds a key, not a path.** The column name is kept — renaming it means a
migration and touching every reader for no behavioural gain — but its *meaning* changed, so
`is_storage_key()` exists to tell the two apart during the transition. A pre-SPEC-002 row holds
something like `/static/uploads/abc.pdf`; a new row holds `<uuid>/documents/<hex>.pdf`.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

__all__ = [
    "ObjectNotFound",
    "StorageError",
    "StorageProvider",
    "build_key",
    "get_storage",
    "is_storage_key",
    "key_account",
    "reset_storage",
]


class StorageError(RuntimeError):
    """Storage could not satisfy the request."""


class ObjectNotFound(StorageError):
    """No object exists at that key."""


@runtime_checkable
class StorageProvider(Protocol):
    """The operations the application needs, and no more.

    Deliberately narrow: a provider that also exposed "list everything" or "make public" would
    invite a route that used them. `url()` returning a *time-limited* reference rather than a
    permanent one is the whole point of A14.
    """

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        """Store `data` at `key`, overwriting any existing object."""
        ...

    def get(self, key: str) -> bytes:
        """Return the bytes at `key`, or raise `ObjectNotFound`."""
        ...

    def delete(self, key: str) -> None:
        """Remove `key`. Succeeds whether or not it existed (idempotent)."""
        ...

    def exists(self, key: str) -> bool:
        ...

    def size(self, key: str) -> int:
        """Byte length at `key`, or raise `ObjectNotFound`.

        Used by the G16 importer's verify step, which must confirm an uploaded object is really
        there and really the right length before any row referencing it is committed.
        """
        ...

    def url(self, key: str, *, expires_in: int = 900) -> str | None:
        """A time-limited URL, or None if this backend cannot issue one.

        None is not a failure — the filesystem backend legitimately has no URL, and the caller
        falls back to streaming the bytes through a tenant-checked route. What no backend may do is
        return a *permanent, public* URL, which is the failure mode A14 forbids.
        """
        ...


# --------------------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------------------

def build_key(account_id: uuid.UUID | str, category: str, filename: str | None = None) -> str:
    """`{account_id}/{category}/{opaque}{ext}` — tenant-prefixed and content-free.

    Only the **extension** survives from `filename`, and only after validation. The stem is
    discarded: a key that embeds user text leaks metadata (a filename is content) and creates a
    second, weaker namespace to reason about.

    The extension is length-capped and character-restricted rather than trusted, because it ends up
    in a path and — for the filesystem backend — on a real disk.
    """
    ext = ""
    if filename:
        suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
        # A conservative allow-shape: a dot, then a short run of alphanumerics. Anything else
        # (path separators, "..", null bytes, a 200-character "extension") is dropped rather than
        # sanitised, since no legitimate extension looks like that.
        if 1 < len(suffix) <= 12 and suffix[1:].isalnum():
            ext = suffix

    if "/" in category or ".." in category or not category:
        raise ValueError(f"invalid storage category: {category!r}")

    return f"{account_id}/{category}/{uuid.uuid4().hex}{ext}"


def key_account(key: str) -> str | None:
    """The account segment of a key, or None if it does not look like one.

    This is what lets a download route authorise **before** touching storage: compare this against
    the caller's account and refuse on mismatch. No database lookup, no bytes read.
    """
    parts = PurePosixPath(key).parts
    if len(parts) < 3:
        return None
    try:
        return str(uuid.UUID(parts[0]))
    except ValueError:
        return None


def is_storage_key(value: str) -> bool:
    """Is this a storage key rather than a legacy filesystem path?

    `Document.file_path` holds both during the transition: pre-SPEC-002 rows carry things like
    `/static/uploads/abc.pdf`, new rows carry `<uuid>/documents/<hex>.pdf`. Callers need to tell
    them apart, and guessing from a leading slash alone would misread a Windows path.
    """
    return key_account(value) is not None


# --------------------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------------------

_provider: StorageProvider | None = None


def get_storage(
    *, refresh: bool = False, override_root: str | Path | None = None
) -> StorageProvider:
    """The configured provider, created once per process.

    `STORAGE_PROVIDER=s3` selects S3; anything else (or unset) selects the filesystem backend,
    because a **local default must not be a cloud call**: an unconfigured dev machine should write
    to disk, not fail against a bucket that does not exist.

    Selecting S3 with incomplete configuration raises rather than silently falling back to the
    filesystem — a hosted deployment quietly writing tenant documents to a container's ephemeral
    disk is worse than not starting.
    """
    global _provider
    if _provider is not None and not refresh and override_root is None:
        return _provider

    name = (os.environ.get("STORAGE_PROVIDER") or "filesystem").strip().lower()
    if name in ("s3", "aws", "minio"):
        from mihomes.storage.s3 import S3Storage

        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise StorageError(
                "STORAGE_PROVIDER=s3 but S3_BUCKET is not set. Refusing to fall back to local "
                "disk: a hosted deployment writing tenant documents to an ephemeral container "
                "filesystem loses them silently."
            )
        _provider = S3Storage(
            bucket=bucket,
            region=os.environ.get("S3_REGION"),
            endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
        )
    elif name in ("filesystem", "fs", "local"):
        import mihomes.config as config
        from mihomes.storage.filesystem import FilesystemStorage

        # `import mihomes.config as config` and read the attribute live, never
        # `from mihomes.config import MEDIA_DIR`. That path is computed from MIHOMES_DIR **at config
        # import time**, so a test setting the env var afterwards changes nothing — and this exact
        # trap wrote 8 test files into the author's real ~/.mihomes/media before it was caught.
        # `lessons.md` records the same hazard for DB_URL; reading live is what makes
        # `monkeypatch.setattr(config, "MEDIA_DIR", tmp)` work.
        root = Path(override_root) if override_root else config.MEDIA_DIR / "objects"
        _provider = FilesystemStorage(root=root)
    else:
        raise StorageError(
            f"unknown STORAGE_PROVIDER {name!r} — expected 's3' or 'filesystem'"
        )
    return _provider


def reset_storage() -> None:
    """Drop the cached provider. For tests that change the environment."""
    global _provider
    _provider = None
