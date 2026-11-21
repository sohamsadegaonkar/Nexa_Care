"""Qualification of included-router discovery across compatible FastAPI versions."""

from fastapi import APIRouter, FastAPI

from tests.registered_routes import registered_routes


def test_nested_route_inventory_preserves_path_method_and_dependencies() -> None:
    app = FastAPI()
    middle = APIRouter()
    leaf = APIRouter()

    def authorize() -> None:
        return None

    @leaf.get("/record", dependencies=[])
    def read_record() -> dict[str, bool]:
        return {"ok": True}

    middle.include_router(leaf, prefix="/patients")
    app.include_router(middle, prefix="/api/v2")

    before = tuple(id(route) for route in app.routes)
    effective = list(registered_routes(app))
    after = tuple(id(route) for route in app.routes)

    matches = [
        route
        for route in effective
        if getattr(route, "path", None) == "/api/v2/patients/record"
        and "GET" in (getattr(route, "methods", None) or set())
    ]
    assert len(matches) == 1
    assert matches[0].endpoint is read_record
    assert before == after  # discovery must not replace or mutate runtime routes
    assert all(getattr(route, "path", None) for route in effective)
