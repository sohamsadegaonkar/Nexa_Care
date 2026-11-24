"""Read-only registration introspection across FastAPI router layouts.

FastAPI 0.137+ preserves included routers as nested _IncludedRouter nodes.
Inspect the framework's effective candidates instead of assuming the private
`app.routes` storage is flat. This helper must never change the runtime
router, authorize a request, or silently hide a route.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def registered_routes(app_or_router: Any) -> Iterator[Any]:
    """Yield effective routes including nested prefixes and dependencies.

    Older FastAPI already flattens routes, so this works for both layouts.
    Effective candidate views expose the resolved path, methods, endpoint and
    dependant used by the runtime; recurse only through actual included routers.
    """
    for route in app_or_router.routes:
        yield from _walk(route)


def _walk(route: Any) -> Iterator[Any]:
    if type(route).__name__ == "_IncludedRouter":
        candidates = route.effective_candidates()
        for child in candidates:
            yield from _walk(child)
        for child in route.effective_low_priority_routes():
            yield from _walk(child)
    else:
        yield route
