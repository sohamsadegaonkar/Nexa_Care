"""Single-use clinician original access, bound to a patient-signed document decision."""

import base64
import binascii
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal

from app.core.consent_gate import require_consent, validate_consent_for_patient
from app.core.database import get_db_session
from app.core.dependencies import (
    AuthenticatedPatientSession,
    enforce_current_clinical_capability,
    get_current_patient_session,
    require_clinical_capability,
)
from app.models.original_document_access import OriginalDocumentAccess as Access
from app.models.patient import Patient
from app.models.patient_device_keys import PatientDeviceKey
from app.models.patient_records import DocumentReference
from app.models.provider import HospitalRegistry, ProviderIdentity
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.security.provider_capabilities import ClinicalCapability
from app.services.approved_access_capability import (
    ApprovedAccessCapability,
    CLINICAL_ACCESS_SESSION_GRANT_TYPE,
)
from app.services.audit_outbox import enqueue_audit_event
from app.services.document_storage import get_document_storage
from app.services.original_document_access import (
    context,
    owned_source,
    verify_device_decision,
    require_live_parent_for_approval,
)
from app.services.patient_external_record_lifecycle import (
    assert_patient_external_record_access_active,
)
from app.services.patient_external_record_source_safety import (
    PatientSourceSafetyError,
    qualify_patient_source_for_extraction,
)
from app.services.original_document_preview import require_passive_pdf


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def private(request: Request):
            try:
                response = await handle(request)
            except RequestValidationError:
                failure = HTTPException(422, "Invalid document request")
            except HTTPException as exc:
                failure = exc
            except Exception:
                failure = HTTPException(503, "Document access unavailable")
            else:
                response.headers["Cache-Control"] = "private, no-store"
                response.headers["X-Content-Type-Options"] = "nosniff"
                return response

            actor = getattr(request.state, "original_access_actor", None)
            if actor:
                try:
                    await append_audit_log_or_503(
                        audit_context=request.state.original_access_audit,
                        actor_uid=actor,
                        target_id="ORIGINAL_DOCUMENT",
                        event_type="ORIGINAL_DOCUMENT_ACCESS_DENIED",
                        status="DENIED",
                        metadata={"status_code": failure.status_code},
                    )
                except Exception:
                    failure = HTTPException(503, "Document access unavailable")
            failure.headers = {
                **(failure.headers or {}),
                "Cache-Control": "private, no-store",
            }
            raise failure from None

        return private


router = APIRouter(prefix="/api/v2/original-access/v1", route_class=PrivateRoute)


class SelectDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID


class SelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class Decision(SelectRequest):
    decision: Literal["approved", "denied"]
    key_id: UUID
    signature: str = Field(min_length=1, max_length=160)


def now():
    return datetime.now(timezone.utc)


def state(row):
    if row.status not in ("denied", "revoked") and now() >= row.expires_at:
        return "expired"
    return row.status


def receipt(row):
    return {
        "request_id": str(row.id),
        "status": state(row),
        "expires_at": row.expires_at,
    }


async def history(
    request: Request,
    grant=Depends(require_consent("clinical_summary", authority_check_only=True)),
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.RECORD_READ)
    ),
):
    request.state.original_access_audit = current_audit_context(AuditDomain.CONSENT)
    request.state.original_access_actor = provider.actor_uid
    if (
        not isinstance(grant, ApprovedAccessCapability)
        or grant.is_break_glass
        or grant.grant_type != CLINICAL_ACCESS_SESSION_GRANT_TYPE
        or grant.allowed_operations != ("READ_CLINICAL_HISTORY",)
        or not grant.clinical_session_id
        or not provider.session_binding
    ):
        raise HTTPException(403, "Signed history access required")
    return grant, provider


async def patient_principal(
    request: Request,
    principal: AuthenticatedPatientSession = Depends(get_current_patient_session),
):
    request.state.original_access_audit = current_audit_context(AuditDomain.CONSENT)
    request.state.original_access_actor = principal.patient_id
    return UUID(principal.patient_id)


async def record(db, row, actor, event):
    await enqueue_audit_event(
        db,
        audit_context=current_audit_context(AuditDomain.CONSENT),
        idempotency_key=f"original:{row.id}:{event}",
        actor_id=str(actor),
        event_type=event,
        target_id=str(row.patient_id),
        patient_id=str(row.patient_id),
        metadata={
            "audit_transaction_id": str(row.id),
            "hospital_id": str(row.hospital_id),
            "purpose": "review_original_for_current_care",
        },
    )
    await db.commit()


def provider_filter(bindings):
    grant, provider = bindings
    return (
        Access.patient_id == UUID(grant.patient_id),
        Access.provider_id == provider.provider.provider_id,
        Access.hospital_id == provider.hospital_id,
        Access.history_id == UUID(grant.clinical_session_id),
        Access.affiliation_id == provider.affiliation.affiliation_id,
        Access.session_hash
        == hashlib.sha256(provider.session_binding.encode()).hexdigest(),
    )


async def find(db, selection, *conditions):
    row = (
        await db.execute(
            select(Access)
            .where(Access.id == selection.request_id, *conditions)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Document request unavailable")
    return row


@router.post("/request", status_code=201)
async def create(
    payload: SelectDocument,
    request: Request,
    bindings=Depends(history),
    db: AsyncSession = Depends(get_db_session),
):
    grant, provider = bindings
    await enforce_current_clinical_capability(
        request=request,
        provider=provider,
        db=db,
        capability=ClinicalCapability.CONSENT_REQUEST,
    )
    patient = UUID(grant.patient_id)
    await assert_patient_external_record_access_active(db, patient_id=str(patient))
    # Serialize issuance per patient so concurrent retries reuse the same pending request.
    await db.execute(
        select(Patient.patient_uuid)
        .where(Patient.patient_uuid == patient)
        .with_for_update()
    )
    existing = (
        await db.execute(
            select(Access)
            .where(
                *provider_filter(bindings),
                Access.document_id == payload.document_id,
                Access.status == "pending",
                Access.expires_at > now(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return receipt(existing)
    live_count = (
        await db.execute(
            select(func.count())
            .select_from(Access)
            .where(
                Access.patient_id == patient,
                Access.status.in_(("pending", "approved", "opened")),
                Access.expires_at > now(),
            )
        )
    ).scalar_one()
    if live_count >= 25:
        raise HTTPException(429, "Too many active document requests")
    doc, source = await owned_source(db, patient, payload.document_id)
    key = (
        await db.execute(
            select(PatientDeviceKey.id)
            .where(
                PatientDeviceKey.patient_id == patient,
                PatientDeviceKey.status == "active",
                PatientDeviceKey.revoked_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(409, "An enrolled patient device is required")
    issued = now()
    expiry = min(
        issued + timedelta(minutes=5),
        datetime.fromisoformat(grant.expires_at.replace("Z", "+00:00")),
    )
    if expiry <= issued:
        raise HTTPException(403, "History approval expired")
    row = Access(
        id=uuid4(),
        patient_id=patient,
        document_id=doc.id,
        provider_id=provider.provider.provider_id,
        hospital_id=provider.hospital_id,
        history_id=UUID(grant.clinical_session_id),
        affiliation_id=provider.affiliation.affiliation_id,
        source_hash=source.content_hash,
        session_hash=hashlib.sha256(provider.session_binding.encode()).hexdigest(),
        nonce=secrets.token_hex(32),
        issued_at=issued,
        expires_at=expiry,
        status="pending",
    )
    db.add(row)
    await record(db, row, provider.actor_uid, "ORIGINAL_DOCUMENT_REQUESTED")
    return receipt(row)


@router.get("/mine")
async def mine(
    patient=Depends(patient_principal), db: AsyncSession = Depends(get_db_session)
):
    await assert_patient_external_record_access_active(db, patient_id=str(patient))
    result = await db.execute(
        select(
            Access,
            ProviderIdentity.display_name,
            HospitalRegistry.display_name,
            DocumentReference.document_type,
            DocumentReference.uploaded_at,
        )
        .join(ProviderIdentity, ProviderIdentity.id == Access.provider_id)
        .join(HospitalRegistry, HospitalRegistry.id == Access.hospital_id)
        .join(DocumentReference, DocumentReference.id == Access.document_id)
        .where(Access.patient_id == patient, DocumentReference.patient_id == patient)
        .order_by(
            case(
                (
                    (Access.expires_at > now())
                    & Access.status.in_(("pending", "approved", "opened")),
                    0,
                ),
                else_=1,
            ),
            Access.issued_at.desc(),
            Access.id.desc(),
        )
        .limit(100)
    )
    return [
        {
            "context": context(row),
            **receipt(row),
            "provider": provider,
            "hospital": hospital,
            "category": category,
            "uploaded_at": uploaded,
        }
        for row, provider, hospital, category, uploaded in result
    ]


@router.post("/decision")
async def decide(
    payload: Decision,
    patient=Depends(patient_principal),
    db: AsyncSession = Depends(get_db_session),
):
    row = await find(db, payload, Access.patient_id == patient)
    await assert_patient_external_record_access_active(db, patient_id=str(patient))
    if state(row) != "pending":
        raise HTTPException(409, "Request is no longer pending")
    try:
        signature = base64.b64decode(payload.signature, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(403, "Document approval unavailable") from None
    if payload.decision == "approved":
        await require_live_parent_for_approval(db, row)
        await owned_source(db, row.patient_id, row.document_id)
    await verify_device_decision(db, row, payload.key_id, signature, payload.decision)
    if state(row) != "pending":
        raise HTTPException(409, "Request expired")
    row.signature, row.key_id, row.status = signature, payload.key_id, payload.decision
    await record(
        db,
        row,
        patient,
        "ORIGINAL_DOCUMENT_APPROVED"
        if payload.decision == "approved"
        else "ORIGINAL_DOCUMENT_DENIED",
    )
    return receipt(row)


@router.post("/revoke")
async def revoke(
    payload: SelectRequest,
    patient=Depends(patient_principal),
    db: AsyncSession = Depends(get_db_session),
):
    row = await find(db, payload, Access.patient_id == patient)
    if state(row) in ("pending", "approved", "opened"):
        row.status = "revoked"
        await record(db, row, patient, "ORIGINAL_DOCUMENT_REVOKED")
    return receipt(row)


@router.post("/status")
async def status(
    payload: SelectRequest,
    bindings=Depends(history),
    db: AsyncSession = Depends(get_db_session),
):
    row = await find(db, payload, *provider_filter(bindings))
    await assert_patient_external_record_access_active(
        db, patient_id=str(row.patient_id)
    )
    if state(row) in ("opened", "approved"):
        await verify_device_decision(db, row, row.key_id, row.signature, "approved")
        _, source = await owned_source(db, row.patient_id, row.document_id)
        if source.content_hash != row.source_hash:
            raise HTTPException(409, "Original unavailable")
    return receipt(row)


@router.post("/open")
async def open_original(
    payload: SelectRequest,
    request: Request,
    bindings=Depends(history),
    token: str = Header(alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    grant, provider = bindings
    row = await find(db, payload, *provider_filter(bindings))
    if state(row) != "approved":
        raise HTTPException(403, "Document approval unavailable")
    patient = str(row.patient_id)
    await assert_patient_external_record_access_active(db, patient_id=patient)
    await verify_device_decision(db, row, row.key_id, row.signature, "approved")
    _, source = await owned_source(db, row.patient_id, row.document_id)
    if source.content_hash != row.source_hash:
        raise HTTPException(409, "Original unavailable")
    data = await get_document_storage().get_patient_document_bytes(
        source.storage_ref, patient_id=patient, expected_size=source.size
    )
    if len(data) != source.size:
        raise HTTPException(409, "Original unavailable")
    try:
        await qualify_patient_source_for_extraction(
            data, mime_type=source.content_type, expected_hash=row.source_hash
        )
    except PatientSourceSafetyError as exc:
        raise HTTPException(
            503 if exc.retryable else 415, "Original unavailable"
        ) from None
    if source.content_type == "application/pdf":
        require_passive_pdf(data)
    await enforce_current_clinical_capability(
        request=request,
        provider=provider,
        db=db,
        capability=ClinicalCapability.RECORD_READ,
    )
    # Re-read durable lifecycle state after scanning; the first authorization may have populated the identity map.
    live = await validate_consent_for_patient(
        patient, "clinical_summary", provider, token, db, authority_check_only=True
    )
    if (
        not isinstance(live, ApprovedAccessCapability)
        or live.clinical_session_id != grant.clinical_session_id
    ):
        raise HTTPException(403, "History approval unavailable")
    await assert_patient_external_record_access_active(db, patient_id=patient)
    if state(row) != "approved":
        raise HTTPException(403, "Document approval expired")
    row.status = "opened"
    await record(db, row, provider.actor_uid, "ORIGINAL_DOCUMENT_OPENED")
    return Response(
        data,
        media_type=source.content_type,
        headers={
            "Content-Disposition": "attachment",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
