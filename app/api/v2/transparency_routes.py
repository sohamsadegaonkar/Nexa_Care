from fastapi import APIRouter

# Deliberately empty. The former mock access-log route was never registered and
# has been removed. Patient transparency is served by the audited
# /api/v2/patient/me/access-history endpoint.
router = APIRouter(prefix="/api/v2/patient", tags=["transparency"])
