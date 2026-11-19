"""Tenant config service for G5: per-tenant feature flags, rate limits, and custom domains."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class FeatureFlag(str, Enum):
    """Feature flags that can be toggled per tenant."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    ROLLOUT = "rollout"  # percentage rollout


@dataclass
class RateLimit:
    """Rate limit configuration for a tenant."""

    requests_per_second: float = 10.0
    burst_size: int = 20
    daily_quota: int = 100000


@dataclass
class CustomDomain:
    """Custom domain configuration for a tenant."""

    domain: str
    ssl_enabled: bool = True
    redirect: str | None = None  # optional redirect to primary domain


@dataclass
class TenantConfig:
    """Complete configuration for a single tenant."""

    tenant_id: str
    feature_flags: dict[str, FeatureFlag] = field(default_factory=dict)
    rate_limit: RateLimit = field(default_factory=RateLimit)
    custom_domains: list[CustomDomain] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Mark the config as updated."""
        self.updated_at = time.time()


class TenantConfigService:
    """In-memory service for per-tenant configuration management.

    Provides CRUD for tenant configs, feature flag evaluation,
    rate limit lookup, and custom domain resolution.
    """

    def __init__(self) -> None:
        self._configs: dict[str, TenantConfig] = {}

    # -- CRUD --

    def create(self, tenant_id: str, **overrides: Any) -> TenantConfig:
        """Create a new tenant config with optional overrides."""
        if tenant_id in self._configs:
            raise ValueError(f"Tenant config already exists: {tenant_id}")

        config = TenantConfig(tenant_id=tenant_id)

        # Apply overrides
        if "feature_flags" in overrides:
            config.feature_flags = overrides["feature_flags"]
        if "rate_limit" in overrides:
            config.rate_limit = overrides["rate_limit"]
        if "custom_domains" in overrides:
            config.custom_domains = overrides["custom_domains"]
        if "metadata" in overrides:
            config.metadata = overrides["metadata"]

        self._configs[tenant_id] = config
        logger.info("Created tenant config for %s", tenant_id)
        return config

    def get(self, tenant_id: str) -> TenantConfig | None:
        """Get the config for a tenant, or None if not found."""
        return self._configs.get(tenant_id)

    def update(self, tenant_id: str, **overrides: Any) -> TenantConfig:
        """Update an existing tenant config."""
        config = self._configs.get(tenant_id)
        if config is None:
            raise ValueError(f"Tenant config not found: {tenant_id}")

        if "feature_flags" in overrides:
            config.feature_flags = overrides["feature_flags"]
        if "rate_limit" in overrides:
            config.rate_limit = overrides["rate_limit"]
        if "custom_domains" in overrides:
            config.custom_domains = overrides["custom_domains"]
        if "metadata" in overrides:
            config.metadata = overrides["metadata"]

        config.touch()
        logger.info("Updated tenant config for %s", tenant_id)
        return config

    def delete(self, tenant_id: str) -> bool:
        """Delete a tenant config. Returns True if deleted, False if not found."""
        if tenant_id not in self._configs:
            return False
        del self._configs[tenant_id]
        logger.info("Deleted tenant config for %s", tenant_id)
        return True

    def list_all(self) -> list[TenantConfig]:
        """List all tenant configs."""
        return list(self._configs.values())

    # -- Feature flags --

    def is_enabled(self, tenant_id: str, flag: str) -> bool:
        """Check if a feature flag is enabled for a tenant."""
        config = self._configs.get(tenant_id)
        if config is None:
            return False

        value = config.feature_flags.get(flag)
        if value is None:
            return False  # default: disabled

        if value == FeatureFlag.ENABLED:
            return True
        if value == FeatureFlag.DISABLED:
            return False
        if value == FeatureFlag.ROLLOUT:
            # Rollout is stored as a percentage in metadata
            rollout_pct = config.metadata.get(f"rollout_{flag}", 0)
            import hashlib

            hash_val = int(hashlib.sha256(tenant_id.encode()).hexdigest(), 16) % 100
            return hash_val < rollout_pct

        return False

    def set_flag(self, tenant_id: str, flag: str, value: FeatureFlag, **extra: Any) -> TenantConfig:
        """Set a feature flag for a tenant."""
        config = self.get(tenant_id)
        if config is None:
            raise ValueError(f"Tenant config not found: {tenant_id}")

        config.feature_flags[flag] = value
        if extra:
            config.metadata.update(extra)
        config.touch()
        return config

    # -- Rate limits --

    def get_rate_limit(self, tenant_id: str) -> RateLimit:
        """Get the rate limit for a tenant."""
        config = self._configs.get(tenant_id)
        if config is None:
            return RateLimit()  # default rate limit

        return config.rate_limit

    def update_rate_limit(self, tenant_id: str, **overrides: Any) -> RateLimit:
        """Update the rate limit for a tenant."""
        config = self.get(tenant_id)
        if config is None:
            raise ValueError(f"Tenant config not found: {tenant_id}")

        if "requests_per_second" in overrides:
            config.rate_limit.requests_per_second = overrides["requests_per_second"]
        if "burst_size" in overrides:
            config.rate_limit.burst_size = overrides["burst_size"]
        if "daily_quota" in overrides:
            config.rate_limit.daily_quota = overrides["daily_quota"]

        config.touch()
        return config.rate_limit

    # -- Custom domains --

    def add_domain(self, tenant_id: str, domain: str, ssl_enabled: bool = True,
                   redirect: str | None = None) -> CustomDomain:
        """Add a custom domain for a tenant."""
        config = self.get(tenant_id)
        if config is None:
            raise ValueError(f"Tenant config not found: {tenant_id}")

        domain_obj = CustomDomain(domain=domain, ssl_enabled=ssl_enabled, redirect=redirect)
        config.custom_domains.append(domain_obj)
        config.touch()
        return domain_obj

    def remove_domain(self, tenant_id: str, domain: str) -> bool:
        """Remove a custom domain for a tenant."""
        config = self.get(tenant_id)
        if config is None:
            return False

        before = len(config.custom_domains)
        config.custom_domains = [d for d in config.custom_domains if d.domain != domain]
        if len(config.custom_domains) < before:
            config.touch()
            return True
        return False

    def resolve_domain(self, domain: str) -> TenantConfig | None:
        """Resolve a custom domain to its tenant config."""
        for config in self._configs.values():
            for d in config.custom_domains:
                if d.domain == domain:
                    return config
        return None

    # -- Metadata --

    def get_metadata(self, tenant_id: str, key: str | None = None) -> dict[str, Any] | Any | None:
        """Get metadata for a tenant, optionally filtering to a specific key."""
        config = self._configs.get(tenant_id)
        if config is None:
            return {} if key is None else None

        if key is None:
            return config.metadata
        return config.metadata.get(key)

    def set_metadata(self, tenant_id: str, key: str, value: Any) -> TenantConfig:
        """Set a metadata value for a tenant."""
        config = self.get(tenant_id)
        if config is None:
            raise ValueError(f"Tenant config not found: {tenant_id}")

        config.metadata[key] = value
        config.touch()
        return config