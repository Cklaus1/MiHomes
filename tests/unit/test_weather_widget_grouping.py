"""Dashboard weather grouping (2026-10-15).

Properties sharing a ZIP share a forecast card; a property with no ZIP stands alone. The widget
used to pool every ZIP-less property into one "—" bucket, so one geocodable address lent its
forecast to properties with no usable location — measured live on evo-dev, where four test
properties with junk addresses appeared under Portland's weather.
"""

from __future__ import annotations

from types import SimpleNamespace

from mihomes.web.routes.weather import _group_by_zip


def _prop(name, address):
    return SimpleNamespace(name=name, address=address)


def test_same_zip_shares_a_group():
    a = _prop("A", "1 Main St, Atlanta, GA 30327")
    b = _prop("B", "2 Main St, Atlanta, GA 30327")
    groups = _group_by_zip([a, b])
    assert len(groups) == 1
    assert groups[0]["zip"] == "30327"
    assert groups[0]["props"] == [a, b]


def test_properties_without_a_zip_are_never_pooled():
    located = _prop("Oak Street", "412 Oak Street, Portland, OR")
    junk = [_prop("Test 3", "1355 tester house"), _prop("Test 4", "grafsfnsncndad")]
    no_address = _prop("Bare", None)
    groups = _group_by_zip([located, *junk, no_address])
    assert len(groups) == 4, "each ZIP-less property must be its own group"
    assert all(len(g["props"]) == 1 for g in groups)
    assert all(g["zip"] == "—" for g in groups)


def test_order_is_preserved():
    props = [_prop("X", "a 11111"), _prop("Y", None), _prop("Z", "b 22222")]
    assert [g["props"][0].name for g in _group_by_zip(props)] == ["X", "Y", "Z"]
