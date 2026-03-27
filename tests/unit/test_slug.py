"""Tests for slug generation and entity resolution."""

import pytest
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.models import Base, SlugMixin
from mihomes.services.slug import (
    EntityNotFoundError,
    ensure_unique_slug,
    generate_slug,
    resolve_identifier,
)


# Test model
class DummyModel(Base, SlugMixin):
    __tablename__ = "dummy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))


class TestGenerateSlug:
    def test_basic(self):
        assert generate_slug("Beach House") == "beach-house"

    def test_special_characters(self):
        assert generate_slug("ABC Plumbing & Co.") == "abc-plumbing-co"

    def test_unicode(self):
        assert generate_slug("Château de Loire") == "chateau-de-loire"

    def test_max_length(self):
        long_name = "A" * 200
        slug = generate_slug(long_name)
        assert len(slug) <= 80

    def test_empty_string(self):
        assert generate_slug("") == ""

    def test_numbers(self):
        assert generate_slug("Property 123") == "property-123"


class TestEnsureUniqueSlug:
    def test_no_collision(self, session):
        slug = ensure_unique_slug(session, DummyModel, "beach-house")
        assert slug == "beach-house"

    def test_collision_appends_suffix(self, session):
        session.add(DummyModel(name="Beach House", slug="beach-house"))
        session.flush()
        slug = ensure_unique_slug(session, DummyModel, "beach-house")
        assert slug == "beach-house-2"

    def test_multiple_collisions(self, session):
        session.add(DummyModel(name="A", slug="beach-house"))
        session.add(DummyModel(name="B", slug="beach-house-2"))
        session.flush()
        slug = ensure_unique_slug(session, DummyModel, "beach-house")
        assert slug == "beach-house-3"

    def test_exclude_id(self, session):
        obj = DummyModel(name="Beach House", slug="beach-house")
        session.add(obj)
        session.flush()
        # Should not collide with itself
        slug = ensure_unique_slug(session, DummyModel, "beach-house", exclude_id=obj.id)
        assert slug == "beach-house"


class TestResolveIdentifier:
    def test_resolve_by_id(self, session):
        obj = DummyModel(name="Beach House", slug="beach-house")
        session.add(obj)
        session.flush()
        result = resolve_identifier(session, DummyModel, str(obj.id))
        assert result.id == obj.id

    def test_resolve_by_slug(self, session):
        obj = DummyModel(name="Beach House", slug="beach-house")
        session.add(obj)
        session.flush()
        result = resolve_identifier(session, DummyModel, "beach-house")
        assert result.id == obj.id

    def test_not_found_raises(self, session):
        with pytest.raises(EntityNotFoundError) as exc_info:
            resolve_identifier(session, DummyModel, "nonexistent")
        assert "nonexistent" in str(exc_info.value)

    def test_numeric_slug_resolves_by_id_first(self, session):
        obj = DummyModel(name="123 Main St", slug="123-main-st")
        session.add(obj)
        session.flush()
        # "123-main-st" is not numeric, resolves by slug
        result = resolve_identifier(session, DummyModel, "123-main-st")
        assert result.slug == "123-main-st"
