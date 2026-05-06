"""API router for Nexa Care endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/register")
async def register_patient() -> dict:
    return {"message": "register endpoint placeholder"}


@router.get("/view-record")
async def view_record() -> dict:
    return {"message": "view-record endpoint placeholder"}
