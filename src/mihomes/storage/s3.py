"""S3 storage — the hosted backend (G11).

**Objects are private and access is a presigned URL with a short expiry.** No ACL is set, ever: an
object written with `public-read` is world-readable forever and no amount of application-level
checking takes that back. A14's wording is *"tenant files are never world-readable"*, and the only
way to guarantee that is never to make one public in the first place.

A presigned URL still carries a real consideration worth stating: **anyone holding the URL can
fetch the object until it expires.** That is acceptable for a link handed to the browser that
requested it, and it is why the default expiry is 15 minutes rather than days. It also means a
presigned URL must never be logged or embedded in a page that gets cached — the caller's problem,
but noted here because this module is where the reader will look.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mihomes.storage import ObjectNotFound, StorageError

__all__ = ["S3Storage"]


@dataclass
class S3Storage:
    bucket: str
    region: str | None = None
    endpoint_url: str | None = None
    _client: Any = field(default=None, repr=False, compare=False)

    @property
    def client(self):
        """Created lazily, so importing this module does not require credentials.

        A module-level client would make `import mihomes.storage.s3` fail on a machine with no AWS
        configuration — including during test collection.
        """
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3", region_name=self.region, endpoint_url=self.endpoint_url
            )
        return self._client

    def _not_found(self, error: Exception, key: str) -> bool:
        code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
        return code in ("404", "NoSuchKey", "NotFound")

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        try:
            # No ACL parameter, deliberately — see the module docstring. The bucket itself should
            # also have Block Public Access on; that is deployment configuration, and
            # `test_no_public_acl_is_ever_set` guards the application half.
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        except Exception as e:  # noqa: BLE001 — re-raised as our own type below
            raise StorageError(f"could not write {key}: {e}") from e

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except Exception as e:  # noqa: BLE001
            if self._not_found(e, key):
                raise ObjectNotFound(key) from None
            raise StorageError(f"could not read {key}: {e}") from e

    def delete(self, key: str) -> None:
        try:
            # S3 delete is already idempotent: deleting a missing key succeeds.
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"could not delete {key}: {e}") from e

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as e:  # noqa: BLE001
            if self._not_found(e, key):
                return False
            raise StorageError(f"could not stat {key}: {e}") from e

    def size(self, key: str) -> int:
        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except Exception as e:  # noqa: BLE001
            if self._not_found(e, key):
                raise ObjectNotFound(key) from None
            raise StorageError(f"could not stat {key}: {e}") from e

    def url(self, key: str, *, expires_in: int = 900) -> str | None:
        """A presigned GET URL, valid for `expires_in` seconds.

        Capped at one hour. A caller asking for a week-long URL has misunderstood what this is for:
        the link is for the browser that just requested the page, not a shareable artifact. Capping
        rather than raising keeps a mistaken caller working while limiting the exposure.
        """
        expires_in = max(1, min(int(expires_in), 3600))
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"could not presign {key}: {e}") from e
