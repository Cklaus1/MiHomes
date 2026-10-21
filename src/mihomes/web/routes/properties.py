"""Property routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.authz.scope import current_role
from mihomes.models.property import PropertyStatus, PropertyType
from mihomes.models.task import TaskStatus
from mihomes.services import issue as issue_svc
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import staff as staff_svc
from mihomes.services import task as task_svc
from mihomes.services.health_score import compute_property_health
from mihomes.web.deps import get_db, templates, with_toast

router = APIRouter()

_SPACE_TYPES = ["bedroom", "bathroom", "kitchen", "living", "dining", "office", "entertainment", "recreation", "storage", "garage", "outdoor", "other"]


def _list_context(db: Session) -> dict:
    """The /properties page, including the upgrade popup when the plan's home cap is reached.

    `home_upgrade_prompt` is asked here, in this route's transaction, and nowhere else — the
    Add button is the only thing that needs it.
    """
    return {
        "page": "properties",
        "properties": prop_svc.list_properties(db),
        "upgrade": _upgrade_context(db),
    }


def _upgrade_context(db: Session) -> dict | None:
    prompt = prop_svc.home_upgrade_prompt(db)
    if prompt is None:
        return None
    return {**prompt, "is_owner": current_role.get() == "owner"}


@router.get("/")
@declares("property.view", Access.COLLECTION)
def list_properties(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "properties.html", _list_context(db))


@router.get("/new")
@declares("property.add", Access.ACCOUNT)
def new_property_form(request: Request, db: Session = Depends(get_db)):
    # At the cap, the choice replaces the form — the same one the popup on /properties offers,
    # for a bookmarked link or a browser without JS. Showing a form that can only be refused
    # would be the dead end §4.1 exists to prevent.
    return templates.TemplateResponse(
        request,
        "property_form.html",
        {
            "page": "properties",
            "property": None,
            "property_types": [t.value for t in PropertyType],
            "property_statuses": [s.value for s in PropertyStatus],
            "upgrade": _upgrade_context(db),
        },
    )


@router.post("/", response_class=HTMLResponse)
@declares("property.add", Access.ACCOUNT)
def create_property(
    request: Request,
    name: str = Form(...),
    address: str = Form(""),
    property_type: str = Form("other"),
    status: str = Form("open"),
    db: Session = Depends(get_db),
):
    prop_svc.create_property(
        db,
        name=name,
        address=address or None,
        property_type=PropertyType(property_type),
        status=PropertyStatus(status),
    )
    # Flushed here, while the tenant context is still bound: the insert's `account_id` is stamped
    # at flush, and `get_db`'s commit runs after the context has been reset. Rendering the list
    # used to force this flush as a side effect of the query.
    db.flush()
    # A plain form post, answered with a redirect (post/redirect/get). It used to be `hx-post`,
    # and htmx 2 does not swap a 4xx — so a plan refusal (402) left the user staring at an
    # unchanged form. A normal post renders the paywall page like any other navigation.
    return RedirectResponse("/properties/", status_code=303)


def _detail_ctx(db: Session, slug: str) -> dict:
    from mihomes.models.asset import Asset
    from mihomes.models.issue import Issue
    from mihomes.models.task import Task

    prop = prop_svc.get_property(db, slug)
    health = compute_property_health(db, prop.id)
    open_tasks = task_svc.list_tasks(db, property_id_or_slug=slug, status=TaskStatus.PENDING)
    open_issues = issue_svc.list_issues(db, property_id_or_slug=slug, open_only=True)
    assigned_staff = staff_svc.list_by_property(db, slug)
    spaces = space_svc.list_spaces(db, property_id_or_slug=slug)
    # What `delete_property` removes along with the home, so the confirmation can say so.
    delete_counts = {
        "rooms": len(spaces),
        "assets": db.query(Asset).filter(Asset.property_id == prop.id).count(),
        "tasks": db.query(Task).filter(Task.property_id == prop.id).count(),
        "issues": db.query(Issue).filter(Issue.property_id == prop.id).count(),
    }
    return {
        "page": "properties",
        "prop": prop,
        "health": health,
        "open_tasks": open_tasks,
        "open_issues": open_issues,
        "assigned_staff": assigned_staff,
        "spaces": spaces,
        "room_contents": space_svc.content_counts(db, prop.id),
        "delete_counts": delete_counts,
        "delete_blockers": prop_svc.delete_blockers(db, prop.id),
        "space_types": _SPACE_TYPES,
        "property_types": [t.value for t in PropertyType],
        "property_statuses": [s.value for s in PropertyStatus],
    }


@router.get("/{slug}")
@declares("property.view", Access.ITEM)
def property_detail(request: Request, slug: str, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "property_detail.html", _detail_ctx(db, slug))


@router.post("/{slug}/occupy", response_class=HTMLResponse)
@declares("property.edit", Access.ITEM)
def occupy(request: Request, slug: str, db: Session = Depends(get_db)):
    prop = prop_svc.occupy_property(db, slug)
    return with_toast(templates.TemplateResponse(
        request,
        "partials/property_status_badge.html",
        {"prop": prop},
    ), f"{prop.name} marked open")


@router.post("/{slug}/vacate", response_class=HTMLResponse)
@declares("property.edit", Access.ITEM)
def vacate(request: Request, slug: str, db: Session = Depends(get_db)):
    prop = prop_svc.vacate_property(db, slug)
    return with_toast(templates.TemplateResponse(
        request,
        "partials/property_status_badge.html",
        {"prop": prop},
    ), f"{prop.name} vacated")


@router.post("/{slug}/edit", response_class=HTMLResponse)
@declares("property.edit", Access.ITEM)
def edit_property(
    request: Request,
    slug: str,
    name: str = Form(""),
    address: str = Form(""),
    property_type: str = Form(""),
    status: str = Form(""),
    db: Session = Depends(get_db),
):
    kwargs: dict = {}
    if name:
        kwargs["name"] = name
    if address is not None:
        kwargs["address"] = address or None
    if property_type:
        kwargs["property_type"] = PropertyType(property_type)
    if status:
        kwargs["status"] = PropertyStatus(status)
    prop = prop_svc.update_property(db, slug, **kwargs)
    # Edited from the property's own page, so answer with that page — it used to swap in the
    # list while the address bar still named the property. A rename can change the slug, so
    # the URL follows it.
    resp = templates.TemplateResponse(request, "property_detail.html", _detail_ctx(db, prop.slug))
    resp.headers["HX-Push-Url"] = f"/properties/{prop.slug}"
    return with_toast(resp, "Property saved")


@router.post("/{slug}/delete", response_class=HTMLResponse)
@declares("property.delete", Access.ITEM)
def delete_property(
    request: Request, slug: str, confirm: str = Form(""), db: Session = Depends(get_db)
):
    # Deleting a property erases every room, asset, task and issue in it, unrecoverably. The
    # confirmation dialog posts `confirm=yes` only after the user has ticked that they exported
    # their data and pressed Yes; anything else — a stray or scripted POST — is refused here,
    # so the dialog is not the only thing standing between a click and the data.
    if confirm != "yes":
        return HTMLResponse(
            "Confirm the deletion first — nothing was deleted.", status_code=400
        )
    try:
        name = prop_svc.delete_property(db, slug)
    except ValueError as exc:
        # The service refuses while records it will not erase are attached; the short text
        # becomes an error toast (static/app.js) and the page stays as it was.
        return HTMLResponse(str(exc), status_code=400)
    # The page this came from no longer exists; keep a refresh from landing on a 404.
    resp = templates.TemplateResponse(request, "properties.html", _list_context(db))
    resp.headers["HX-Push-Url"] = "/properties/"
    return with_toast(resp, f"{name} deleted")


def _rooms_ctx(db, slug: str) -> dict:
    spaces = space_svc.list_spaces(db, property_id_or_slug=slug)
    return {
        "spaces": spaces,
        "prop_slug": slug,
        "room_contents": space_svc.content_counts(db, slug),
        "space_types": _SPACE_TYPES,
    }


@router.post("/{slug}/spaces", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
def create_space(
    request: Request,
    slug: str,
    name: str = Form(...),
    space_type: str = Form(""),
    db: Session = Depends(get_db),
):
    space_svc.create_space(db, name, slug, space_type=space_type or None)
    return with_toast(templates.TemplateResponse(request, "partials/rooms_list.html", _rooms_ctx(db, slug)),
                      "Room added")


@router.post("/{slug}/spaces/{space_slug}/delete", response_class=HTMLResponse)
@declares("inventory.manage", Access.ITEM)
def delete_space(request: Request, slug: str, space_slug: str, db: Session = Depends(get_db)):
    name = space_svc.delete_space(db, space_slug)
    return with_toast(templates.TemplateResponse(request, "partials/rooms_list.html", _rooms_ctx(db, slug)),
                      f"{name} deleted")
