"""Cloud API service for G4: multi-tenant SaaS API with tenant lifecycle, config sync, and health."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Tenant:
    """A cloud tenant managed by the Cloud API."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    status: str = "active"  # active | suspended | deleted
    config: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    plan: str = "default"  # default | pro | enterprise


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CloudAPIService:
    """Manages tenant lifecycle, config sync, and health for the Cloud API."""

    def __init__(self) -> None:
        self._tenants: Dict[str, Tenant] = {}
        self._router = self._create_router()

    # -- tenant CRUD --------------------------------------------------------

    def create_tenant(self, name: str, plan: str = "default", config: Optional[Dict[str, Any]] = None) -> Tenant:
        tenant = Tenant(name=name, plan=plan, config=config or {})
        self._tenants[tenant.id] = tenant
        logger.info("tenant created %s (%s)", tenant.id, name)
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant:
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        return tenant

    def list_tenants(self, status: Optional[str] = None) -> list[Tenant]:
        if status:
            return [t for t in self._tenants.values() if t.status == status]
        return list(self._tenants.values())

    def update_tenant(self, tenant_id: str, **kwargs) -> Tenant:
        tenant = self.get_tenant(tenant_id)
        for key, value in kwargs.items():
            if hasattr(tenant, key):
                setattr(tenant, key, value)
        tenant.updated_at = time.time()
        self._tenants[tenant_id] = tenant
        return tenant

    def suspend_tenant(self, tenant_id: str) -> Tenant:
        tenant = self.get_tenant(tenant_id)
        tenant.status = "suspended"
        tenant.updated_at = time.time()
        self._tenants[tenant_id] = tenant
        logger.info("tenant suspended %s", tenant_id)
        return tenant

    def delete_tenant(self, tenant_id: str) -> None:
        if tenant_id not in self._tenants:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        del self._tenants[tenant_id]
        logger.info("tenant deleted %s", tenant_id)

    # -- config sync --------------------------------------------------------

    def sync_tenant_config(self, tenant_id: str, config: Dict[str, Any]) -> Tenant:
        tenant = self.get_tenant(tenant_id)
        tenant.config.update(config)
        tenant.updated_at = time.time()
        self._tenants[tenant_id] = tenant
        logger.info("tenant config synced %s", tenant_id)
        return tenant

    # -- health -------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "tenant_count": len(self._tenants),
            "active_tenants": sum(1 for t in self._tenants.values() if t.status == "active"),
        }

    # -- router -------------------------------------------------------------

    def _create_router(self) -> APIRouter:
        router = APIRouter(prefix="/api/cloud", tags=["cloud"])

        @router.get("/health")
        async def cloud_health():
            return self.health()

        @router.post("/tenants", response_model=Tenant, status_code=201)
        async def create_tenant_endpoint(
            body: Dict[str, Any],
        ):
            return self.create_tenant(
                name=body["name"],
                plan=body.get("plan", "default"),
                config=body.get("config"),
            )

        @router.get("/tenants")
        async def list_tenants_endpoint(status: Optional[str] = None):
            return self.list_tenants(status=status)

        @router.get("/tenants/{tenant_id}")
        async def get_tenant_endpoint(tenant_id: str):
            return self.get_tenant(tenant_id)

        @router.patch("/tenants/{tenant_id}")
        async def update_tenant_endpoint(tenant_id: str, body: Dict[str, Any]):
            return self.update_tenant(tenant_id, **body)

        @router.post("/tenants/{tenant_id}/suspend")
        async def suspend_tenant_endpoint(tenant_id: str):
            return self.suspend_tenant(tenant_id)

        @router.delete("/tenants/{tenant_id}")
        async def delete_tenant_endpoint(tenant_id: str):
            self.delete_tenant(tenant_id)
            return {"detail": "Tenant deleted"}

        @router.post("/tenants/{tenant_id}/config")
        async def sync_config_endpoint(tenant_id: str, body: Dict[str, Any]):
            return self.sync_tenant_config(tenant_id, body)

        return router

    @property
    def router(self) -> APIRouter:
        return self._router


# ---------------------------------------------------------------------------
# Tenant isolation dependency
# ---------------------------------------------------------------------------


async def get_current_tenant(tenant_id: str, cloud: CloudAPIService) -> Tenant:
    """FastAPI dependency that resolves the current tenant from a header or query param."""
    return cloud.get_tenant(tenant_id)