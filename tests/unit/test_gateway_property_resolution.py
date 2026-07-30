"""L2: gateway inventory routing must not hardcode `belle-estate`.

When the property is unknown, resolve to the sole property if exactly one
exists, else None — never a made-up slug that happens to match one estate.
"""

import pytest

from mihomes.models.property import Property
from mihomes.services.gateways.review_common import resolve_default_property
from mihomes.services.property import create_property


class TestResolveDefaultProperty:
    def test_sole_property_returned(self, session):
        p = create_property(session, "Only Estate")
        assert resolve_default_property(session) == p.slug

    def test_none_when_no_properties(self, session):
        assert resolve_default_property(session) is None

    def test_none_when_multiple_properties(self, session):
        create_property(session, "Estate A")
        create_property(session, "Estate B")
        assert resolve_default_property(session) is None
