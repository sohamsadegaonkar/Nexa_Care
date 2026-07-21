from dataclasses import dataclass


@dataclass
class ErrorDefinition:
    error_code: str
    message: str
    status_code: int
    retryable: bool


class Catalog:
    DB_CONNECTION_LOST = ErrorDefinition(
        error_code="DB_CONNECTION_LOST",
        message="Database connection could not be established.",
        status_code=503,
        retryable=True,
    )
    AUTH_INVALID = ErrorDefinition(
        error_code="AUTH_INVALID",
        message="Invalid or missing authentication credentials.",
        status_code=401,
        retryable=False,
    )
    DOC_TIMEOUT = ErrorDefinition(
        error_code="DOC_TIMEOUT",
        message="Document processing timed out. Please try again.",
        status_code=504,
        retryable=True,
    )
    # [NEW FIX]: Added standard fallback states so we don't default to 503
    INTERNAL_SERVER_ERROR = ErrorDefinition(
        error_code="INTERNAL_SERVER_ERROR",
        message="An unexpected system error occurred.",
        status_code=500,
        retryable=False,
    )
    VALIDATION_ERROR = ErrorDefinition(
        error_code="VALIDATION_ERROR",
        message="Invalid input or request formatting.",
        status_code=400,
        retryable=False,
    )


def get_error(error_def: ErrorDefinition) -> dict:
    return {
        "error_code": error_def.error_code,
        "message": error_def.message,
        "retryable": error_def.retryable,
    }
