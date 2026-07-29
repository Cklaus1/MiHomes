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
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mihomes.web import forms
from mihomes.web.forms import MAX_DOCUMENT_BYTES, read_document_upload
from mihomes.web.secure_static import SecureStaticFiles


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
    """Point the shared UPLOADS_DIR at a throwaway dir for every test here."""
    monkeypatch.setattr(forms, "UPLOADS_DIR", tmp_path)
    return tmp_path


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
    # Nothing should have been written.
    assert not any(_isolate_uploads.iterdir())


def test_pdf_accepted_and_stored_under_uploads_dir(_isolate_uploads):
    path = _run(read_document_upload(_FakeUpload("invoice.pdf", b"%PDF-1.4", content_type="application/pdf")))
    assert path.startswith("/uploads/")
    stored = list(_isolate_uploads.iterdir())
    assert len(stored) == 1
    # Stored name is randomised, never the client filename.
    assert stored[0].name != "invoice.pdf"
    assert stored[0].suffix == ".pdf"


def test_stored_path_is_outside_the_package(_isolate_uploads):
    # H34: uploads must not live inside the installed package tree.
    import mihomes

    pkg_root = next(iter(mihomes.__path__))
    assert str(_isolate_uploads).startswith(pkg_root) is False


# --- SecureStaticFiles serving headers ------------------------------------

@pytest.fixture
def serve_client(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "note.txt").write_text("hello")
    (tmp_path / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    app = FastAPI()
    app.mount("/uploads", SecureStaticFiles(directory=str(tmp_path)), name="uploads")
    return TestClient(app)


def test_pdf_served_inline_with_nosniff(serve_client):
    r = serve_client.get("/uploads/a.pdf")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" not in r.headers.get("content-disposition", "")


def test_image_served_inline_with_nosniff(serve_client):
    r = serve_client.get("/uploads/pic.png")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" not in r.headers.get("content-disposition", "")


def test_non_inline_type_forced_to_attachment(serve_client):
    # text/plain isn't inline-safe → must download, not render in page context.
    r = serve_client.get("/uploads/note.txt")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in r.headers.get("content-disposition", "")
