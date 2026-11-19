"""Library counts by room — ported from telegram-bot (`e4c2e75`, `ea02c64`, `687f980`).

The bot is scoped to one property, so "how many books in the Blue Room?" must count that
property's rooms only, and books with no room must not render as "space None".
"""

from __future__ import annotations

from mihomes.services import book as book_svc
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services.ai.context import _fetch_books
from mihomes.services.ai.tools import execute_tool


def _seed_two_libraries(client) -> tuple[str, str]:
    with client._SessionLocal() as s:
        manor = prop_svc.list_properties(s)[0]
        lodge = prop_svc.create_property(s, "Lake Lodge")
        study = space_svc.create_space(s, "Study", manor.slug)
        den = space_svc.create_space(s, "Den", lodge.slug)
        book_svc.create_book(s, "Middlemarch", manor.slug, study.slug, genre="Fiction")
        book_svc.create_book(s, "Walden", manor.slug, genre="Essays")  # no room
        book_svc.create_book(s, "Dune", lodge.slug, den.slug, genre="Fiction")
        s.commit()
        return manor.slug, lodge.slug


def test_context_labels_unassigned_books_and_scopes_rooms(client):
    manor, _ = _seed_two_libraries(client)
    with client._SessionLocal() as s:
        text = _fetch_books(s, manor)
    assert "Library / Books (2 total)" in text
    assert "Study: 1 books" in text
    assert "No room assigned: 1 books" in text
    assert "space None" not in text
    assert "Den" not in text  # the other property's room


def test_count_only_tool_scopes_room_breakdown_to_property(client):
    manor, _ = _seed_two_libraries(client)
    with client._SessionLocal() as s:
        out = execute_tool(s, "query_library", {"property_slug": manor, "count_only": True})
    assert "Study: 1 books" in out
    assert "Den" not in out
