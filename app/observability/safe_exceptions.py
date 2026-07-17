"""Allow-list based exception metadata for production logs and responses.

Exception messages are attacker-controlled in many failure modes.  This module
therefore never serializes ``str(exc)``, ``repr(exc)``, exception arguments, or
locals/tracebacks into the general application or audit sinks.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from typing import Any

from app.core.request_context import request_id_var, trace_id_var

_VALIDATION_TYPES = {
    "missing": "Required field is missing.",
    "value_error.missing": "Required field is missing.",
    "string_type": "Value must be a string.",
    "type_error.str": "Value must be a string.",
    "int_type": "Value must be an integer.",
    "type_error.integer": "Value must be an integer.",
    "bool_type": "Value must be a boolean.",
    "uuid_parsing": "Value must be a valid UUID.",
    "value_error.uuid": "Value must be a valid UUID.",
    "json_invalid": "Request JSON is invalid.",
    "extra_forbidden": "Unexpected field is not permitted.",
    "value_error.extra": "Unexpected field is not permitted.",
    "string_too_short": "Value is shorter than permitted.",
    "string_too_long": "Value is longer than permitted.",
    "greater_than": "Value is below the permitted range.",
    "less_than": "Value is above the permitted range.",
}


def _exception_code(exc: BaseException, subsystem: str) -> tuple[str, bool, int | None]:
    name = type(exc).__name__.lower()
    module = type(exc).__module__.lower()
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status <= 599:
        return ("HTTP_REQUEST_REJECTED", status >= 500, status)
    if "validation" in name or name in {"jsondecodeerror"}:
        return ("VALIDATION_ERROR", False, 422)
    if any(token in name or token in module for token in ("sql", "database", "asyncpg", "integrity")):
        return ("DATABASE_OPERATION_FAILED", True, 503)
    if "redis" in name or "redis" in module:
        return ("REDIS_OPERATION_FAILED", True, 503)
    if "kms" in name or subsystem == "kms":
        return ("KMS_OPERATION_FAILED", True, 503)
    if any(token in name or token in module for token in ("crypto", "decrypt", "encrypt", "signature")):
        return ("CRYPTOGRAPHIC_OPERATION_FAILED", False, 500)
    if subsystem in {"extraction", "document_extraction"}:
        return ("EXTRACTION_OPERATION_FAILED", True, 503)
    if isinstance(exc, TimeoutError):
        return ("OPERATION_TIMEOUT", True, 504)
    return ("INTERNAL_OPERATION_FAILED", False, 500)


def _safe_location(location: Any) -> list[str | int]:
    if not isinstance(location, (list, tuple)):
        return []
    safe: list[str | int] = []
    for item in location[:12]:
        if isinstance(item, int):
            safe.append(item)
        elif isinstance(item, str) and item.replace("_", "").replace("-", "").isalnum():
            safe.append(item[:64])
    return safe


def _validation_issues(exc: BaseException) -> list[dict[str, Any]]:
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return []
    try:
        raw_errors = errors(include_url=False, include_context=False, include_input=False)
    except TypeError:
        try:
            raw_errors = errors()
        except Exception:
            return []
    except Exception:
        return []

    issues: list[dict[str, Any]] = []
    if not isinstance(raw_errors, list):
        return issues
    for raw in raw_errors[:50]:
        if not isinstance(raw, Mapping):
            continue
        rule = str(raw.get("type") or "validation_error")[:96]
        issues.append(
            {
                "location": _safe_location(raw.get("loc")),
                "rule": rule,
                "message": _VALIDATION_TYPES.get(rule, "Value failed validation."),
            }
        )
    return issues


def safe_exception_metadata(
    exc: BaseException,
    *,
    subsystem: str,
    operation: str,
    error_id: str | None = None,
) -> dict[str, Any]:
    """Return only explicitly approved exception metadata."""

    internal_id = error_id or f"err-{uuid.uuid4().hex}"
    code, retryable, http_status = _exception_code(exc, subsystem)
    chain: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        chain.append(type(current).__name__[:128])
        current = current.__cause__ or current.__context__

    metadata: dict[str, Any] = {
        "exception_class": type(exc).__name__[:128],
        "error_code": code,
        "error_id": internal_id,
        "subsystem": subsystem[:64],
        "operation": operation[:96],
        "retryable": retryable,
        "http_status": http_status,
        "correlation_id": trace_id_var.get() or request_id_var.get() or internal_id,
        "exception_chain": chain,
    }
    issues = _validation_issues(exc)
    if issues:
        metadata["validation_issues"] = issues
    return metadata


def log_safe_exception(
    logger: logging.Logger,
    level: int | BaseException,
    event: str | None = None,
    exc: BaseException | None = None,
    *,
    subsystem: str,
    operation: str,
    fields: Mapping[str, str | int | bool | None] | None = None,
) -> dict[str, Any]:
    """Emit one sanitized JSON log record and return its metadata."""

    if isinstance(level, BaseException):
        exc = level
        log_level = logging.ERROR
        log_event = event or f"{operation}_failed"
    else:
        log_level = level
        if exc is None:
            raise TypeError("exc is required when level is provided")
        log_event = event or f"{operation}_failed"

    metadata = safe_exception_metadata(exc, subsystem=subsystem, operation=operation)
    payload: dict[str, Any] = {"event": log_event[:96], **metadata}
    if fields:
        payload["context"] = {
            str(key)[:64]: value
            for key, value in fields.items()
            if isinstance(value, (str, int, bool)) or value is None
        }
    logger.log(log_level, json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return metadata


def safe_error_response(metadata: Mapping[str, Any], message: str) -> dict[str, Any]:
    return {
        "error_code": metadata["error_code"],
        "message": message,
        "retryable": bool(metadata["retryable"]),
        "error_id": metadata["error_id"],
        "correlation_id": metadata["correlation_id"],
    }
