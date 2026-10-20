"""Documents page — upload a file, then quick-view it.

Uploads go through `read_document_upload`, so they land in storage under a tenant-prefixed key, not
a URL. The page must turn that key into `/documents/file/<key>` (the authorising download route);
used raw, it resolves relative to the page and 404s.
"""

from __future__ import annotations

import pytest

from mihomes.storage import get_storage, reset_storage
from mihomes.web.deps import _doc_kind, _doc_url


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """A filesystem provider in a temp dir — see `test_document_download.storage` for why not MIHOMES_DIR."""
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
    reset_storage()
    provider = get_storage(refresh=True, override_root=tmp_path / "objects")
    yield provider
    reset_storage()


def _file_url(body: str) -> str:
    start = body.index('data-preview-fp="/documents/file/') + len('data-preview-fp="')
    return body[start:body.index('"', start)]


def test_uploaded_pdf_gets_a_preview_that_serves_inline(client, storage):
    resp = client.post(
        "/documents/",
        data={"title": "Roof warranty", "document_type": "warranty", "file_path": ""},
        files={"file": ("warranty.pdf", b"%PDF-1.4 roof", "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    assert "Preview PDF" in resp.text

    url = _file_url(resp.text)
    served = client.get(url)
    assert served.status_code == 200
    assert served.content == b"%PDF-1.4 roof"
    assert served.headers["content-disposition"].startswith("inline")


def test_disallowed_file_is_refused_and_nothing_is_saved(client, storage):
    resp = client.post(
        "/documents/",
        data={"title": "Sneaky", "document_type": "other", "file_path": ""},
        files={"file": ("logo.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
    )
    assert resp.status_code == 200
    assert "isn’t an allowed document type" in resp.text
    assert "The document was not saved." in resp.text
    assert ">Sneaky<" not in resp.text.replace("\n", "").replace(" ", "")


def test_link_only_document_still_works_without_a_file(client, storage):
    resp = client.post(
        "/documents/",
        data={"title": "Pool SOP", "document_type": "manual", "file_path": "https://example.com/pool"},
        files={"file": ("", b"", "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    assert 'href="https://example.com/pool"' in resp.text


def test_editing_without_a_new_file_keeps_the_upload(client, storage):
    created = client.post(
        "/documents/",
        data={"title": "Gate photo", "document_type": "other", "file_path": ""},
        files={"file": ("gate.png", b"\x89PNG fake", "image/png")},
    )
    url = _file_url(created.text)
    key = url.removeprefix("/documents/file/")
    # The edit form carries the key in a hidden field; resubmitting it must not lose the file.
    assert f'type="hidden" name="file_path" value="{key}"' in created.text

    slug = "gate-photo"  # auto-generated from the title
    assert f'hx-post="/documents/{slug}/edit"' in created.text
    edited = client.post(
        f"/documents/{slug}/edit",
        data={"title": "Gate photo (front)", "document_type": "other", "file_path": key},
    )
    assert edited.status_code == 200, edited.text
    assert f'data-preview-fp="{url}"' in edited.text
    assert "Gate photo (front)" in edited.text


class TestFilters:
    def test_storage_key_becomes_the_download_route(self):
        key = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b/documents/abc123.pdf"
        assert _doc_url(key) == f"/documents/file/{key}"

    @pytest.mark.parametrize("fp", ["https://drive.google.com/x", "/static/uploads/a.png", "—", "", None])
    def test_everything_else_passes_through(self, fp):
        assert _doc_url(fp) == (fp or "")

    @pytest.mark.parametrize(
        ("name", "kind"), [("c.PDF", "pdf"), ("x.jpeg", "image"), ("x.webp", "image"), ("x.md", "")],
    )
    def test_kind_of_an_upload(self, name, kind):
        assert _doc_kind(f"0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b/documents/{name}") == kind

    @pytest.mark.parametrize(
        "fp",
        [
            "javascript:alert(1)//x.pdf",
            'https://x/a" onerror="alert(1)" y=".png',
            "https://example.com/manual.pdf",
            "/static/uploads/old.png",
            None,
        ],
    )
    def test_typed_links_are_never_previewable(self, fp):
        """`previewDoc()` builds HTML from the URL unescaped — only storage keys may reach it."""
        assert _doc_kind(fp) == ""


def test_a_hostile_typed_link_gets_no_preview(client, storage):
    resp = client.post(
        "/documents/",
        data={"title": "Bait", "document_type": "other", "file_path": "javascript:alert(1)//x.pdf"},
    )
    assert resp.status_code == 200, resp.text
    # base.html's delegated handler mentions `data-preview-fp` itself, so look for the attribute.
    assert 'data-preview-fp="javascript' not in resp.text
    assert "Preview PDF" not in resp.text
