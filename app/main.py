"""Nexa Care FastAPI entrypoint."""

from fastapi import FastAPI

from app.api.routes import router as api_router

app = FastAPI(title="Nexa Care API", version="0.1.0")

app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok"}
