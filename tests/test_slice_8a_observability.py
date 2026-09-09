from __future__ import annotations

from types import SimpleNamespace

from app.middleware.logging_middleware import _route_template, _safe_trace_id


class _Request:
    def __init__(self, trace_id: str = "", route_path: str | None = None) -> None:
        self.headers = {"X-Trace-Id": trace_id} if trace_id else {}
        route = SimpleNamespace(path=route_path) if route_path is not None else None
        self.scope = {"route": route}


def test_trace_id_accepts_only_bounded_server_safe_format() -> None:
    supplied = "trace-" + "a" * 32
    assert _safe_trace_id(_Request(trace_id=supplied)) == supplied

    rejected = _safe_trace_id(_Request(trace_id="patient-name@example.test"))
    assert rejected.startswith("trace-")
    assert "patient-name" not in rejected


def test_logging_uses_route_template_not_raw_request_path() -> None:
    request = _Request(route_path="/api/v2/patients/{patient_id}")
    request.url = SimpleNamespace(path="/api/v2/patients/patient-sensitive-id")
    assert _route_template(request) == "/api/v2/patients/{patient_id}"
    assert "patient-sensitive-id" not in _route_template(request)


def test_unresolved_route_never_falls_back_to_raw_path() -> None:
    request = _Request(route_path=None)
    request.url = SimpleNamespace(path="/secret/value/in/path")
    assert _route_template(request) == "unresolved"
