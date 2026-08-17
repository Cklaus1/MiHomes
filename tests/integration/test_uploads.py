"""Upload hardening regression tests (spec D6 / H34 / M45).

Covers the four defects the shared ``read_document_upload`` helper closes and
the serving-side headers ``SecureStaticFiles`` adds:

* a client-named ``.svg``/``.html`` (stored-XSS vector) is rejected,
* a legitimate PDF is accepted and stored under the user-data ``UPLOADS_DIR``
  (outside the package, so ``pip upgrade`` can't wipe it — H34),
* an oversized body is rejected before it is written (OOM guard),
* served files carry ``X-Content-Type-Options: nosniff`` always, and anything
  not inline-safe is forced to ``Content-Disposition: attachment`` (D6).
"""

import asyncio

import pytest

from mihomes.web.forms import MAX_DOCUMENT_BYTES, read_document_upload


class _FakeUpload:
    """Minimal stand-in for Starlette's UploadFile (filename/content_type/read)."""

    def __init__(self, filename, data=b"x", content_type=""):
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self):
        return self._data


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _isolate_uploads(tmp_path, monkeypatch):
    """Point storage at a throwaway root and bind an account for every test here.

    Uploads no longer go to `UPLOADS_DIR` directly — G11 routes them through the storage provider
    under a tenant-prefixed key, so the fixture has to supply both a storage root and an account
    context. `require_account()` raising without one is deliberate: a file with no tenant has
    nowhere to live and nobody who may read it.

    **Uses `override_root`, not `monkeypatch.setenv("MIHOMES_DIR")`.** `config.MEDIA_DIR` is
    computed from that variable **at config import time**, so setting it in a fixture changes
    nothing — and the first version of this fixture therefore wrote 8 test files into the author's
    real `~/.mihomes/media/objects`. `lessons.md` already recorded the same trap for `DB_URL`;
    passing the root explicitly removes the possibility rather than relying on getting the
    monkeypatch right.
    """
    import uuid as _uuid

    from mihomes.storage import get_storage, reset_storage
    from mihomes.tenancy import account_context

    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
    reset_storage()
    root = tmp_path / "objects"
    get_storage(refresh=True, override_root=root)
    with account_context(_uuid.uuid4()):
        yield root
    reset_storage()


# --- read_document_upload validation --------------------------------------

def test_svg_upload_rejected():
    with pytest.raises(ValueError):
        _run(read_document_upload(_FakeUpload("logo.svg", b"<svg onload=alert(1)>")))


def test_html_upload_rejected():
    with pytest.raises(ValueError):
        _run(read_document_upload(_FakeUpload("x.html", b"<script>alert(1)</script>")))


def test_svg_masquerading_as_png_rejected():
    # Declared image/png but the *extension* is .svg — must still be rejected,
    # because the served file's handling keys off the extension.
    with pytest.raises(ValueError):
        _run(read_document_upload(_FakeUpload("x.svg", b"<svg/>", content_type="image/png")))


def test_missing_filename_rejected():
    with pytest.raises(ValueError):
        _run(read_document_upload(_FakeUpload("", b"data")))


def test_oversized_upload_rejected(_isolate_uploads):
    big = b"0" * (MAX_DOCUMENT_BYTES + 1)
    with pytest.raises(ValueError):
        _run(read_document_upload(_FakeUpload("huge.pdf", big, content_type="application/pdf")))
    # Nothing should have been written. The storage root may not even exist yet — the filesystem
    # backend creates directories lazily on the first `put`, so "no directory" is the strongest
    # possible form of "nothing was written" rather than a missing-path bug.
    written = list(_isolate_uploads.rglob("*")) if _isolate_uploads.exists() else []
    assert not written, f"a rejected oversized upload still wrote {written}"


def test_pdf_accepted_and_stored_under_a_tenant_key(_isolate_uploads):
    """A legitimate PDF is accepted and stored under a tenant-prefixed key (G11 · A14).

    Was `assert path.startswith("/uploads/")`. That URL was served by an unauthenticated static
    mount, so it is gone; the return value is now an opaque storage key whose first segment is the
    owning account.
    """
    from mihomes.storage import is_storage_key, key_account

    key = _run(read_document_upload(
        _FakeUpload("invoice.pdf", b"%PDF-1.4", content_type="application/pdf")
    ))
    assert is_storage_key(key), f"expected a storage key, got {key!r}"
    assert key_account(key) is not None, "the key must carry the owning account"
    assert key.endswith(".pdf")
    # The client filename never appears in the key — a filename is content.
    assert "invoice" not in key

    stored = [p for p in _isolate_uploads.rglob("*") if p.is_file()]
    assert len(stored) == 1
    assert stored[0].name != "invoice.pdf"
    assert stored[0].suffix == ".pdf"


def test_stored_path_is_outside_the_package(_isolate_uploads):
    # H34: uploads must not live inside the installed package tree.
    import mihomes

    pkg_root = next(iter(mihomes.__path__))
    assert str(_isolate_uploads).startswith(pkg_root) is False


# --- serving headers ------------------------------------------------------
#
# The three tests that lived here drove `SecureStaticFiles` through the `/uploads` mount, asserting
# nosniff and forced-attachment on downloads. **G11 removed that mount** — it served every tenant's
# documents to any request that could reach the app, and a static mount has nowhere to put an
# authorisation check.
#
# The behaviour they guarded was not dropped with them. The tenant-checked download route sets the
# same headers, and `tests/web/test_document_download.py` asserts them
# (`test_owner_can_download_its_own_document` checks `X-Content-Type-Options: nosniff` and
# `Cache-Control: private, no-store`) alongside the cross-tenant refusal those tests could not
# express at all.
