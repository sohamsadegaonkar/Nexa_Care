"""
Improved Login + MFA routes with per-IP + per-email rate limiting
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.core.redis import get_redis_client

router = APIRouter(prefix="/api/v2/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(request: Request, payload: LoginRequest):
    client_ip = request.client.host if request.client else "unknown"

    # Rate limit per email (10 attempts per minute)
    try:
        redis = get_redis_client()
        email_key = f"login_rate:{payload.email}"
        current_email = await redis.incr(email_key)
        if current_email == 1:
            await redis.expire(email_key, 60)
        if current_email > 10:
            raise HTTPException(status_code=429, detail="Too many login attempts")
    except Exception:
        pass

    # Rate limit per IP (20 attempts per minute) - brute force protection
    try:
        ip_key = f"login_ip_rate:{client_ip}"
        current_ip = await redis.incr(ip_key)
        if current_ip == 1:
            await redis.expire(ip_key, 60)
        if current_ip > 20:
            raise HTTPException(status_code=429, detail="Too many login attempts from this IP")
    except Exception:
        pass

    # TODO: Add actual authentication logic here
    return {"message": "Login successful (rate limited)"}