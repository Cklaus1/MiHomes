"""Book service — CRUD for book tracking across the estate."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.book import Book, BookCondition
from mihomes.services.slug import generate_slug, resolve_identifier, EntityNotFoundError
from mihomes.services.update_helpers import safe_update


def create_book(
    session: Session,
    title: str,
    property_id_or_slug: str,
    space_id_or_slug: str | None = None,
    author: str | None = None,
    genre: str | None = None,
    isbn: str | None = None,
    condition: BookCondition = BookCondition.GOOD,
    notes: str | None = None,
) -> Book:
    from mihomes.models.property import Property
    from mihomes.models.space import Space

    prop = resolve_identifier(session, Property, property_id_or_slug)
    space = resolve_identifier(session, Space, space_id_or_slug) if space_id_or_slug else None

    book = Book(
        title=title,
        slug=generate_slug(title),
        property_id=prop.id,
        space_id=space.id if space else None,
        author=author,
        genre=genre,
        isbn=isbn,
        condition=condition,
        notes=notes,
    )
    session.add(book)
    session.flush()
    return book


def list_books(
    session: Session,
    property_id_or_slug: str | None = None,
    space_id_or_slug: str | None = None,
    active_only: bool = True,
) -> list[Book]:
    from mihomes.models.property import Property
    from mihomes.models.space import Space

    q = select(Book)
    if active_only:
        q = q.where(Book.active == True)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        q = q.where(Book.property_id == prop.id)
    if space_id_or_slug:
        space = resolve_identifier(session, Space, space_id_or_slug)
        q = q.where(Book.space_id == space.id)
    q = q.order_by(Book.title)
    return list(session.execute(q).scalars())


def get_book(session: Session, id_or_slug: str) -> Book:
    return resolve_identifier(session, Book, id_or_slug)


def update_book(session: Session, id_or_slug: str, **kwargs) -> Book:
    book = get_book(session, id_or_slug)
    safe_update(book, kwargs)
    session.flush()
    return book


def delete_book(session: Session, id_or_slug: str) -> str:
    book = get_book(session, id_or_slug)
    title = book.title
    session.delete(book)
    session.flush()
    return title
