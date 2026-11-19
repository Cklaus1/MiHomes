"""`tests/route_table.py` sees the whole app — the guard that keeps route-walking tests honest.

If FastAPI changes how included routers are stored again, these fail here, by name, instead of
every security test that loops over routes silently looping over nothing.
"""

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from tests.route_table import all_routes, api_routes


def _toy_app():
    inner = APIRouter()

    @inner.get("/leaf")
    def leaf():
        return 1

    outer = APIRouter()

    @outer.post("/branch")
    def branch():
        return 1

    outer.include_router(inner, prefix="/inner")
    app = FastAPI()
    app.include_router(outer, prefix="/outer")
    return app, leaf, branch


def test_included_and_nested_routes_come_back_with_full_paths():
    app, leaf, branch = _toy_app()
    found = {(r.path, frozenset(r.methods), r.endpoint) for r in api_routes(app)}
    assert ("/outer/branch", frozenset({"POST"}), branch) in found
    assert ("/outer/inner/leaf", frozenset({"GET"}), leaf) in found


def test_nothing_is_left_wrapped():
    app, _, _ = _toy_app()
    assert not [r for r in all_routes(app) if hasattr(r, "effective_route_contexts")]


def test_the_real_app_is_fully_visible():
    """Numbers well under today's (~190 API routes) but far above what a broken walk returns (1)."""
    from mihomes.web.app import create_app

    routes = api_routes(create_app())
    paths = {r.path for r in routes}
    assert len(routes) >= 140
    assert {"/billing", "/healthz", "/tasks/", "/chat/", "/legal/terms"} <= paths
    assert all(isinstance(r, APIRoute) for r in routes)
