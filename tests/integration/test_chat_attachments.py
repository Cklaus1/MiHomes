"""SPEC-013 A15, A16, A18, A21 (+ A19 through the route) — chat attachments on the app role."""

from __future__ import annotations

import io
import re
import uuid

import pytest

from mihomes.models.chat_native import ChatAttachment
from mihomes.services.chat import conversations as conv
from mihomes.services.chat.attachments import download_headers
from tests.integration.chat_helpers import app_client, app_session, estate

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%fake\n"


@pytest.fixture(autouse=True)
def storage(tmp_path):
    from mihomes.storage import get_storage, reset_storage

    reset_storage()
    yield get_storage(refresh=True, override_root=tmp_path / "objects")
    reset_storage()


_REAL_DISPATCH = None


@pytest.fixture(autouse=True)
def _no_ai(monkeypatch):
    """The route's background dispatch is a no-op here; A21 calls the real one directly."""
    global _REAL_DISPATCH
    from mihomes.services.chat import ai_bridge

    _REAL_DISPATCH = ai_bridge.dispatch
    monkeypatch.setattr(ai_bridge, "dispatch", lambda *a, **k: None)


def _rooms(app_engine, e):
    with app_session(app_engine, e.account_id) as s:
        ids = {"belle": conv.get_or_create_property(s, e.belle).id,
               "general": conv.get_or_create_estate(s).id}
        s.commit()
        return ids


def _upload(client, conversation_id, files, body=""):
    return client.post(f"/chat/c/{conversation_id}/posts", data={"body": body},
                       files=[("files", f) for f in files])


def _attachment_ids(r) -> list[str]:
    return re.findall(r'/chat/file/([0-9a-f-]{36})', r.text)


def test_download_404_for_non_visible_member_app_role(app_client, app_engine, estate, storage):
    """A15 — Blue staff get 404 on a Belle photo; Belle staff get 200 with the bytes."""
    e = estate
    rooms = _rooms(app_engine, e)
    owner = app_client(e.account_id, e.owner)
    r = _upload(owner, rooms["belle"], [("pump.png", io.BytesIO(PNG), "image/png")], "pump")
    assert r.status_code == 200, r.text
    (att_id,) = set(_attachment_ids(r))

    blue = app_client(e.account_id, e.staff_blue).get(f"/chat/file/{att_id}")
    assert blue.status_code == 404 and PNG not in blue.content
    belle = app_client(e.account_id, e.staff_belle).get(f"/chat/file/{att_id}")
    assert belle.status_code == 200 and belle.content == PNG


def test_download_404_deleted_and_cross_account(app_client, app_engine, estate, _pg_engine,
                                                account_b):
    """A16 — 404 after soft delete; another account's id is 404 too."""
    from tests.integration.chat_helpers import cleanup_estate, seed_estate

    e = estate
    rooms = _rooms(app_engine, e)
    owner = app_client(e.account_id, e.owner)
    r = _upload(owner, rooms["general"], [("a.png", io.BytesIO(PNG), "image/png")], "look")
    (att_id,) = set(_attachment_ids(r))
    post_id = re.search(r'id="post-([0-9a-f-]{36})"', r.text).group(1)

    other = seed_estate(_pg_engine, account_b)
    try:
        assert app_client(other.account_id, other.owner).get(f"/chat/file/{att_id}").status_code == 404
    finally:
        cleanup_estate(_pg_engine, other)

    assert owner.get(f"/chat/file/{att_id}").status_code == 200
    assert owner.post(f"/chat/posts/{post_id}/delete").status_code == 200
    assert owner.get(f"/chat/file/{att_id}").status_code == 404


def test_download_headers(app_client, app_engine, estate):
    """A18 — images inline, PDFs as attachments, nosniff + no-store + sandbox; a hostile filename
    cannot break the header."""
    e = estate
    rooms = _rooms(app_engine, e)
    owner = app_client(e.account_id, e.owner)
    r = _upload(owner, rooms["general"], [("p.png", io.BytesIO(PNG), "image/png"),
                                          ("invoice.pdf", io.BytesIO(PDF), "application/pdf")])
    img_id, pdf_id = _attachment_ids(r)[0], _attachment_ids(r)[-1]
    img, pdf = owner.get(f"/chat/file/{img_id}"), owner.get(f"/chat/file/{pdf_id}")
    assert img.headers["content-disposition"].startswith("inline")
    assert pdf.headers["content-disposition"].startswith("attachment")
    for resp in (img, pdf):
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["cache-control"] == "private, no-store"
        assert resp.headers["content-security-policy"].startswith("sandbox")
    assert img.headers["content-type"] == "image/png"
    assert pdf.headers["content-type"] == "application/pdf"

    hostile = ChatAttachment(id=uuid.uuid4(), post_id=uuid.uuid4(), storage_key="k",
                             filename='a"; x=1\r\nSet-Cookie: s=1/../../é.png',
                             content_type="image/png", size_bytes=1)
    header = download_headers(hostile)["Content-Disposition"]
    assert "\r" not in header and "\n" not in header
    ascii_part = header.split("filename*=")[0]
    assert ascii_part.count('"') == 2 and "../" not in ascii_part


def test_upload_rules_through_the_route(app_client, app_engine, estate, storage, monkeypatch):
    """A19 at the route — svg/html refused, >5 files refused, a .png sent as text/html is stored
    as image/png, nothing stored for a non-visible conversation, photo-only posts allowed."""
    e = estate
    rooms = _rooms(app_engine, e)
    owner = app_client(e.account_id, e.owner)

    def stored() -> int:
        return sum(1 for p in storage.root.rglob("*") if p.is_file()) if storage.root.exists() else 0

    assert _upload(owner, rooms["general"], [("x.svg", io.BytesIO(b"<svg/>"), "image/svg+xml")],
                   "x").status_code == 400
    assert _upload(owner, rooms["general"], [("x.html", io.BytesIO(b"<p>"), "text/html")],
                   "x").status_code == 400
    six = [(f"{i}.png", io.BytesIO(PNG), "image/png") for i in range(6)]
    assert _upload(owner, rooms["general"], six, "x").status_code == 400
    assert stored() == 0

    r = _upload(owner, rooms["general"], [("disguised.png", io.BytesIO(PNG), "text/html")])
    assert r.status_code == 200    # photo-only (empty body) is allowed
    (att_id,) = set(_attachment_ids(r))
    assert owner.get(f"/chat/file/{att_id}").headers["content-type"] == "image/png"
    assert stored() == 1

    blue = app_client(e.account_id, e.staff_blue)
    assert _upload(blue, rooms["belle"], [("p.png", io.BytesIO(PNG), "image/png")],
                   "intrude").status_code == 404
    assert stored() == 1


def test_failed_post_leaves_no_orphan_object(app_client, app_engine, estate, storage,
                                             monkeypatch):
    """§5.4 step 4 — if the post fails after the files were stored, the objects are deleted."""
    from mihomes.services.chat import posts as post_svc

    e = estate
    rooms = _rooms(app_engine, e)
    owner = app_client(e.account_id, e.owner)
    original = post_svc.create_post

    def boom(*a, **k):
        raise post_svc.ChatInvalid("nope")

    monkeypatch.setattr(post_svc, "create_post", boom)
    assert _upload(owner, rooms["general"], [("p.png", io.BytesIO(PNG), "image/png")],
                   "x").status_code == 400
    assert not any(p.is_file() for p in storage.root.rglob("*")) if storage.root.exists() else True
    monkeypatch.setattr(post_svc, "create_post", original)


def test_photo_passed_to_ai_and_temp_cleaned(app_client, app_engine, estate, monkeypatch, _pg_engine):
    """A21 — a chat photo reaches the provider as an image Attachment; the temp dir is gone."""
    from mihomes import db
    from mihomes.services.chat import ai_bridge
    from mihomes.services.gateways import review_common

    e = estate
    rooms = _rooms(app_engine, e)
    owner = app_client(e.account_id, e.owner)
    r = _upload(owner, rooms["belle"], [("leak.png", io.BytesIO(PNG), "image/png")], "leak here")
    post_id = re.search(r'id="post-([0-9a-f-]{36})"', r.text).group(1)

    seen = {}

    def fake_analyze(session, messages, property_name=None, property_slug=None):
        attachments, _lines = review_common._load_image_attachments(messages)
        seen["images"] = [a.is_image for a in attachments]
        seen["paths"] = [m["mediaPath"] for m in messages if m.get("mediaPath")]
        return {"items": []}

    monkeypatch.setattr(review_common, "analyze_messages", fake_analyze)
    monkeypatch.setattr(ai_bridge.BUS, "publish", lambda *a: None)
    prev = db._engine, db._SessionLocal
    db._engine, db._SessionLocal = app_engine, None
    try:
        _REAL_DISPATCH(e.account_id, rooms["belle"], uuid.UUID(post_id))
    finally:
        db._engine, db._SessionLocal = prev
    assert seen["images"] == [True]
    from pathlib import Path

    assert seen["paths"] and not any(Path(p).exists() for p in seen["paths"])
