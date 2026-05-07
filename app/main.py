"""Nexa Care FastAPI entrypoint."""
from dotenv import load_dotenv
load_dotenv()  # loads .env into os.environ if present
from fastapi import FastAPI

from app.api.routes import router as api_router
from app.core.config import get_redis_config, get_supabase_config

app = FastAPI(title="Nexa Care API", version="0.1.0")


@app.on_event("startup")
async def _validate_required_config() -> None:
    """Fail fast if required secrets are not present."""

    get_supabase_config()
    get_redis_config()


app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok"}
