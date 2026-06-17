import os
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

# We expect callers to pass this header
API_KEY_HEADER = APIKeyHeader(name="X-Provider-Key", auto_error=True)

def verify_provider(api_key: str = Security(API_KEY_HEADER)) -> str:
    """
    Validates that the incoming request is from a trusted provider/kiosk.
    In production, this should check against a database of active provider keys.
    """
    # Fetch from environment, fallback to a hardcoded string ONLY for local dev
    expected_key = os.getenv("PROVIDER_API_KEY", "nexa-dev-provider-secret")
    
    if api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing Provider API Key."
        )
    return api_key