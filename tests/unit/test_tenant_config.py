"""Tests for the tenant config service (G5)."""

from __future__ import annotations

import time

import pytest

from mihomes.services.tenant_config import (
    FeatureFlag,
    TenantConfig,
    TenantConfigService,
)

# -- CRUD ------------------------------------------------------------------

def test_create():
    svc = TenantConfigService()
    cfg = svc.create("t1")
    assert cfg.tenant_id == "t1"
    assert svc.get("t1") is cfg


def test_create_duplicate():
    svc = TenantConfigService()
    svc.create("t1")
    with pytest.raises(ValueError):
        svc.create("t1")


def test_get_not_found():
    svc = TenantConfigService()
    assert svc.get("t1") is None


def test_update():
    svc = TenantConfigService()
    svc.create("t1")
    cfg = svc.update("t1", metadata={"k": "v"})
    assert cfg.metadata == {"k": "v"}


def test_update_not_found():
    svc = TenantConfigService()
    with pytest.raises(ValueError):
        svc.update("t1")


def test_delete():
    svc = TenantConfigService()
    svc.create("t1")
    assert svc.delete("t1") is True
    assert svc.get("t1") is None


def test_delete_not_found():
    svc = TenantConfigService()
    assert svc.delete("t1") is False


def test_list_all():
    svc = TenantConfigService()
    svc.create("t1")
    svc.create("t2")
    assert len(svc.list_all()) == 2


# -- feature flags ---------------------------------------------------------

def test_is_enabled_default_false():
    svc = TenantConfigService()
    svc.create("t1")
    assert svc.is_enabled("t1", "dark_mode") is False


def test_is_enabled_enabled():
    svc = TenantConfigService()
    svc.create("t1")
    svc.set_flag("t1", "dark_mode", FeatureFlag.ENABLED)
    assert svc.is_enabled("t1", "dark_mode") is True


def test_is_enabled_disabled():
    svc = TenantConfigService()
    svc.create("t1")
    svc.set_flag("t1", "dark_mode", FeatureFlag.DISABLED)
    assert svc.is_enabled("t1", "dark_mode") is False


def test_is_enabled_rollout_100pct():
    svc = TenantConfigService()
    svc.create("t1")
    svc.set_flag("t1", "beta", FeatureFlag.ROLLOUT, rollout_beta=100)
    assert svc.is_enabled("t1", "beta") is True


def test_is_enabled_rollout_0pct():
    svc = TenantConfigService()
    svc.create("t1")
    svc.set_flag("t1", "beta", FeatureFlag.ROLLOUT, rollout_beta=0)
    assert svc.is_enabled("t1", "beta") is False


def test_set_flag_not_found():
    with pytest.raises(ValueError):
        TenantConfigService().set_flag("t1", "x", FeatureFlag.ENABLED)


# -- rate limits -----------------------------------------------------------

def test_get_rate_limit_default():
    svc = TenantConfigService()
    svc.create("t1")
    rl = svc.get_rate_limit("t1")
    assert rl.requests_per_second == 10.0


def test_get_rate_limit_not_found():
    svc = TenantConfigService()
    rl = svc.get_rate_limit("t1")
    assert rl.requests_per_second == 10.0


def test_update_rate_limit():
    svc = TenantConfigService()
    svc.create("t1")
    rl = svc.update_rate_limit("t1", requests_per_second=100.0)
    assert rl.requests_per_second == 100.0


def test_update_rate_limit_not_found():
    svc = TenantConfigService()
    with pytest.raises(ValueError):
        svc.update_rate_limit("t1", requests_per_second=100.0)


# -- custom domains --------------------------------------------------------

def test_add_domain():
    svc = TenantConfigService()
    svc.create("t1")
    d = svc.add_domain("t1", "acme.io")
    assert d.domain == "acme.io"
    assert len(svc.get("t1").custom_domains) == 1


def test_remove_domain():
    svc = TenantConfigService()
    svc.create("t1")
    svc.add_domain("t1", "acme.io")
    assert svc.remove_domain("t1", "acme.io") is True


def test_remove_domain_not_found():
    svc = TenantConfigService()
    svc.create("t1")
    assert svc.remove_domain("t1", "acme.io") is False


def test_resolve_domain():
    svc = TenantConfigService()
    svc.create("t1")
    svc.add_domain("t1", "acme.io")
    resolved = svc.resolve_domain("acme.io")
    assert resolved.tenant_id == "t1"


def test_resolve_domain_not_found():
    svc = TenantConfigService()
    assert svc.resolve_domain("acme.io") is None


# -- metadata --------------------------------------------------------------

def test_get_metadata_all():
    svc = TenantConfigService()
    svc.create("t1", metadata={"k": "v"})
    assert svc.get_metadata("t1") == {"k": "v"}


def test_get_metadata_key():
    svc = TenantConfigService()
    svc.create("t1", metadata={"k": "v"})
    assert svc.get_metadata("t1", "k") == "v"


def test_get_metadata_key_not_found():
    svc = TenantConfigService()
    svc.create("t1")
    assert svc.get_metadata("t1", "k") is None


def test_set_metadata():
    svc = TenantConfigService()
    svc.create("t1")
    cfg = svc.set_metadata("t1", "k", "v")
    assert cfg.metadata["k"] == "v"


def test_set_metadata_not_found():
    svc = TenantConfigService()
    with pytest.raises(ValueError):
        svc.set_metadata("t1", "k", "v")


# -- TenantConfig dataclass ------------------------------------------------

def test_tenant_config_touch_updates_timestamp():
    cfg = TenantConfig(tenant_id="t1")
    before = cfg.updated_at
    time.sleep(0.01)
    cfg.touch()
    assert cfg.updated_at > before