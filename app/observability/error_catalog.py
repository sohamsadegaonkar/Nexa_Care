from dataclasses import dataclass

@dataclass(frozen=True)
class ErrorDefinition:
    code: str
    message: str
    http_status: int
    severity: str
    retryable: bool

class Catalog:
    DOC_TIMEOUT = ErrorDefinition(
        code="DOC-001",
        message="Document processing timed out.",
        http_status=504,
        severity="ERROR",
        retryable=True
    )

    DB_CONNECTION_LOST = ErrorDefinition(
        code="DB-001",
        message="Database connection lost.",
        http_status=503,
        severity="CRITICAL",
        retryable=True
    )

    AUTH_INVALID = ErrorDefinition(
        code="AUTH-001",
        message="Invalid token.",
        http_status=401,
        severity="WARNING",
        retryable=False
    )

def get_error(definition: ErrorDefinition) -> dict:
    return {
        "error_code": definition.code,
        "message": definition.message,
        "retryable": definition.retryable
    }