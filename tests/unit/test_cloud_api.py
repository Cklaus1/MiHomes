"""Tests for the Cloud API service (G4)."""

from __future__ import annotations

import pytest
from fastapi import status as http_status
from fastapi.exceptions import HTTPException

from mihomes.services.cloud import CloudAPIService, get_current_tenant

# -- tenant CRUD -----------------------------------------------------------

def test_create_tenant():
    svc = CloudAPIService()
    t = svc.create_tenant("acme", plan="pro")
    assert t.name == "acme"
    assert t.plan == "pro"
    assert t.status == "active"
    assert len(svc._tenants) == 1


def test_get_tenant():
    svc = CloudAPIService()
    t = svc.create_tenant("acme")
    found = svc.get_tenant(t.id)
    assert found is t


def test_get_tenant_404():
    svc = CloudAPIService()
    with pytest.raises(HTTPException) as exc:
        svc.get_tenant("nonexistent")
    assert exc.value.status_code == http_status.HTTP_404_NOT_FOUND


def test_list_tenants():
    svc = CloudAPIService()
    svc.create_tenant("a")
    svc.create_tenant("b")
    all_tenants = svc.list_tenants()
    assert len(all_tenants) == 2

    filtered = svc.list_tenants(status="active")
    assert len(filtered) == 2


def test_list_tenants_filter_by_status():
    svc = CloudAPIService()
    t1 = svc.create_tenant("a")
    svc.suspend_tenant(t1.id)
    active = svc.list_tenants(status="active")
    assert len(active) == 0


def test_update_tenant():
    svc = CloudAPIService()
    t = svc.create_tenant("acme")
    updated = svc.update_tenant(t.id, name="Acme Corp")
    assert updated.name == "Acme Corp"


def test_suspend_tenant():
    svc = CloudAPIService()
    t = svc.create_tenant("acme")
    suspended = svc.suspend_tenant(t.id)
    assert suspended.status == "suspended"


def test_delete_tenant():
    svc = CloudAPIService()
    t = svc.create_tenant("acme")
    svc.delete_tenant(t.id)
    assert len(svc._tenants) == 0
    with pytest.raises(HTTPException) as exc:
        svc.get_tenant(t.id)
    assert exc.value.status_code == http_status.HTTP_404_NOT_FOUND


def test_delete_nonexistent_tenant():
    svc = CloudAPIService()
    with pytest.raises(HTTPException) as exc:
        svc.delete_tenant("nonexistent")
    assert exc.value.status_code == http_status.HTTP_404_NOT_FOUND


# -- config sync -----------------------------------------------------------

def test_sync_tenant_config():
    svc = CloudAPIService()
    t = svc.create_tenant("acme")
    synced = svc.sync_tenant_config(t.id, {"key": "value"})
    assert synced.config["key"] == "value"


# -- health ----------------------------------------------------------------

def test_health():
    svc = CloudAPIService()
    svc.create_tenant("a")
    svc.create_tenant("b")  # noqa: F841
    h = svc.health()
    assert h["status"] == "healthy"
    assert h["tenant_count"] == 2
    assert h["active_tenants"] == 2


# -- router ----------------------------------------------------------------

def test_router_has_routes():
    svc = CloudAPIService()
    routes = [r.path for r in svc.router.routes]
    assert "/api/cloud/health" in routes
    assert "/api/cloud/tenants" in routes


# -- tenant isolation dependency -------------------------------------------

@pytest.mark.asyncio
async def test_get_current_tenant():
    svc = CloudAPIService()
    t = svc.create_tenant("acme")
    resolved = await get_current_tenant(t.id, svc)
    assert resolved is t


@pytest.mark.asyncio
async def test_get_current_tenant_404():
    svc = CloudAPIService()
    with pytest.raises(HTTPException) as exc:
        await get_current_tenant("nonexistent", svc)
    assert exc.value.status_code == http_status.HTTP_404_NOT_FOUND