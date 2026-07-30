"""M10 — text-matching helpers and their call sites must not silently match the
wrong row when a value contains LIKE wildcards, and exact-match lookups must be
exact (case-insensitive), not substring.
"""

from mihomes.services.query_helpers import escape_like, exact_ci


class TestEscapeLike:
    def test_escapes_percent(self):
        assert escape_like("50%") == "50\\%"

    def test_escapes_underscore(self):
        assert escape_like("a_b") == "a\\_b"

    def test_escapes_backslash_first(self):
        # backslash must be doubled before wildcard escaping is applied
        assert escape_like("a\\b") == "a\\\\b"

    def test_plain_text_unchanged(self):
        assert escape_like("Acme Plumbing") == "Acme Plumbing"


class TestConsumableExactMatch:
    """get_or_create_consumable must not treat a wildcard-bearing name as a
    pattern, and must match names exactly (case-insensitively) rather than as a
    substring."""

    def _prop(self, session):
        from mihomes.models.property import Property, PropertyType
        p = Property(name="Fuzzy House", slug="fuzzy-house", property_type=PropertyType.PRIMARY)
        session.add(p)
        session.flush()
        return p

    def test_wildcard_name_does_not_match_other_row(self, session):
        from mihomes.services.consumable import get_or_create_consumable
        prop = self._prop(session)
        real = get_or_create_consumable(session, "Paper Towels", prop.slug)
        # A name of pure wildcards must create a NEW row, not match "Paper Towels".
        wild = get_or_create_consumable(session, "%", prop.slug)
        assert wild.id != real.id

    def test_case_insensitive_exact_match_reuses_row(self, session):
        from mihomes.services.consumable import get_or_create_consumable
        prop = self._prop(session)
        a = get_or_create_consumable(session, "Dish Soap", prop.slug)
        b = get_or_create_consumable(session, "dish soap", prop.slug)
        assert a.id == b.id

    def test_substring_name_creates_distinct_row(self, session):
        from mihomes.services.consumable import get_or_create_consumable
        prop = self._prop(session)
        full = get_or_create_consumable(session, "Trash Bags Large", prop.slug)
        part = get_or_create_consumable(session, "Trash", prop.slug)
        # "Trash" must not be swallowed into "Trash Bags Large".
        assert part.id != full.id


class TestStaffExactMatchHelper:
    def test_exact_ci_matches_only_exact(self, session):
        from mihomes.models.staff import Staff, StaffRole
        session.add(Staff(name="Robert Smith", slug="robert-smith", role=StaffRole.HOUSEKEEPER))
        session.add(Staff(name="Rob", slug="rob", role=StaffRole.HOUSEKEEPER))
        session.flush()
        got = session.query(Staff).filter(exact_ci(Staff.name, "rob")).all()
        assert len(got) == 1
        assert got[0].name == "Rob"
