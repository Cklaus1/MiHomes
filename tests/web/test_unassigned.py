"""W.10 · M19 — "unassigned assets" must filter on ``space_id IS NULL``.

The overview and list views used Python truthiness (``if a.space_id`` /
``if not a.space_id``) to decide whether an asset is unassigned. Under integer
primary keys a space with ``id == 0`` is falsy, so an asset genuinely assigned to
space 0 was miscounted as unassigned (and vice-versa). The fix compares against
``None``, and these tests forced ``id=0`` to provoke the bug.

**SPEC-002 G6.1 (UUIDv7 primary keys) removed that hazard structurally** — a
``uuid.UUID`` is never falsy, so the only falsy ``space_id`` is now ``None``,
which *is* "unassigned". Forcing ``id=0`` is no longer even expressible: it binds
an int to a UUID column and Postgres rejects it.

The behaviour these tests describe still matters, so they keep their assertions
against a normally-keyed space: an asset **with** a space must never be counted
or listed as unassigned, and must appear under its own space. What they no longer
guard is falsy-PK handling — that bug class cannot recur, so do not reintroduce a
sentinel id to "restore coverage".
"""

from mihomes.models.asset import Asset, AssetType
from mihomes.models.space import Space
from mihomes.services import property as prop_svc
from mihomes.web.routes.assets import _list_ctx, _spaces_ctx


def _seed_assigned_asset(s):
    """Create a property + a space + an asset assigned to that space."""
    prop = prop_svc.create_property(s, "Zero Manor")
    s.flush()
    space = Space(property_id=prop.id, name="Ground Zero", space_type="other")
    space.slug = "ground-zero"
    s.add(space)
    s.flush()
    asset = Asset(
        property_id=prop.id,
        space_id=space.id,
        name="Boiler",
        asset_type=AssetType.APPLIANCE,
    )
    asset.slug = "boiler"
    s.add(asset)
    s.commit()
    return prop, space


def test_spaces_ctx_does_not_miscount_assigned_asset(client):
    with client._SessionLocal() as s:
        prop, space = _seed_assigned_asset(s)
        ctx = _spaces_ctx(s, prop.slug)
        assert ctx["unassigned_count"] == 0, (
            "an asset that has a space must not be counted as unassigned"
        )
        assert ctx["space_counts"].get(space.id) == 1


def test_list_ctx_unassigned_excludes_assigned_asset(client):
    with client._SessionLocal() as s:
        prop, space = _seed_assigned_asset(s)
        ctx = _list_ctx(s, prop.slug, "unassigned")
        slugs = {a.slug for a in ctx["assets"]}
        assert "boiler" not in slugs, (
            "an asset that has a space must not appear in the unassigned list"
        )


def test_list_ctx_space_includes_its_asset(client):
    with client._SessionLocal() as s:
        prop, space = _seed_assigned_asset(s)
        ctx = _list_ctx(s, prop.slug, space.slug)
        slugs = {a.slug for a in ctx["assets"]}
        assert "boiler" in slugs
