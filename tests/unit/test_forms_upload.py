"""SPEC-013 A19 — `store_validated_upload`: the extension decides, never the client header."""

from __future__ import annotations

import asyncio
import io
import uuid

import pytest

from mihomes.tenancy.context import account_context
from mihomes.web import forms


class _File:
    def __init__(self, name, data, content_type):
        self.filename = name
        self.content_type = content_type
        self.file = io.BytesIO(data)

    async def read(self, n=-1):
        return self.file.read(n)


@pytest.fixture
def storage(tmp_path):
    from mihomes.storage import get_storage, reset_storage

    reset_storage()
    yield get_storage(refresh=True, override_root=tmp_path / "objects")
    reset_storage()


@pytest.fixture
def acct():
    a = uuid.uuid4()
    with account_context(a):
        yield a


def test_store_validated_upload_rules(storage, acct):
    png = b"\x89PNG" + b"0" * 10
    for bad in ("x.svg", "x.html", "x.exe", "noext", ""):
        with pytest.raises(ValueError):
            forms.store_validated_upload_sync(_File(bad, png, "image/png"), category="chat")
    with pytest.raises(ValueError):
        forms.store_validated_upload_sync(_File("big.png", b"0" * 101, "image/png"),
                                          category="chat", max_bytes=100)
    with pytest.raises(ValueError):
        forms.store_validated_upload_sync(_File("empty.png", b"", "image/png"), category="chat")

    up = forms.store_validated_upload_sync(_File("../../evil.PNG", png, "text/html"), category="chat")
    assert up.content_type == "image/png"            # the header was ignored
    assert up.filename == "evil.PNG" and up.size == len(png)
    assert up.key.startswith(f"{acct}/chat/") and up.key.endswith(".png")
    assert storage.get(up.key) == png

    pdf = asyncio.run(forms.store_validated_upload(_File("Invoice.pdf", b"%PDF", "image/png"),
                                                   category="chat"))
    assert pdf.content_type == "application/pdf"


def test_read_document_upload_unchanged(storage, acct):
    key = asyncio.run(forms.read_document_upload(_File("a.pdf", b"%PDF", "application/pdf")))
    assert key.startswith(f"{acct}/documents/")
