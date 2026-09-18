from app.api.v2 import treatment_session_v1_claim_routes as claim_routes


def test_claim_router_exposes_only_one_time_claim_surface():
    paths = {route.path for route in claim_routes.router.routes}
    assert paths == {"/api/v2/treatment-session/v1/{request_id}/claim"}
