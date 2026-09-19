"""Transactional medication-catalog governance application boundary.

No HTTP routes are introduced here. These services operate only on global
catalog authority and never read or write patient clinical records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.medication_catalog import (
    DrugScheduleClass,
    MedicationCatalogEmergencyDeny,
    MedicationCatalogEntry,
    MedicationCatalogEvidence,
    MedicationCatalogRelease,
    MedicationCatalogReleaseStatus,
    MedicationEmergencyAction,
    MedicationEmergencyReason,
    MedicationEvidenceAuthority,
    MedicationEvidenceDimension,
    NdpsClass,
    NexaHighRiskClass,
    RegulatoryProductStatus,
    SpecialRecordkeepingClass,
    TelemedicineClass,
    TerminologyConceptStatus,
)
from app.models.provider import (
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.security.audit_context import AuditContext, AuditDomain
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
)
from app.services.audit_outbox import enqueue_audit_event
from app.services.medication_catalog_manifest import (
    build_release_manifest_bytes,
    candidate_entry_digest,
    canonicalize_json,
    sha256_hex,
)
from app.services.medication_catalog_policy import (
    MEDICATION_CATALOG_POLICY_VERSION,
    MedicationCatalogEntryPolicyFacts,
    classifications_support_v1_universal,
    derive_v1_universal_allowed,
)
from app.services.medication_catalog_signing import (
    CATALOG_SIGNATURE_ALGORITHM,
    MedicationCatalogSigningError,
    MedicationCatalogSigningProvider,
    signature_from_text,
    signature_to_text,
)
from app.services.policy_service import validate_idempotency_key
from app.services.provider_trust_authorization import (
    ProviderTrustAuthorizationService,
    TrustManagementAuthentication,
)

_IDEMPOTENCY_TENANT = "platform-medication-catalog"
_MFA_FRESHNESS_WINDOW = timedelta(minutes=15)
_MAX_RELEASE_ENTRIES = 100
_MAX_POSITIVE_ENTRIES = 20
_MAX_EVIDENCE_PER_ENTRY = 32

_OPERATION_CREATE = "medication.catalog.release.create.v1"
_OPERATION_ENTRY = "medication.catalog.entry.upsert.v1"
_OPERATION_EVIDENCE = "medication.catalog.evidence.replace.v1"
_OPERATION_REVIEW = "medication.catalog.entry.review.v1"
_OPERATION_QUALIFY = "medication.catalog.release.qualify.v1"
_OPERATION_ACTIVATE = "medication.catalog.release.activate.v1"
_OPERATION_REVOKE = "medication.catalog.release.revoke.v1"
_OPERATION_EMERGENCY = "medication.catalog.emergency.v1"

_IDEMPOTENCY_SELECT = text("""
    SELECT request_hash, response_status, response_payload
    FROM public.mutation_idempotency
    WHERE tenant_id = :tenant_id
      AND operation = :operation
      AND idempotency_key = :key
""")
_IDEMPOTENCY_RESERVE = text("""
    INSERT INTO public.mutation_idempotency
      (tenant_id, actor_id, operation, resource_id, idempotency_key,
       request_hash, created_at, retention_expires_at)
    VALUES
      (:tenant_id, :actor_id, :operation, :resource_id, :key,
       :request_hash, now(), now() + interval '90 days')
    ON CONFLICT (tenant_id, operation, idempotency_key) DO NOTHING
    RETURNING id
""")
_IDEMPOTENCY_COMPLETE = text("""
    UPDATE public.mutation_idempotency
    SET response_status = 200,
        response_payload = CAST(:payload AS JSONB)
    WHERE tenant_id = :tenant_id
      AND operation = :operation
      AND idempotency_key = :key
""")
_EMERGENCY_LOCK = text(
    "SELECT pg_advisory_xact_lock(hashtextextended(:medication_code, 0))"
)


class MedicationCatalogApplicationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DraftMedicationEntryInput:
    medication_code: str
    code_system: str
    code_system_version: str
    canonical_generic_name: str
    medication_display: str
    ingredient_identity: str
    dose_form: str | None
    identity_strength_descriptor: str | None
    identity_granularity_sufficient: bool
    terminology_status: TerminologyConceptStatus
    drug_schedule_class: DrugScheduleClass
    ndps_class: NdpsClass
    telemedicine_class: TelemedicineClass
    special_recordkeeping_class: SpecialRecordkeepingClass
    nexa_high_risk_class: NexaHighRiskClass
    regulatory_product_status: RegulatoryProductStatus
    classification_rationale_code: str


@dataclass(frozen=True, slots=True)
class DraftMedicationEvidenceInput:
    finding_dimension: MedicationEvidenceDimension
    source_authority: MedicationEvidenceAuthority
    source_document_version: str
    source_reference: str
    publication_date: date | None
    effective_date: date | None
    checked_at: datetime
    finding_value: str
    rationale_code: str
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class MedicationCatalogMutationResult:
    resource_id: UUID
    status: str
    idempotent_replay: bool


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MedicationCatalogApplicationError("INVALID_REQUEST")
    return value.astimezone(timezone.utc)


def _clean(value: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise MedicationCatalogApplicationError("INVALID_REQUEST")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        raise MedicationCatalogApplicationError("INVALID_REQUEST")
    return cleaned


def _enum(value: object, enum_type: type[Enum]) -> Enum:
    try:
        return value if isinstance(value, enum_type) else enum_type(str(value))
    except (TypeError, ValueError) as exc:
        raise MedicationCatalogApplicationError("INVALID_REQUEST") from exc


def _request_hash(payload: dict[str, object]) -> str:
    return sha256_hex(canonicalize_json(payload))


def _evidence_payload(item: DraftMedicationEvidenceInput) -> dict[str, object]:
    checked = _aware_utc(item.checked_at)
    return {
        "finding_dimension": item.finding_dimension.value,
        "source_authority": item.source_authority.value,
        "source_document_version": item.source_document_version,
        "source_reference": item.source_reference,
        "publication_date": (
            None if item.publication_date is None else item.publication_date.isoformat()
        ),
        "effective_date": (
            None if item.effective_date is None else item.effective_date.isoformat()
        ),
        "checked_at": checked.isoformat(),
        "finding_value": item.finding_value,
        "rationale_code": item.rationale_code,
        "evidence_sha256": item.evidence_sha256,
    }


class MedicationCatalogApplicationService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        signing_provider: MedicationCatalogSigningProvider | None = None,
    ) -> None:
        self.db = db
        self._signer = signing_provider
        self._authorization = ProviderTrustAuthorizationService()

    async def _authorize_actor(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        moment: datetime,
    ) -> None:
        actor = (
            await self.db.execute(
                select(ProviderIdentity)
                .options(selectinload(ProviderIdentity.credential))
                .where(ProviderIdentity.id == actor_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        await self.db.execute(
            select(ProviderCredential)
            .where(ProviderCredential.provider_id == actor_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        grants = list(
            (
                await self.db.execute(
                    select(ProviderTrustPermissionGrant)
                    .where(ProviderTrustPermissionGrant.provider_id == actor_id)
                    .order_by(ProviderTrustPermissionGrant.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )

        denial = self._authorization._strong_auth(actor, authentication, moment)
        if denial is not None:
            raise MedicationCatalogApplicationError("AUTHORIZATION_DENIED")
        mfa_at = authentication.mfa_verified_at
        if (
            mfa_at is None
            or mfa_at.tzinfo is None
            or mfa_at.utcoffset() is None
            or mfa_at > moment
        ):
            raise MedicationCatalogApplicationError("AUTHORIZATION_DENIED")
        if moment - mfa_at.astimezone(timezone.utc) > _MFA_FRESHNESS_WINDOW:
            raise MedicationCatalogApplicationError("MFA_STEP_UP_REQUIRED")
        decision = self._authorization._matching_grant(
            grants,
            TrustManagementPermission.MEDICATION_CATALOG_RELEASE_REVIEW,
            None,
            moment,
        )
        if (
            not decision.allowed
            or decision.scope is not TrustPermissionScope.GLOBAL
        ):
            raise MedicationCatalogApplicationError("AUTHORIZATION_DENIED")

    async def _begin_idempotent(
        self,
        *,
        actor_id: UUID,
        operation: str,
        key: str,
        resource_id: str,
        payload: dict[str, object],
    ) -> dict[str, Any] | None:
        try:
            clean_key = validate_idempotency_key(key)
        except ValueError as exc:
            raise MedicationCatalogApplicationError("INVALID_REQUEST") from exc
        digest = _request_hash(payload)
        params = {
            "tenant_id": _IDEMPOTENCY_TENANT,
            "actor_id": str(actor_id),
            "operation": operation,
            "resource_id": resource_id,
            "key": clean_key,
            "request_hash": digest,
        }
        existing = (
            await self.db.execute(_IDEMPOTENCY_SELECT, params)
        ).mappings().first()
        if existing is not None:
            if existing["request_hash"] != digest:
                raise MedicationCatalogApplicationError("IDEMPOTENCY_CONFLICT")
            if existing["response_status"] == 200:
                value = existing["response_payload"]
                return value if isinstance(value, dict) else json.loads(value)
            raise MedicationCatalogApplicationError("IDEMPOTENCY_IN_PROGRESS")

        reserved = await self.db.scalar(_IDEMPOTENCY_RESERVE, params)
        if reserved is None:
            existing = (
                await self.db.execute(_IDEMPOTENCY_SELECT, params)
            ).mappings().first()
            if existing is None or existing["request_hash"] != digest:
                raise MedicationCatalogApplicationError("IDEMPOTENCY_CONFLICT")
            if existing["response_status"] == 200:
                value = existing["response_payload"]
                return value if isinstance(value, dict) else json.loads(value)
            raise MedicationCatalogApplicationError("IDEMPOTENCY_IN_PROGRESS")
        return None

    async def _complete_idempotent(
        self,
        *,
        operation: str,
        key: str,
        response: dict[str, object],
    ) -> None:
        await self.db.execute(
            _IDEMPOTENCY_COMPLETE,
            {
                "tenant_id": _IDEMPOTENCY_TENANT,
                "operation": operation,
                "key": validate_idempotency_key(key),
                "payload": json.dumps(
                    response,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        )

    async def _audit(
        self,
        *,
        actor_id: UUID,
        event: ProviderTrustAuditEvent,
        target_id: UUID,
        suffix: str,
        metadata: dict[str, object],
    ) -> None:
        audit_key_material = (
            f"{event.value}|{suffix}|{target_id}"
        ).encode("utf-8")
        await enqueue_audit_event(
            self.db,
            audit_context=AuditContext.platform(domain=AuditDomain.PLATFORM),
            idempotency_key=(
                "medication-catalog-audit:"
                + sha256_hex(audit_key_material)
            ),
            actor_id=str(actor_id),
            event_type=event.value,
            target_id=str(target_id),
            patient_id=None,
            metadata=metadata,
        )

    async def create_release(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        version: str,
        source_cutoff_at: datetime,
        source_terminology_version: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> MedicationCatalogMutationResult:
        moment = _aware_utc(now or datetime.now(timezone.utc))
        version = _clean(version, maximum=64)
        source_term = _clean(source_terminology_version, maximum=128)
        source_cutoff = _aware_utc(source_cutoff_at)
        payload = {
            "version": version,
            "source_cutoff_at": source_cutoff.isoformat(),
            "source_terminology_version": source_term,
            "policy_version": MEDICATION_CATALOG_POLICY_VERSION,
        }
        async with self.db.begin():
            await self._authorize_actor(
                actor_id=actor_id,
                authentication=authentication,
                moment=moment,
            )
            replay = await self._begin_idempotent(
                actor_id=actor_id,
                operation=_OPERATION_CREATE,
                key=idempotency_key,
                resource_id=version,
                payload=payload,
            )
            if replay is not None:
                return MedicationCatalogMutationResult(
                    resource_id=UUID(str(replay["release_id"])),
                    status=str(replay["status"]),
                    idempotent_replay=True,
                )
            existing = await self.db.scalar(
                select(MedicationCatalogRelease).where(
                    MedicationCatalogRelease.version == version
                )
            )
            if existing is not None:
                raise MedicationCatalogApplicationError("RELEASE_VERSION_EXISTS")
            release = MedicationCatalogRelease(
                id=uuid4(),
                version=version,
                status=MedicationCatalogReleaseStatus.DRAFT.value,
                source_cutoff_at=source_cutoff,
                policy_version=MEDICATION_CATALOG_POLICY_VERSION,
                source_terminology_version=source_term,
                prepared_by=actor_id,
            )
            self.db.add(release)
            await self.db.flush()
            response = {"release_id": str(release.id), "status": release.status}
            await self._audit(
                actor_id=actor_id,
                event=ProviderTrustAuditEvent.MEDICATION_CATALOG_RELEASE_CREATED,
                target_id=release.id,
                suffix=f"release-created:{idempotency_key}",
                metadata={
                    "release_version": release.version,
                    "policy_version": release.policy_version,
                    "source_cutoff": release.source_cutoff_at.isoformat(),
                },
            )
            await self._complete_idempotent(
                operation=_OPERATION_CREATE,
                key=idempotency_key,
                response=response,
            )
        return MedicationCatalogMutationResult(
            resource_id=release.id,
            status=release.status,
            idempotent_replay=False,
        )

    async def upsert_draft_entry(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        release_id: UUID,
        entry_input: DraftMedicationEntryInput,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> MedicationCatalogMutationResult:
        moment = _aware_utc(now or datetime.now(timezone.utc))
        data = self._normalize_entry_input(entry_input)
        payload = {"release_id": str(release_id), **data}
        async with self.db.begin():
            await self._authorize_actor(
                actor_id=actor_id,
                authentication=authentication,
                moment=moment,
            )
            release = await self._lock_draft_release(release_id)
            replay = await self._begin_idempotent(
                actor_id=actor_id,
                operation=_OPERATION_ENTRY,
                key=idempotency_key,
                resource_id=str(release_id),
                payload=payload,
            )
            if replay is not None:
                return MedicationCatalogMutationResult(
                    resource_id=UUID(str(replay["entry_id"])),
                    status="DRAFT",
                    idempotent_replay=True,
                )
            entries = list(
                (
                    await self.db.execute(
                        select(MedicationCatalogEntry)
                        .where(MedicationCatalogEntry.release_id == release.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            entry = next(
                (
                    item
                    for item in entries
                    if item.medication_code == data["medication_code"]
                ),
                None,
            )
            if entry is None:
                if len(entries) >= _MAX_RELEASE_ENTRIES:
                    raise MedicationCatalogApplicationError("RELEASE_ENTRY_LIMIT")
                entry = MedicationCatalogEntry(
                    id=uuid4(),
                    release_id=release.id,
                    **data,
                )
                self.db.add(entry)
            else:
                for name, value in data.items():
                    setattr(entry, name, value)
                self._invalidate_entry_reviews(entry)
            await self.db.flush()
            response = {"entry_id": str(entry.id), "status": "DRAFT"}
            await self._complete_idempotent(
                operation=_OPERATION_ENTRY,
                key=idempotency_key,
                response=response,
            )
        return MedicationCatalogMutationResult(
            resource_id=entry.id,
            status="DRAFT",
            idempotent_replay=False,
        )

    def _normalize_entry_input(
        self,
        value: DraftMedicationEntryInput,
    ) -> dict[str, object]:
        if not isinstance(value, DraftMedicationEntryInput):
            raise MedicationCatalogApplicationError("INVALID_REQUEST")
        if not isinstance(value.identity_granularity_sufficient, bool):
            raise MedicationCatalogApplicationError("INVALID_REQUEST")
        return {
            "medication_code": _clean(value.medication_code, maximum=64),
            "code_system": _clean(value.code_system, maximum=64),
            "code_system_version": _clean(value.code_system_version, maximum=128),
            "canonical_generic_name": _clean(
                value.canonical_generic_name, maximum=255
            ),
            "medication_display": _clean(value.medication_display, maximum=255),
            "ingredient_identity": _clean(value.ingredient_identity, maximum=512),
            "dose_form": (
                None if value.dose_form is None else _clean(value.dose_form, maximum=128)
            ),
            "identity_strength_descriptor": (
                None
                if value.identity_strength_descriptor is None
                else _clean(value.identity_strength_descriptor, maximum=128)
            ),
            "identity_granularity_sufficient": value.identity_granularity_sufficient,
            "terminology_status": _enum(
                value.terminology_status, TerminologyConceptStatus
            ).value,
            "drug_schedule_class": _enum(
                value.drug_schedule_class, DrugScheduleClass
            ).value,
            "ndps_class": _enum(value.ndps_class, NdpsClass).value,
            "telemedicine_class": _enum(
                value.telemedicine_class, TelemedicineClass
            ).value,
            "special_recordkeeping_class": _enum(
                value.special_recordkeeping_class, SpecialRecordkeepingClass
            ).value,
            "nexa_high_risk_class": _enum(
                value.nexa_high_risk_class, NexaHighRiskClass
            ).value,
            "regulatory_product_status": _enum(
                value.regulatory_product_status, RegulatoryProductStatus
            ).value,
            "classification_rationale_code": _clean(
                value.classification_rationale_code,
                maximum=64,
            ),
            "v1_universal_allowed": False,
            "entry_integrity_digest": None,
            "first_reviewer_provider_id": None,
            "second_reviewer_provider_id": None,
            "first_review_digest": None,
            "second_review_digest": None,
            "first_reviewed_at": None,
            "second_reviewed_at": None,
        }

    @staticmethod
    def _invalidate_entry_reviews(entry: MedicationCatalogEntry) -> None:
        entry.v1_universal_allowed = False
        entry.entry_integrity_digest = None
        entry.first_reviewer_provider_id = None
        entry.second_reviewer_provider_id = None
        entry.first_review_digest = None
        entry.second_review_digest = None
        entry.first_reviewed_at = None
        entry.second_reviewed_at = None

    async def replace_draft_evidence(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        release_id: UUID,
        entry_id: UUID,
        evidence: tuple[DraftMedicationEvidenceInput, ...],
        idempotency_key: str,
        now: datetime | None = None,
    ) -> MedicationCatalogMutationResult:
        moment = _aware_utc(now or datetime.now(timezone.utc))
        if not evidence or len(evidence) > _MAX_EVIDENCE_PER_ENTRY:
            raise MedicationCatalogApplicationError("INVALID_REQUEST")
        normalized = tuple(self._normalize_evidence(item) for item in evidence)
        payload = {
            "release_id": str(release_id),
            "entry_id": str(entry_id),
            "evidence": [item["request_payload"] for item in normalized],
        }
        async with self.db.begin():
            await self._authorize_actor(
                actor_id=actor_id,
                authentication=authentication,
                moment=moment,
            )
            await self._lock_draft_release(release_id)
            entry = await self._lock_entry(release_id, entry_id)
            replay = await self._begin_idempotent(
                actor_id=actor_id,
                operation=_OPERATION_EVIDENCE,
                key=idempotency_key,
                resource_id=str(entry_id),
                payload=payload,
            )
            if replay is not None:
                return MedicationCatalogMutationResult(
                    resource_id=entry_id,
                    status="DRAFT",
                    idempotent_replay=True,
                )
            await self.db.execute(
                delete(MedicationCatalogEvidence).where(
                    MedicationCatalogEvidence.release_id == release_id,
                    MedicationCatalogEvidence.entry_id == entry_id,
                )
            )
            for item in normalized:
                self.db.add(
                    MedicationCatalogEvidence(
                        id=uuid4(),
                        release_id=release_id,
                        entry_id=entry_id,
                        prepared_by=actor_id,
                        **item["model_payload"],
                    )
                )
            self._invalidate_entry_reviews(entry)
            await self.db.flush()
            response = {"entry_id": str(entry.id), "status": "DRAFT"}
            await self._complete_idempotent(
                operation=_OPERATION_EVIDENCE,
                key=idempotency_key,
                response=response,
            )
        return MedicationCatalogMutationResult(
            resource_id=entry.id,
            status="DRAFT",
            idempotent_replay=False,
        )

    def _normalize_evidence(
        self,
        item: DraftMedicationEvidenceInput,
    ) -> dict[str, dict[str, object]]:
        if not isinstance(item, DraftMedicationEvidenceInput):
            raise MedicationCatalogApplicationError("INVALID_REQUEST")
        checked_at = _aware_utc(item.checked_at)
        sha = _clean(item.evidence_sha256, maximum=64)
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise MedicationCatalogApplicationError("INVALID_REQUEST")
        model_payload: dict[str, object] = {
            "finding_dimension": _enum(
                item.finding_dimension, MedicationEvidenceDimension
            ).value,
            "source_authority": _enum(
                item.source_authority, MedicationEvidenceAuthority
            ).value,
            "source_document_version": _clean(
                item.source_document_version, maximum=128
            ),
            "source_reference": _clean(item.source_reference, maximum=255),
            "publication_date": item.publication_date,
            "effective_date": item.effective_date,
            "checked_at": checked_at,
            "finding_value": _clean(item.finding_value, maximum=128),
            "rationale_code": _clean(item.rationale_code, maximum=64),
            "evidence_sha256": sha,
        }
        request_payload = _evidence_payload(
            DraftMedicationEvidenceInput(
                finding_dimension=MedicationEvidenceDimension(
                    str(model_payload["finding_dimension"])
                ),
                source_authority=MedicationEvidenceAuthority(
                    str(model_payload["source_authority"])
                ),
                source_document_version=str(model_payload["source_document_version"]),
                source_reference=str(model_payload["source_reference"]),
                publication_date=item.publication_date,
                effective_date=item.effective_date,
                checked_at=checked_at,
                finding_value=str(model_payload["finding_value"]),
                rationale_code=str(model_payload["rationale_code"]),
                evidence_sha256=sha,
            )
        )
        return {"model_payload": model_payload, "request_payload": request_payload}

    async def review_positive_entry(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        release_id: UUID,
        entry_id: UUID,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> MedicationCatalogMutationResult:
        moment = _aware_utc(now or datetime.now(timezone.utc))
        payload = {"release_id": str(release_id), "entry_id": str(entry_id)}
        async with self.db.begin():
            await self._authorize_actor(
                actor_id=actor_id,
                authentication=authentication,
                moment=moment,
            )
            release = await self._lock_draft_release(release_id)
            if release.prepared_by == actor_id:
                raise MedicationCatalogApplicationError("SELF_REVIEW_PROHIBITED")
            entry = await self._lock_entry(release_id, entry_id)
            evidence = await self._entry_evidence(entry_id, lock=True)
            digest = candidate_entry_digest(
                entry,
                evidence,
                policy_version=release.policy_version,
            )
            payload["candidate_digest"] = digest
            replay = await self._begin_idempotent(
                actor_id=actor_id,
                operation=_OPERATION_REVIEW,
                key=idempotency_key,
                resource_id=str(entry_id),
                payload=payload,
            )
            if replay is not None:
                return MedicationCatalogMutationResult(
                    resource_id=entry_id,
                    status="REVIEWED",
                    idempotent_replay=True,
                )
            if entry.first_reviewer_provider_id is None:
                entry.first_reviewer_provider_id = actor_id
                entry.first_review_digest = digest
                entry.first_reviewed_at = moment
            elif entry.first_reviewer_provider_id == actor_id:
                if entry.first_review_digest != digest:
                    raise MedicationCatalogApplicationError("REVIEW_CONTENT_CHANGED")
                raise MedicationCatalogApplicationError("REVIEWER_ALREADY_USED")
            elif entry.second_reviewer_provider_id is None:
                entry.second_reviewer_provider_id = actor_id
                entry.second_review_digest = digest
                entry.second_reviewed_at = moment
            elif entry.second_reviewer_provider_id == actor_id:
                if entry.second_review_digest != digest:
                    raise MedicationCatalogApplicationError("REVIEW_CONTENT_CHANGED")
                raise MedicationCatalogApplicationError("REVIEWER_ALREADY_USED")
            else:
                raise MedicationCatalogApplicationError("REVIEW_SLOTS_COMPLETE")
            await self.db.flush()
            response = {"entry_id": str(entry.id), "status": "REVIEWED"}
            await self._audit(
                actor_id=actor_id,
                event=ProviderTrustAuditEvent.MEDICATION_CATALOG_ENTRY_REVIEWED,
                target_id=entry.id,
                suffix=f"entry-reviewed:{idempotency_key}",
                metadata={
                    "medication_code": entry.medication_code,
                    "candidate_digest": digest,
                    "release_version": release.version,
                },
            )
            await self._complete_idempotent(
                operation=_OPERATION_REVIEW,
                key=idempotency_key,
                response=response,
            )
        return MedicationCatalogMutationResult(
            resource_id=entry.id,
            status="REVIEWED",
            idempotent_replay=False,
        )

    async def qualify_release(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        release_id: UUID,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> MedicationCatalogMutationResult:
        moment = _aware_utc(now or datetime.now(timezone.utc))
        async with self.db.begin():
            await self._authorize_actor(
                actor_id=actor_id,
                authentication=authentication,
                moment=moment,
            )
            release = await self._lock_draft_release(release_id)
            if release.prepared_by == actor_id:
                raise MedicationCatalogApplicationError("SELF_QUALIFY_PROHIBITED")
            replay = await self._begin_idempotent(
                actor_id=actor_id,
                operation=_OPERATION_QUALIFY,
                key=idempotency_key,
                resource_id=str(release_id),
                payload={"release_id": str(release_id)},
            )
            if replay is not None:
                return MedicationCatalogMutationResult(
                    resource_id=release_id,
                    status=str(replay["status"]),
                    idempotent_replay=True,
                )
            entries = await self._release_entries(release_id, lock=True)
            if not entries:
                raise MedicationCatalogApplicationError("RELEASE_EMPTY")
            evidence_by_entry = await self._release_evidence(entries, lock=True)
            positive_count = 0
            for entry in entries:
                rows = evidence_by_entry.get(entry.id, [])
                digest = candidate_entry_digest(
                    entry,
                    rows,
                    policy_version=release.policy_version,
                )
                facts = self._policy_facts(release, entry, rows, digest)
                supports_positive = classifications_support_v1_universal(facts)
                allowed = derive_v1_universal_allowed(facts)
                if supports_positive and not allowed:
                    raise MedicationCatalogApplicationError(
                        "POSITIVE_REVIEWS_REQUIRED"
                    )
                entry.entry_integrity_digest = digest
                entry.v1_universal_allowed = allowed
                if allowed:
                    positive_count += 1
            if positive_count > _MAX_POSITIVE_ENTRIES:
                raise MedicationCatalogApplicationError("POSITIVE_ENTRY_LIMIT")
            await self.db.flush()

            manifest_bytes = build_release_manifest_bytes(
                release,
                entries,
                evidence_by_entry,
            )
            digest_hex = sha256_hex(manifest_bytes)
            signer = self._require_signer()
            try:
                await signer.assert_ready()
                signature = await signer.sign_release_digest(
                    bytes.fromhex(digest_hex)
                )
                signature_valid = await signer.verify_release_signature(
                    bytes.fromhex(digest_hex),
                    signature,
                )
            except MedicationCatalogSigningError as exc:
                raise MedicationCatalogApplicationError(exc.code) from exc
            if not signature_valid:
                raise MedicationCatalogApplicationError(
                    "CATALOG_SIGNATURE_VERIFICATION_FAILED"
                )

            release.integrity_digest = digest_hex
            release.canonical_manifest = manifest_bytes.decode("utf-8")
            release.artifact_signature = signature_to_text(signature)
            release.artifact_key_id = signer.key_identifier
            release.signature_algorithm = signer.signature_algorithm
            release.qualified_by = actor_id
            release.qualified_at = moment
            release.status = MedicationCatalogReleaseStatus.QUALIFIED.value
            await self.db.flush()
            response = {"release_id": str(release.id), "status": release.status}
            await self._audit(
                actor_id=actor_id,
                event=ProviderTrustAuditEvent.MEDICATION_CATALOG_RELEASE_QUALIFIED,
                target_id=release.id,
                suffix=f"release-qualified:{idempotency_key}",
                metadata={
                    "release_version": release.version,
                    "policy_version": release.policy_version,
                    "entry_count": len(entries),
                    "allowed_entry_count": positive_count,
                    "release_digest": digest_hex,
                },
            )
            await self._complete_idempotent(
                operation=_OPERATION_QUALIFY,
                key=idempotency_key,
                response=response,
            )
        return MedicationCatalogMutationResult(
            resource_id=release.id,
            status=release.status,
            idempotent_replay=False,
        )

    async def activate_release(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        release_id: UUID,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> MedicationCatalogMutationResult:
        moment = _aware_utc(now or datetime.now(timezone.utc))
        async with self.db.begin():
            await self._authorize_actor(
                actor_id=actor_id,
                authentication=authentication,
                moment=moment,
            )
            release = (
                await self.db.execute(
                    select(MedicationCatalogRelease)
                    .where(MedicationCatalogRelease.id == release_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if (
                release is None
                or release.status
                != MedicationCatalogReleaseStatus.QUALIFIED.value
            ):
                raise MedicationCatalogApplicationError(
                    "RELEASE_NOT_QUALIFIED"
                )
            if release.prepared_by == actor_id:
                raise MedicationCatalogApplicationError("SELF_ACTIVATE_PROHIBITED")
            replay = await self._begin_idempotent(
                actor_id=actor_id,
                operation=_OPERATION_ACTIVATE,
                key=idempotency_key,
                resource_id=str(release_id),
                payload={"release_id": str(release_id)},
            )
            if replay is not None:
                return MedicationCatalogMutationResult(
                    resource_id=release_id,
                    status=str(replay["status"]),
                    idempotent_replay=True,
                )
            active_rows = list(
                (
                    await self.db.execute(
                        select(MedicationCatalogRelease)
                        .where(
                            MedicationCatalogRelease.status
                            == MedicationCatalogReleaseStatus.ACTIVE.value
                        )
                        .order_by(MedicationCatalogRelease.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            if len(active_rows) > 1:
                raise MedicationCatalogApplicationError(
                    "CATALOG_ACTIVE_STATE_INVALID"
                )
            await self._verify_release_projection(release)
            previous = active_rows[0] if active_rows else None
            if previous is not None:
                previous.status = MedicationCatalogReleaseStatus.SUPERSEDED.value
                previous.superseded_at = moment
                await self.db.flush()
                await self._audit(
                    actor_id=actor_id,
                    event=ProviderTrustAuditEvent.MEDICATION_CATALOG_RELEASE_SUPERSEDED,
                    target_id=previous.id,
                    suffix=f"release-superseded:{idempotency_key}",
                    metadata={"release_version": previous.version},
                )
            release.previous_release_id = None if previous is None else previous.id
            release.status = MedicationCatalogReleaseStatus.ACTIVE.value
            release.activated_by = actor_id
            release.activated_at = moment
            await self.db.flush()
            response = {"release_id": str(release.id), "status": release.status}
            await self._audit(
                actor_id=actor_id,
                event=ProviderTrustAuditEvent.MEDICATION_CATALOG_RELEASE_ACTIVATED,
                target_id=release.id,
                suffix=f"release-activated:{idempotency_key}",
                metadata={
                    "release_version": release.version,
                    "policy_version": release.policy_version,
                    "release_digest": release.integrity_digest or "",
                },
            )
            await self._complete_idempotent(
                operation=_OPERATION_ACTIVATE,
                key=idempotency_key,
                response=response,
            )
        return MedicationCatalogMutationResult(
            resource_id=release.id,
            status=release.status,
            idempotent_replay=False,
        )

    async def revoke_release(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        release_id: UUID,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> MedicationCatalogMutationResult:
        moment = _aware_utc(now or datetime.now(timezone.utc))
        async with self.db.begin():
            await self._authorize_actor(
                actor_id=actor_id,
                authentication=authentication,
                moment=moment,
            )
            release = (
                await self.db.execute(
                    select(MedicationCatalogRelease)
                    .where(MedicationCatalogRelease.id == release_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if release is None or release.status not in {
                MedicationCatalogReleaseStatus.QUALIFIED.value,
                MedicationCatalogReleaseStatus.ACTIVE.value,
            }:
                raise MedicationCatalogApplicationError("RELEASE_NOT_REVOCABLE")
            replay = await self._begin_idempotent(
                actor_id=actor_id,
                operation=_OPERATION_REVOKE,
                key=idempotency_key,
                resource_id=str(release_id),
                payload={"release_id": str(release_id)},
            )
            if replay is not None:
                return MedicationCatalogMutationResult(
                    resource_id=release_id,
                    status=str(replay["status"]),
                    idempotent_replay=True,
                )
            release.status = MedicationCatalogReleaseStatus.REVOKED.value
            release.revoked_at = moment
            await self.db.flush()
            response = {"release_id": str(release.id), "status": release.status}
            await self._audit(
                actor_id=actor_id,
                event=ProviderTrustAuditEvent.MEDICATION_CATALOG_RELEASE_REVOKED,
                target_id=release.id,
                suffix=f"release-revoked:{idempotency_key}",
                metadata={"release_version": release.version},
            )
            await self._complete_idempotent(
                operation=_OPERATION_REVOKE,
                key=idempotency_key,
                response=response,
            )
        return MedicationCatalogMutationResult(
            resource_id=release.id,
            status=release.status,
            idempotent_replay=False,
        )

    async def append_emergency_policy(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        medication_code: str,
        action: MedicationEmergencyAction,
        reason_code: MedicationEmergencyReason,
        evidence_reference: str,
        evidence_sha256: str,
        idempotency_key: str,
        effective_at: datetime | None = None,
        now: datetime | None = None,
    ) -> MedicationCatalogMutationResult:
        moment = _aware_utc(now or datetime.now(timezone.utc))
        effective = _aware_utc(effective_at or moment)
        code = _clean(medication_code, maximum=64)
        action = _enum(action, MedicationEmergencyAction)
        reason = _enum(reason_code, MedicationEmergencyReason)
        assert isinstance(action, MedicationEmergencyAction)
        assert isinstance(reason, MedicationEmergencyReason)
        reference = _clean(evidence_reference, maximum=255)
        evidence_digest = _clean(evidence_sha256, maximum=64)
        if len(evidence_digest) != 64 or any(
            char not in "0123456789abcdef" for char in evidence_digest
        ):
            raise MedicationCatalogApplicationError("INVALID_REQUEST")
        payload = {
            "medication_code": code,
            "action": action.value,
            "reason_code": reason.value,
            "evidence_reference": reference,
            "evidence_sha256": evidence_digest,
            "effective_at": effective.isoformat(),
        }
        async with self.db.begin():
            await self._authorize_actor(
                actor_id=actor_id,
                authentication=authentication,
                moment=moment,
            )
            replay = await self._begin_idempotent(
                actor_id=actor_id,
                operation=_OPERATION_EMERGENCY,
                key=idempotency_key,
                resource_id=code,
                payload=payload,
            )
            if replay is not None:
                return MedicationCatalogMutationResult(
                    resource_id=UUID(str(replay["event_id"])),
                    status=str(replay["action"]),
                    idempotent_replay=True,
                )
            await self.db.execute(_EMERGENCY_LOCK, {"medication_code": code})
            previous = (
                await self.db.execute(
                    select(MedicationCatalogEmergencyDeny)
                    .where(
                        MedicationCatalogEmergencyDeny.medication_code == code
                    )
                    .order_by(
                        MedicationCatalogEmergencyDeny.version.desc(),
                        MedicationCatalogEmergencyDeny.id.desc(),
                    )
                    .limit(1)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if action is MedicationEmergencyAction.CLEAR and (
                previous is None
                or previous.action != MedicationEmergencyAction.DENY.value
            ):
                raise MedicationCatalogApplicationError("NO_EMERGENCY_DENY_TO_CLEAR")
            event = MedicationCatalogEmergencyDeny(
                id=uuid4(),
                medication_code=code,
                version=1 if previous is None else previous.version + 1,
                action=action.value,
                reason_code=reason.value,
                evidence_reference=reference,
                evidence_sha256=evidence_digest,
                actor_provider_id=actor_id,
                effective_at=effective,
                predecessor_event_id=None if previous is None else previous.id,
            )
            self.db.add(event)
            await self.db.flush()
            response = {"event_id": str(event.id), "action": event.action}
            audit_event = (
                ProviderTrustAuditEvent.MEDICATION_CATALOG_EMERGENCY_DENY_RECORDED
                if action is MedicationEmergencyAction.DENY
                else ProviderTrustAuditEvent.MEDICATION_CATALOG_EMERGENCY_DENY_CLEARED
            )
            await self._audit(
                actor_id=actor_id,
                event=audit_event,
                target_id=event.id,
                suffix=f"emergency:{idempotency_key}",
                metadata={
                    "medication_code": code,
                    "action": event.action,
                    "reason_code": event.reason_code,
                    "version": event.version,
                },
            )
            await self._complete_idempotent(
                operation=_OPERATION_EMERGENCY,
                key=idempotency_key,
                response=response,
            )
        return MedicationCatalogMutationResult(
            resource_id=event.id,
            status=event.action,
            idempotent_replay=False,
        )

    async def _lock_draft_release(
        self,
        release_id: UUID,
    ) -> MedicationCatalogRelease:
        release = (
            await self.db.execute(
                select(MedicationCatalogRelease)
                .where(MedicationCatalogRelease.id == release_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if (
            release is None
            or release.status != MedicationCatalogReleaseStatus.DRAFT.value
        ):
            raise MedicationCatalogApplicationError("RELEASE_NOT_DRAFT")
        return release

    async def _lock_entry(
        self,
        release_id: UUID,
        entry_id: UUID,
    ) -> MedicationCatalogEntry:
        entry = (
            await self.db.execute(
                select(MedicationCatalogEntry)
                .where(
                    MedicationCatalogEntry.id == entry_id,
                    MedicationCatalogEntry.release_id == release_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if entry is None:
            raise MedicationCatalogApplicationError("ENTRY_NOT_FOUND")
        return entry

    async def _entry_evidence(
        self,
        entry_id: UUID,
        *,
        lock: bool,
    ) -> list[MedicationCatalogEvidence]:
        stmt = (
            select(MedicationCatalogEvidence)
            .where(MedicationCatalogEvidence.entry_id == entry_id)
            .order_by(
                MedicationCatalogEvidence.finding_dimension,
                MedicationCatalogEvidence.source_authority,
                MedicationCatalogEvidence.source_reference,
                MedicationCatalogEvidence.evidence_sha256,
            )
        )
        if lock:
            stmt = stmt.with_for_update()
        return list((await self.db.execute(stmt)).scalars().all())

    async def _release_entries(
        self,
        release_id: UUID,
        *,
        lock: bool,
    ) -> list[MedicationCatalogEntry]:
        stmt = (
            select(MedicationCatalogEntry)
            .where(MedicationCatalogEntry.release_id == release_id)
            .order_by(MedicationCatalogEntry.medication_code)
        )
        if lock:
            stmt = stmt.with_for_update()
        return list((await self.db.execute(stmt)).scalars().all())

    async def _release_evidence(
        self,
        entries: list[MedicationCatalogEntry],
        *,
        lock: bool,
    ) -> dict[UUID, list[MedicationCatalogEvidence]]:
        if not entries:
            return {}
        ids = [entry.id for entry in entries]
        stmt = (
            select(MedicationCatalogEvidence)
            .where(MedicationCatalogEvidence.entry_id.in_(ids))
            .order_by(
                MedicationCatalogEvidence.entry_id,
                MedicationCatalogEvidence.finding_dimension,
                MedicationCatalogEvidence.evidence_sha256,
            )
        )
        if lock:
            stmt = stmt.with_for_update()
        rows = list((await self.db.execute(stmt)).scalars().all())
        result: dict[UUID, list[MedicationCatalogEvidence]] = {
            entry.id: [] for entry in entries
        }
        for row in rows:
            result.setdefault(row.entry_id, []).append(row)
        return result

    def _policy_facts(
        self,
        release: MedicationCatalogRelease,
        entry: MedicationCatalogEntry,
        evidence: list[MedicationCatalogEvidence],
        digest: str,
    ) -> MedicationCatalogEntryPolicyFacts:
        try:
            dimensions = frozenset(
                MedicationEvidenceDimension(row.finding_dimension)
                for row in evidence
            )
            return MedicationCatalogEntryPolicyFacts(
                terminology_status=TerminologyConceptStatus(
                    entry.terminology_status
                ),
                identity_granularity_sufficient=(
                    entry.identity_granularity_sufficient
                ),
                drug_schedule_class=DrugScheduleClass(
                    entry.drug_schedule_class
                ),
                ndps_class=NdpsClass(entry.ndps_class),
                telemedicine_class=TelemedicineClass(
                    entry.telemedicine_class
                ),
                special_recordkeeping_class=SpecialRecordkeepingClass(
                    entry.special_recordkeeping_class
                ),
                nexa_high_risk_class=NexaHighRiskClass(
                    entry.nexa_high_risk_class
                ),
                regulatory_product_status=RegulatoryProductStatus(
                    entry.regulatory_product_status
                ),
                evidence_dimensions=dimensions,
                candidate_digest=digest,
                preparer_provider_id=release.prepared_by,
                first_reviewer_provider_id=entry.first_reviewer_provider_id,
                second_reviewer_provider_id=entry.second_reviewer_provider_id,
                first_review_digest=entry.first_review_digest,
                second_review_digest=entry.second_review_digest,
            )
        except ValueError as exc:
            raise MedicationCatalogApplicationError(
                "CATALOG_CLASSIFICATION_INVALID"
            ) from exc

    def _require_signer(self) -> MedicationCatalogSigningProvider:
        if self._signer is None:
            raise MedicationCatalogApplicationError(
                "CATALOG_SIGNING_PROVIDER_UNAVAILABLE"
            )
        return self._signer

    async def _verify_release_projection(
        self,
        release: MedicationCatalogRelease,
    ) -> None:
        if (
            release.policy_version != MEDICATION_CATALOG_POLICY_VERSION
            or release.signature_algorithm != CATALOG_SIGNATURE_ALGORITHM
            or not release.integrity_digest
            or not release.canonical_manifest
            or not release.artifact_signature
            or not release.artifact_key_id
        ):
            raise MedicationCatalogApplicationError("CATALOG_INTEGRITY_INVALID")
        entries = await self._release_entries(release.id, lock=True)
        evidence = await self._release_evidence(entries, lock=True)
        manifest_bytes = build_release_manifest_bytes(release, entries, evidence)
        digest = sha256_hex(manifest_bytes)
        if (
            digest != release.integrity_digest
            or manifest_bytes != release.canonical_manifest.encode("utf-8")
        ):
            raise MedicationCatalogApplicationError("CATALOG_INTEGRITY_INVALID")
        signer = self._require_signer()
        if (
            signer.key_identifier != release.artifact_key_id
            or signer.signature_algorithm != release.signature_algorithm
        ):
            raise MedicationCatalogApplicationError("CATALOG_SIGNATURE_INVALID")
        try:
            signature = signature_from_text(release.artifact_signature)
            valid = await signer.verify_release_signature(
                bytes.fromhex(digest),
                signature,
            )
        except MedicationCatalogSigningError as exc:
            raise MedicationCatalogApplicationError(exc.code) from exc
        if not valid:
            raise MedicationCatalogApplicationError(
                "CATALOG_SIGNATURE_VERIFICATION_FAILED"
            )
