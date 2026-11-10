"""The app's routes as a flat list, on every FastAPI version production can run.

**Why this exists (2026-11-10).** From FastAPI 0.137 `include_router` no longer copies a router's
routes into `app.routes`; it adds one lazy `_IncludedRouter` per included router. evo-dev runs
0.139.2. Every test that walked `app.routes` therefore saw `/openapi.json`, `/static` and
`/healthz` and nothing else — and the worst of them did not fail, they **passed checking
nothing**: the billing owner-only test looped over zero billing routes, the leak matrix over zero
endpoints, the landing allowlist found nothing outside the allowlist. Only the route census
noticed, because it asserts a minimum count.

`effective_route_contexts()` is private FastAPI API — there is no public way to list included
routes on these versions. Isolating it here means one place to change when FastAPI moves again,
and `test_route_table.py` fails loudly (rather than every caller passing vacuously) if this stops
seeing the whole app.
"""

from __future__ import annotations

import copy

from fastapi.routing import APIRoute


def all_routes(app) -> list:
    """Every route with its full path. Included routers are expanded, nested ones too.

    A route from an included router comes back as a shallow copy of the router's own route with
    `path` set to the full mounted path, so `isinstance(r, APIRoute)`, `r.endpoint` and
    `r.methods` behave exactly as they did when FastAPI copied routes eagerly.
    """
    routes = []
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts is None:
            routes.append(route)
            continue
        for context in contexts():
            flat = copy.copy(context.original_route)
            flat.path = context.path
            routes.append(flat)
    return routes


def api_routes(app) -> list[APIRoute]:
    """`all_routes`, keeping only FastAPI endpoints (no static mounts, no plain Starlette routes)."""
    return [route for route in all_routes(app) if isinstance(route, APIRoute)]
