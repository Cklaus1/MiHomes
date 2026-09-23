"""W.5 · H30 — CSRF-ish request-origin + Host guard middleware.

MiHomes web is a local-first single-user app bound to localhost. A malicious
page in the user's browser could POST to http://localhost:8000/... (CSRF) or a
DNS-rebinding attack could send a foreign Host header. The middleware rejects:
  - state-changing methods (POST/PUT/PATCH/DELETE) whose Origin or
    Sec-Fetch-Site indicates a cross-site request, and
  - any request whose Host is not a localhost address.
Safe methods (GET/HEAD) and same-origin/none requests pass through.
"""

import pytest

from mihomes.models.staff import StaffRole
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc


@pytest.fixture
def client(web_client_factory):
    """Overrides the package `client` only to get `raise_server_exceptions=False`,
    so the guard's 403/400 responses are observable rather than re-raised."""

    def seed(s):
        prop = prop_svc.create_property(s, "Test Manor")
        staff_svc.create_staff(
            s, "Marcia Staff", role=StaffRole.HOUSEKEEPER, property_id_or_slug=prop.slug
        )

    return web_client_factory(seed, raise_server_exceptions=False)


def test_get_allowed_without_origin(client):
    assert client.get("/").status_code == 200


def test_cross_site_post_rejected_by_origin(client):
    resp = client.post(
        "/budget/transactions",
        data={"property_id": "1", "description": "x", "amount": "5", "category": "general"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


def test_cross_site_post_rejected_by_sec_fetch_site(client):
    resp = client.post(
        "/budget/transactions",
        data={"property_id": "1", "description": "x", "amount": "5", "category": "general"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403


def test_same_origin_post_allowed(client):
    resp = client.post(
        "/budget/transactions",
        data={"property_id": "1", "description": "x", "amount": "5", "category": "general"},
        headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"},
    )
    assert resp.status_code != 403


def test_foreign_host_rejected(client):
    resp = client.get("/", headers={"Host": "evil.example.com"})
    assert resp.status_code == 400


TS_HOST = "evo-dev.tail6765ef.ts.net"


def test_allowed_hosts_env_admits_named_host(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_ALLOWED_HOSTS", f" {TS_HOST.upper()} , other.example ")
    assert client.get("/", headers={"Host": TS_HOST}).status_code == 200


def test_allowed_hosts_env_admits_matching_origin_on_post(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_ALLOWED_HOSTS", TS_HOST)
    resp = client.post(
        "/budget/transactions",
        data={"property_id": "1", "description": "x", "amount": "5", "category": "general"},
        headers={"Host": TS_HOST, "Origin": f"https://{TS_HOST}"},
    )
    assert resp.status_code not in (400, 403)


def test_allowed_hosts_env_admits_nothing_else(client, monkeypatch):
    monkeypatch.setenv("MIHOMES_ALLOWED_HOSTS", TS_HOST)
    assert client.get("/", headers={"Host": "evil.example.com"}).status_code == 400
    resp = client.post(
        "/budget/transactions",
        data={"property_id": "1", "description": "x", "amount": "5", "category": "general"},
        headers={"Host": TS_HOST, "Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403
