"""Cryptographic contract and canonical source resolution for original document access."""

import json
from datetime import timezone
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_async_redis_client
from app.models.clinical_access_session import ClinicalAccessSessionRecord
from app.services.approved_access_capability import (
    CLINICAL_ACCESS_SESSION_GRANT_TYPE,
    load_server_bound_capability,
)
from app.services.clinical_access_session_store import validate_active_session
from app.services.consent_v3_authority import (
    ConsentV3ProviderIneligible,
    assert_live_consent_v3_provider,
)
from app.models.patient_device_keys import PatientDeviceKey
from app.models.patient_external_record_import import (
    PatientExternalRecordImport as Import,
)
from app.models.patient_records import DocumentReference
from app.models.pipeline import DocumentStorage as Source, ExtractionJob
from app.services.patient_external_record_source_safety import (
    effective_patient_source_max_bytes,
)

PROTOCOL = "nexa-original-document-v1"
PURPOSE = "review_original_for_current_care"


def context(row):
    return {
        "protocol_version": PROTOCOL,
        "operation": "READ_DOCUMENTS",
        "purpose": PURPOSE,
        "request_id": str(row.id),
        "document_id": str(row.document_id),
        "patient_id": str(row.patient_id),
        "provider_id": str(row.provider_id),
        "hospital_id": str(row.hospital_id),
        "history_id": str(row.history_id),
        "affiliation_id": str(row.affiliation_id),
        "session_hash": row.session_hash,
        "source_hash": row.source_hash,
        "nonce": row.nonce,
        "issued_at": row.issued_at.astimezone(timezone.utc).isoformat(),
        "expires_at": row.expires_at.astimezone(timezone.utc).isoformat(),
    }


def signing_input(row, key, decision):
    payload = {
        **context(row),
        "decision": decision,
        "key_id": str(key.id),
        "device_id": str(key.device_id),
        "key_version": key.key_version,
        "key_fingerprint": key.public_key_fingerprint,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")


async def verify_device_decision(db, row, key_id, signature, decision):
    key = (
        await db.execute(
            select(PatientDeviceKey)
            .where(
                PatientDeviceKey.patient_id == row.patient_id,
                PatientDeviceKey.id == key_id,
                PatientDeviceKey.status == "active",
                PatientDeviceKey.revoked_at.is_(None),
            )
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(403, "Document approval unavailable")
    try:
        public = serialization.load_der_public_key(key.device_public_key)
        if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(
            public.curve, ec.SECP256R1
        ):
            raise ValueError("unsupported signing key")
        public.verify(
            signature, signing_input(row, key, decision), ec.ECDSA(hashes.SHA256())
        )
    except (InvalidSignature, ValueError, TypeError):
        raise HTTPException(403, "Document approval unavailable") from None


async def owned_source(db: AsyncSession, patient: UUID, document: UUID):
    query = (
        select(DocumentReference, Source)
        .join(Import, Import.final_record_id == DocumentReference.id)
        .join(Source, Source.id == Import.source_document_id)
        .where(
            DocumentReference.patient_id == patient,
            DocumentReference.id == document,
            DocumentReference.extraction_job_id.is_(None),
            Import.patient_id == patient,
            Import.final_record_type == "DOCUMENT_REFERENCE",
            Import.status == "COMPLETED",
            Import.review_completed_at.is_not(None),
            Source.patient_id == patient,
            Source.tenant_id.is_(None),
            Source.source_system == "patient_self",
            Source.upload_purpose == "patient_external_record_import",
            Source.content_hash == Import.content_hash,
            Source.storage_ref == DocumentReference.storage_ref,
            Source.content_type.in_(("application/pdf", "image/png", "image/jpeg")),
            Source.size > 0,
            Source.size <= effective_patient_source_max_bytes(),
            ~select(ExtractionJob.id)
            .where(ExtractionJob.document_id == Source.id)
            .exists(),
        )
        .with_for_update(read=True)
    )
    pair = (await db.execute(query)).one_or_none()
    if pair is None:
        raise HTTPException(404, "Original unavailable")
    return pair


async def require_live_parent_for_approval(db, row):
    """Patient-side approval reuses the original server-held initiation assurance."""
    parent = await db.get(
        ClinicalAccessSessionRecord, row.history_id, populate_existing=True
    )
    if parent is None:
        raise HTTPException(403, "History approval unavailable")
    identity = dict(
        patient_id=str(row.patient_id),
        provider_id=str(row.provider_id),
        hospital_id=str(row.hospital_id),
    )
    grant = await load_server_bound_capability(digest=parent.token_hash, **identity)
    if (
        grant is None
        or grant.is_break_glass
        or grant.grant_type != CLINICAL_ACCESS_SESSION_GRANT_TYPE
        or grant.clinical_session_id != str(row.history_id)
        or grant.allowed_operations != ("READ_CLINICAL_HISTORY",)
        or grant.provider_session_binding_hash != row.session_hash
        or not await validate_active_session(
            db,
            session_id=str(row.history_id),
            token_hash=parent.token_hash,
            consent_request_id=grant.request_id,
            provider_session_binding_hash=row.session_hash,
            allowed_operations=grant.allowed_operations,
            policy_version=grant.clinical_access_policy_version,
            **identity,
        )
    ):
        raise HTTPException(403, "History approval unavailable")
    raw = await get_async_redis_client().get(f"consent_request:{grant.request_id}")
    data = json.loads(raw) if raw else {}
    if data.get("status") != "approved" or any(
        str(data.get(k)) != v for k, v in identity.items()
    ):
        raise HTTPException(403, "History approval unavailable")
    try:
        await assert_live_consent_v3_provider(db=db, request_data=data)
    except ConsentV3ProviderIneligible:
        raise HTTPException(403, "Provider authority unavailable") from None
