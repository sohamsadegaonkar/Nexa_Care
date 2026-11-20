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
