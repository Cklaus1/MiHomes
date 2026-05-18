"""Books routes — scoped within property/space views."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.book import BookCondition
from mihomes.services import book as book_svc
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _return_space_view(request: Request, db: Session, from_property: str, from_space: str):
    """Re-render the full space view (assets + books) after a mutation."""
    from mihomes.web.routes.assets import _list_ctx
    ctx = _list_ctx(db, from_property, from_space)
    ctx["active_tab"] = "books"
    return templates.TemplateResponse(request, "assets.html", ctx)


@router.post("/", response_class=HTMLResponse)
def create_book(
    request: Request,
    title: str = Form(...),
    author: str = Form(""),
    genre: str = Form(""),
    isbn: str = Form(""),
    condition: str = Form("good"),
    notes: str = Form(""),
    property_slug: str = Form(...),
    space_slug: str = Form(""),
    from_property: str = Form(""),
    from_space: str = Form(""),
    db: Session = Depends(get_db),
):
    book_svc.create_book(
        db,
        title=title,
        property_id_or_slug=property_slug,
        space_id_or_slug=space_slug or None,
        author=author or None,
        genre=genre or None,
        isbn=isbn or None,
        condition=BookCondition(condition),
        notes=notes or None,
    )
    return _return_space_view(request, db, from_property or property_slug, from_space or space_slug)


@router.post("/{slug}/edit", response_class=HTMLResponse)
def edit_book(
    request: Request,
    slug: str,
    title: str = Form(...),
    author: str = Form(""),
    genre: str = Form(""),
    isbn: str = Form(""),
    condition: str = Form(""),
    notes: str = Form(""),
    space_slug: str = Form(""),
    from_property: str = Form(""),
    from_space: str = Form(""),
    db: Session = Depends(get_db),
):
    kwargs = dict(title=title, notes=notes or None)
    if author: kwargs["author"] = author
    if genre: kwargs["genre"] = genre
    if isbn: kwargs["isbn"] = isbn
    if condition: kwargs["condition"] = BookCondition(condition)
    if space_slug:
        space = space_svc.get_space(db, space_slug)
        kwargs["space_id"] = space.id
    book_svc.update_book(db, slug, **kwargs)
    return _return_space_view(request, db, from_property, from_space)


@router.post("/{slug}/delete", response_class=HTMLResponse)
def delete_book(
    request: Request,
    slug: str,
    from_property: str = Form(""),
    from_space: str = Form(""),
    db: Session = Depends(get_db),
):
    book_svc.delete_book(db, slug)
    return _return_space_view(request, db, from_property, from_space)
