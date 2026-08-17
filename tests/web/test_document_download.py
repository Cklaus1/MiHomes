"""G11 · A14 — one tenant must not be able to download another's document.

**This closes a real hole rather than guarding a hypothetical one.** Until G11, `web/app.py`
mounted the uploads directory as static files with no authentication and no tenant check:

    app.mount("/uploads", SecureStaticFiles(directory=UPLOADS_DIR))

Any request reaching the app could fetch any tenant's document. The only obstacle was filename
guessability, and generated reports were named `{title-slug}-{8 hex}` — derived from text the user
can see. `test_the_unauthenticated_uploads_mount_is_gone` keeps the mount from coming back.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.storage import build_key, get_storage, reset_storage


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """A filesystem provider rooted in a temp dir, so no test writes to the real media directory.

    **Uses `override_root`, not `monkeypatch.setenv("MIHOMES_DIR")`.** `config.MEDIA_DIR` is
    computed from that variable **at config import time**, so setting it in a fixture changes
    nothing — and the first version of this fixture therefore wrote 8 test files into the author's
    real `~/.mihomes/media/objects`. `lessons.md` already recorded the same trap for `DB_URL`;
    passing the root explicitly removes the possibility rather than relying on getting the
    monkeypatch right.
    """
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
    reset_storage()
    provider = get_storage(refresh=True, override_root=tmp_path / "objects")
    yield provider
    reset_storage()


def test_owner_can_download_its_own_document(client, account_a, storage):
    """The positive half. Without it, a route that refused everything would look secure."""
    key = build_key(account_a, "documents", "deed.pdf")
    storage.put(key, b"%PDF-1.4 mine", content_type="application/pdf")

    resp = client.get(f"/documents/file/{key}")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-1.4 mine"
    assert resp.headers["x-content-type-options"] == "nosniff"
    # A tenant document must not land in a shared cache.
    assert "no-store" in resp.headers.get("cache-control", "")


def test_cannot_download_another_accounts_document(client, account_a, storage):
    """**The whole point of A14.** The client is bound to account A; the key belongs to B.

    The bytes exist on disk and the key is correct — the only thing standing between the request
    and the file is the authorisation check, which is exactly what needs proving.
    """
    other_account = uuid.uuid4()
    foreign_key = build_key(other_account, "documents", "their-deed.pdf")
    storage.put(foreign_key, b"%PDF-1.4 not yours", content_type="application/pdf")

    resp = client.get(f"/documents/file/{foreign_key}")

    assert resp.status_code == 404, (
        f"account A fetched another tenant's document (status {resp.status_code}) — this is the "
        "cross-tenant read the static uploads mount allowed"
    )
    assert b"not yours" not in resp.content


def test_refusal_is_404_not_403(client, account_a, storage):
    """A 403 confirms the object exists and belongs to someone else.

    That is itself a cross-tenant disclosure: it turns "can I read this?" into "does this exist?",
    which is enough to enumerate another tenant's documents given a key. Both the foreign-key case
    and the never-existed case must be indistinguishable.
    """
    foreign_key = build_key(uuid.uuid4(), "documents", "real.pdf")
    storage.put(foreign_key, b"exists")
    never_existed = build_key(uuid.uuid4(), "documents", "ghost.pdf")

    foreign = client.get(f"/documents/file/{foreign_key}")
    missing = client.get(f"/documents/file/{never_existed}")

    assert foreign.status_code == missing.status_code == 404, (
        "an existing-but-foreign key and a non-existent key must be indistinguishable"
    )


@pytest.mark.parametrize(
    "key",
    [
        "../../../etc/passwd",
        "not-a-uuid/documents/x.pdf",
        "documents/x.pdf",
        "",
    ],
)
def test_malformed_keys_are_refused(client, account_a, storage, key):
    """A key with no valid account prefix cannot be authorised, so it is refused.

    Including the traversal attempt: the account check rejects it before the storage layer's own
    path guard is reached, so there are two independent defences and this asserts the outer one.
    """
    resp = client.get(f"/documents/file/{key}")
    assert resp.status_code == 404


def test_the_unauthenticated_uploads_mount_is_gone():
    """Regression guard on the hole itself.

    A static mount has nowhere to put an authorisation check, so re-adding one would silently
    reopen cross-tenant document access. Asserted against the app's own route table rather than by
    reading the source, so a differently-spelled mount cannot slip past.
    """
    from mihomes.web.app import create_app

    app = create_app()
    mounted = [
        r.path for r in app.routes if type(r).__name__ == "Mount"
    ]
    assert "/uploads" not in mounted, (
        "the unauthenticated /uploads static mount is back — it serves every tenant's documents "
        "to any request that can reach the app"
    )
    # /static is fine: it holds the app's own CSS and JS, which are not tenant data.
    assert "/static" in mounted, "the app's own static assets should still be mounted"
