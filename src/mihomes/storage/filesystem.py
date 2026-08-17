"""Filesystem storage — the development backend (G11).

Writes objects under a root directory, one file per key. Intended for local single-machine use;
a hosted deployment uses S3, because a container filesystem is ephemeral.

**`url()` returns None, deliberately.** This backend has no way to hand out a time-limited
reference, and the alternative — mounting the root as static files — is exactly the hole G11
closes: that mount had no tenant check, so any request could read any tenant's document. Returning
None forces the caller down the tenant-checked streaming route instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mihomes.storage import ObjectNotFound, StorageError

__all__ = ["FilesystemStorage"]


@dataclass
class FilesystemStorage:
    root: Path

    def _resolve(self, key: str) -> Path:
        """Map a key to a path, refusing anything that escapes the root.

        **Checked by resolved path, not by inspecting the key for `..`.** A substring or segment
        check has to anticipate every spelling (`..`, url-encoded, a symlink, a Windows `..\\`);
        comparing the *resolved* path against the resolved root is a property rather than a
        blacklist. `strict=False` so a not-yet-existing object still resolves.
        """
        if not key or key != key.strip():
            raise StorageError(f"invalid storage key: {key!r}")
        # Reject absolute keys outright: joining one would discard the root entirely.
        if PurePosixPath(key).is_absolute() or Path(key).is_absolute():
            raise StorageError(f"storage key must be relative: {key!r}")

        root = self.root.resolve()
        candidate = (root / key).resolve()
        if candidate != root and root not in candidate.parents:
            raise StorageError(
                f"storage key escapes the storage root: {key!r} -> {candidate}"
            )
        return candidate

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        # content_type is accepted for protocol parity and is not persisted: the filesystem has
        # nowhere to keep it, and the download route derives it from the key's extension. Storing
        # it in a sidecar file would be a second source of truth.
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary neighbour and replace, so a crash mid-write cannot leave a
        # half-written object that `exists()` would report as present.
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise ObjectNotFound(key) from None

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def size(self, key: str) -> int:
        path = self._resolve(key)
        try:
            return path.stat().st_size
        except FileNotFoundError:
            raise ObjectNotFound(key) from None

    def url(self, key: str, *, expires_in: int = 900) -> str | None:
        """No URL. See the module docstring — this is the safe answer, not a missing feature."""
        return None
