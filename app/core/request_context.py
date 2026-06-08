from contextvars import ContextVar
import uuid
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
span_id_var: ContextVar[str] = ContextVar("span_id", default="")

def generate_trace_id() -> str:
    """Generates a new root trace ID."""
    return f"trace-{uuid.uuid4().hex}"

def generate_span_id() -> str:
    """Generates a new span ID for a specific operation."""
    return f"span-{uuid.uuid4().hex[:16]}"