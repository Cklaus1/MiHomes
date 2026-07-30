"""W.10 · M19 — "unassigned assets" must filter on ``space_id IS NULL``.

The overview and list views used Python truthiness (``if a.space_id`` /
``if not a.space_id``) to decide whether an asset is unassigned. A space with
primary key ``0`` is falsy, so an asset genuinely assigned to space 0 was
miscounted as unassigned (and vice-versa). The fix compares against ``None``.

These tests force a space with ``id == 0`` and assert the assigned asset is
never treated as unassigned.
"""

from mihomes.models.asset import Asset, AssetType
from mihomes.models.space import Space
from mihomes.services import property as prop_svc
from mihomes.web.routes.assets import _list_ctx, _spaces_ctx


def _seed_space_zero(s):
    """Create a property + a space whose primary key is 0 + an asset in it."""
    prop = prop_svc.create_property(s, "Zero Manor")
    s.flush()
    space = Space(id=0, property_id=prop.id, name="Ground Zero", space_type="other")
    space.slug = "ground-zero"
    s.add(space)
    s.flush()
    asset = Asset(
        property_id=prop.id,
        space_id=0,
        name="Boiler",
        asset_type=AssetType.APPLIANCE,
    )
    asset.slug = "boiler"
    s.add(asset)
    s.commit()
    return prop, space


def test_spaces_ctx_does_not_miscount_space_zero_asset(client):
    with client._SessionLocal() as s:
        prop, space = _seed_space_zero(s)
        ctx = _spaces_ctx(s, prop.slug)
        assert ctx["unassigned_count"] == 0, (
            "asset in space id=0 must not be counted as unassigned"
        )
        assert ctx["space_counts"].get(0) == 1


def test_list_ctx_unassigned_excludes_space_zero_asset(client):
    with client._SessionLocal() as s:
        prop, space = _seed_space_zero(s)
        ctx = _list_ctx(s, prop.slug, "unassigned")
        slugs = {a.slug for a in ctx["assets"]}
        assert "boiler" not in slugs, (
            "asset in space id=0 must not appear in the unassigned list"
        )


def test_list_ctx_space_zero_includes_its_asset(client):
    with client._SessionLocal() as s:
        prop, space = _seed_space_zero(s)
        ctx = _list_ctx(s, prop.slug, space.slug)
        slugs = {a.slug for a in ctx["assets"]}
        assert "boiler" in slugs
