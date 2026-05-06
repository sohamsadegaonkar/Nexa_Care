"""Nexa Care FastAPI entrypoint."""

from fastapi import FastAPI

app = FastAPI(title="Nexa Care API", version="0.1.0")


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok"}
