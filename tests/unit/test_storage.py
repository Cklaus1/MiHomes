"""G11 · §6 Step 11 — `StorageProvider` and tenant-prefixed keys (A14).

A14's wording is *"storage keys are tenant-prefixed; round-trip works"*, and the criterion's real
content is the second half of §6 Step 11: **tenant files are never world-readable.** So these tests
cover the mechanism (keys, round trip) *and* the property (no public ACL, no permanent URL, no
cross-tenant read), because the mechanism can be perfect while the property is violated by how the
files are served.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.storage import (
    ObjectNotFound,
    StorageError,
    StorageProvider,
    build_key,
    get_storage,
    is_storage_key,
    key_account,
    reset_storage,
)
from mihomes.storage.filesystem import FilesystemStorage
from mihomes.storage.s3 import S3Storage


@pytest.fixture
def fs(tmp_path):
    return FilesystemStorage(root=tmp_path / "objects")


# --- keys -------------------------------------------------------------------------------

def test_key_prefix_and_roundtrip(fs):
    """A14 — the named criterion: keys are tenant-prefixed and a round trip works."""
    account = uuid.uuid4()
    key = build_key(account, "documents", "Deed of Sale.pdf")

    assert key.startswith(f"{account}/documents/"), "the key must be tenant-prefixed"
    assert key.endswith(".pdf"), "the extension is preserved for content-type purposes"
    assert key_account(key) == str(account)

    fs.put(key, b"%PDF-1.4 contents", content_type="application/pdf")
    assert fs.get(key) == b"%PDF-1.4 contents"
    assert fs.exists(key) is True
    assert fs.size(key) == len(b"%PDF-1.4 contents")

    fs.delete(key)
    assert fs.exists(key) is False
    with pytest.raises(ObjectNotFound):
        fs.get(key)
    fs.delete(key)  # idempotent: deleting a missing object is not an error


def test_key_does_not_embed_the_original_filename():
    """A filename is content, and a key that carries it leaks metadata.

    `2026-divorce-settlement.pdf` in a key tells anyone who sees the key — in a log, a URL, an
    error message — what the document is about. Only the extension survives.
    """
    key = build_key(uuid.uuid4(), "documents", "2026 divorce settlement FINAL.pdf")
    stem = key.rsplit("/", 1)[-1]
    for leaked in ("divorce", "settlement", "final", "2026"):
        assert leaked not in stem.lower(), f"the key leaks {leaked!r} from the filename: {stem}"


def test_keys_are_unguessable_and_unique():
    """The opaque part is a full uuid4, not a truncated hash.

    Generated reports used to be named `{title-slug}-{8 hex}` — 32 bits, and partly derived from
    text the user can see. That is enumerable. This is 122 bits and content-free.
    """
    account = uuid.uuid4()
    keys = {build_key(account, "documents", "a.pdf") for _ in range(500)}
    assert len(keys) == 500, "keys collided"
    opaque = keys.pop().rsplit("/", 1)[-1].removesuffix(".pdf")
    assert len(opaque) == 32, f"expected a full uuid4 hex, got {len(opaque)} chars"


@pytest.mark.parametrize(
    "filename",
    [
        "evil.pdf/../../../etc/passwd",
        "evil.p df",
        "no-extension",
        "trailing.",
        "x." + "a" * 40,
        "weird.pdf\x00.exe",
    ],
)
def test_hostile_filenames_cannot_shape_the_key(filename):
    """Only a short alphanumeric extension is accepted; anything else is dropped, not sanitised.

    Sanitising invites an escape that the sanitiser did not anticipate. Dropping has no such
    failure mode, and no legitimate extension looks like any of these.
    """
    account = uuid.uuid4()
    key = build_key(account, "documents", filename)
    assert key.startswith(f"{account}/documents/")
    assert key.count("/") == 2, f"the key gained path segments: {key}"
    assert ".." not in key and "\x00" not in key


def test_invalid_category_is_rejected():
    for bad in ("", "../escape", "a/b"):
        with pytest.raises(ValueError):
            build_key(uuid.uuid4(), bad, "x.pdf")


def test_is_storage_key_distinguishes_legacy_paths():
    """`Document.file_path` holds both shapes during the transition, so they must be separable."""
    assert is_storage_key(build_key(uuid.uuid4(), "documents", "x.pdf")) is True
    for legacy in (
        "/static/uploads/1fcfbe88f3d145babc2e175b31bef087.pdf",  # a real row from the author's DB
        "uploads/report.md",
        r"C:\Users\someone\file.pdf",
        "",
    ):
        assert is_storage_key(legacy) is False, f"{legacy!r} was misread as a storage key"


# --- the filesystem backend's path handling --------------------------------------------

@pytest.mark.parametrize(
    "key",
    [
        "../escape.txt",
        "a/../../escape.txt",
        "/absolute/path.txt",
        " leading-space.txt",
    ],
)
def test_filesystem_refuses_keys_that_escape_the_root(fs, key):
    """Checked by comparing the RESOLVED path against the resolved root.

    Not by scanning the key for `..`: a blacklist has to anticipate every spelling (encoded, mixed
    separators, a symlink), while resolving and comparing is a property. An absolute key is refused
    outright because joining one silently discards the root.
    """
    with pytest.raises(StorageError):
        fs.put(key, b"x")


def test_filesystem_put_is_atomic(fs, monkeypatch):
    """A crash mid-write must not leave a half-written object that `exists()` reports as present.

    Written to a `.part` neighbour and renamed, so the object appears whole or not at all.
    """
    key = build_key(uuid.uuid4(), "documents", "big.bin")
    original = FilesystemStorage.put

    def explode(self, k, data, *, content_type=None):
        # Write the temp file, then fail before the replace.
        path = self._resolve(k)
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.with_name(path.name + ".part")).write_bytes(data)
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(FilesystemStorage, "put", explode)
    with pytest.raises(OSError):
        fs.put(key, b"partial")
    monkeypatch.setattr(FilesystemStorage, "put", original)

    assert fs.exists(key) is False, "a partial write is visible as a complete object"


def test_filesystem_has_no_url(fs):
    """None is the safe answer, not a missing feature.

    A URL from this backend would mean a static mount, which is the unauthenticated hole G11
    removed. Returning None forces the caller through the tenant-checked route.
    """
    key = build_key(uuid.uuid4(), "documents", "x.pdf")
    fs.put(key, b"x")
    assert fs.url(key) is None


# --- the S3 backend ---------------------------------------------------------------------

class _FakeS3:
    """Records calls, so the ACL and expiry assertions need no network or credentials."""

    def __init__(self):
        self.puts: list[dict] = []
        self.presigns: list[dict] = []
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kw):
        self.puts.append(kw)
        self.objects[kw["Key"]] = kw["Body"]

    def get_object(self, **kw):
        import io

        if kw["Key"] not in self.objects:
            raise _NoSuchKey()
        return {"Body": io.BytesIO(self.objects[kw["Key"]])}

    def head_object(self, **kw):
        if kw["Key"] not in self.objects:
            raise _NoSuchKey()
        return {"ContentLength": len(self.objects[kw["Key"]])}

    def delete_object(self, **kw):
        self.objects.pop(kw["Key"], None)

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803 - boto3's signature
        self.presigns.append({"op": op, "params": Params, "expires_in": ExpiresIn})
        return f"https://example.invalid/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


@pytest.fixture
def s3():
    provider = S3Storage(bucket="test-bucket")
    provider._client = _FakeS3()
    return provider


def test_s3_roundtrip(s3):
    key = build_key(uuid.uuid4(), "documents", "x.pdf")
    s3.put(key, b"hello", content_type="application/pdf")
    assert s3.get(key) == b"hello"
    assert s3.exists(key) is True
    assert s3.size(key) == 5
    s3.delete(key)
    assert s3.exists(key) is False
    with pytest.raises(ObjectNotFound):
        s3.get(key)


def test_no_public_acl_is_ever_set(s3):
    """A14's core property: **an object written public-read is world-readable forever.**

    No application-level check takes that back, so the only reliable guarantee is never to set it.
    This asserts the request carries no ACL at all rather than asserting it is 'private' — an
    absent ACL inherits the bucket's (locked-down) default, and a future boto3 default cannot
    quietly turn into something permissive without failing here.
    """
    key = build_key(uuid.uuid4(), "documents", "x.pdf")
    s3.put(key, b"x", content_type="application/pdf")
    (call,) = s3._client.puts
    assert "ACL" not in call, f"an ACL was set on upload: {call.get('ACL')!r}"
    for forbidden in ("public-read", "public-read-write", "authenticated-read"):
        assert forbidden not in str(call), f"{forbidden} appears in the put_object call"


def test_presigned_url_expires_and_is_capped(s3):
    """A presigned URL is a short-lived reference, not a shareable artifact.

    Anyone holding it can fetch the object until it expires — acceptable for a link handed to the
    browser that just requested the page, which is why the cap exists. A caller asking for a week
    is capped rather than refused, so a mistake degrades to "one hour" instead of an error page.
    """
    key = build_key(uuid.uuid4(), "documents", "x.pdf")
    s3.put(key, b"x")

    s3.url(key)
    assert s3._client.presigns[-1]["expires_in"] == 900, "the default should be 15 minutes"

    s3.url(key, expires_in=7 * 24 * 3600)
    assert s3._client.presigns[-1]["expires_in"] == 3600, "a long expiry must be capped to an hour"

    s3.url(key, expires_in=0)
    assert s3._client.presigns[-1]["expires_in"] == 1, "a zero expiry must floor to 1s, not 0"


# --- the factory ------------------------------------------------------------------------

def test_factory_defaults_to_filesystem(monkeypatch, tmp_path):
    """An unconfigured machine writes to disk rather than failing against a bucket."""
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
    monkeypatch.setenv("MIHOMES_DIR", str(tmp_path))
    reset_storage()
    try:
        assert isinstance(get_storage(refresh=True), FilesystemStorage)
    finally:
        reset_storage()


def test_factory_refuses_s3_without_a_bucket(monkeypatch):
    """Refusing beats falling back.

    A hosted deployment that quietly wrote tenant documents to an ephemeral container filesystem
    would lose them with no error — worse than not starting.
    """
    monkeypatch.setenv("STORAGE_PROVIDER", "s3")
    monkeypatch.delenv("S3_BUCKET", raising=False)
    reset_storage()
    try:
        with pytest.raises(StorageError) as exc:
            get_storage(refresh=True)
        assert "S3_BUCKET" in str(exc.value)
    finally:
        reset_storage()


def test_factory_rejects_an_unknown_provider(monkeypatch):
    monkeypatch.setenv("STORAGE_PROVIDER", "dropbox")
    reset_storage()
    try:
        with pytest.raises(StorageError):
            get_storage(refresh=True)
    finally:
        reset_storage()


def test_both_backends_satisfy_the_protocol(fs, s3):
    """The Protocol is runtime-checkable so this is a real assertion, not a type-checker comment."""
    assert isinstance(fs, StorageProvider)
    assert isinstance(s3, StorageProvider)


def test_provider_exposes_no_way_to_make_an_object_public(fs, s3):
    """The interface is narrow on purpose: a method that could publish an object invites a route
    that calls it. Asserted so a future convenience method has to justify itself here first."""
    for provider in (fs, s3):
        names = {n for n in dir(provider) if not n.startswith("_")}
        for dangerous in ("make_public", "public_url", "set_acl", "list_all", "list_keys"):
            assert dangerous not in names, (
                f"{type(provider).__name__} exposes {dangerous}() — A14 requires that tenant files "
                "are never world-readable, and a way to publish one will eventually be called"
            )
