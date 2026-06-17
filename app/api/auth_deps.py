import os
import hmac
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="X-Provider-Key", auto_error=True)

def verify_provider(api_key: str = Security(API_KEY_HEADER)) -> str:
    """
    Validates that the incoming request is from a trusted provider/kiosk.
    Uses constant-time comparison to prevent timing attacks.
    """
    # We rely on config.py to fail-fast if this is not set at boot.
    expected_key = os.environ.get("PROVIDER_API_KEY", "")
    
    if not expected_key or not hmac.compare_digest(api_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing Provider API Key."
        )
    return api_key