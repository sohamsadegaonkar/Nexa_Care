"""Fail-closed runtime medication-catalog resolution.

Successful resolution returns only server-owned medication identity/policy data
needed by a future Prescription mutation. No prescribing route exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_catalog import (
    MedicationCatalogEmergencyDeny,
    MedicationCatalogEntry,
    MedicationCatalogEvidence,
    MedicationCatalogRelease,
    MedicationCatalogReleaseStatus,
    MedicationEmergencyAction,
    TerminologyConceptStatus,
)
from app.services.medication_catalog_manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_release_manifest,
    canonicalize_json,
    sha256_hex,
)
from app.services.medication_catalog_policy import (
    MEDICATION_CATALOG_POLICY_VERSION,
)
from app.services.medication_catalog_signing import (
    CATALOG_SIGNATURE_ALGORITHM,
    MedicationCatalogSigningError,
    MedicationCatalogSigningProvider,
    signature_from_text,
)


class MedicationCatalogRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ResolvedV1Medication:
    release_id: UUID
    release_version: str
    policy_version: str
    medication_code: str
    medication_display: str
    code_system: str
    code_system_version: str
    canonical_generic_name: str
    dose_form: str | None
    identity_strength_descriptor: str | None


class MedicationCatalogRuntimeService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        signing_provider: MedicationCatalogSigningProvider | None,
    ) -> None:
        self.db = db
        self._signer = signing_provider

    async def resolve_v1_allowed_medication(
        self,
        medication_code: str,
        *,
        now: datetime | None = None,
    ) -> ResolvedV1Medication:
        if not isinstance(medication_code, str):
            raise MedicationCatalogRuntimeError("CATALOG_MEDICATION_DENIED")
        code = medication_code.strip()
        if not code or len(code) > 64:
            raise MedicationCatalogRuntimeError("CATALOG_MEDICATION_DENIED")
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise MedicationCatalogRuntimeError("CATALOG_UNAVAILABLE")
        moment = moment.astimezone(timezone.utc)

        try:
            active = list(
                (
                    await self.db.execute(
                        select(MedicationCatalogRelease)
                        .where(
                            MedicationCatalogRelease.status
                            == MedicationCatalogReleaseStatus.ACTIVE.value
                        )
                        .order_by(MedicationCatalogRelease.id)
                    )
                )
                .scalars()
                .all()
            )
        except Exception as exc:
            raise MedicationCatalogRuntimeError("CATALOG_UNAVAILABLE") from exc
        if len(active) != 1:
            raise MedicationCatalogRuntimeError("CATALOG_UNAVAILABLE")
        release = active[0]
        try:
            await self._verify_release(release)

            entry = (
                await self.db.execute(
                    select(MedicationCatalogEntry).where(
                        MedicationCatalogEntry.release_id == release.id,
                        MedicationCatalogEntry.medication_code == code,
                    )
                )
            ).scalar_one_or_none()
            if entry is None:
                raise MedicationCatalogRuntimeError("CATALOG_MEDICATION_DENIED")
            if (
                not entry.v1_universal_allowed
                or entry.terminology_status != TerminologyConceptStatus.ACTIVE.value
            ):
                raise MedicationCatalogRuntimeError("CATALOG_MEDICATION_DENIED")

            latest_emergency = (
                await self.db.execute(
                    select(MedicationCatalogEmergencyDeny)
                    .where(
                        MedicationCatalogEmergencyDeny.medication_code == code,
                        MedicationCatalogEmergencyDeny.effective_at <= moment,
                    )
                    .order_by(
                        MedicationCatalogEmergencyDeny.version.desc(),
                        MedicationCatalogEmergencyDeny.id.desc(),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        except MedicationCatalogRuntimeError:
            raise
        except Exception as exc:
            raise MedicationCatalogRuntimeError("CATALOG_UNAVAILABLE") from exc
        if (
            latest_emergency is not None
            and latest_emergency.action == MedicationEmergencyAction.DENY.value
        ):
            raise MedicationCatalogRuntimeError("CATALOG_EMERGENCY_DENY")

        return ResolvedV1Medication(
            release_id=release.id,
            release_version=release.version,
            policy_version=release.policy_version,
            medication_code=entry.medication_code,
            medication_display=entry.medication_display,
            code_system=entry.code_system,
            code_system_version=entry.code_system_version,
            canonical_generic_name=entry.canonical_generic_name,
            dose_form=entry.dose_form,
            identity_strength_descriptor=entry.identity_strength_descriptor,
        )

    async def _verify_release(self, release: MedicationCatalogRelease) -> None:
        if (
            release.status != MedicationCatalogReleaseStatus.ACTIVE.value
            or release.policy_version != MEDICATION_CATALOG_POLICY_VERSION
            or not release.integrity_digest
            or not release.canonical_manifest
            or not release.artifact_signature
            or not release.artifact_key_id
            or release.signature_algorithm != CATALOG_SIGNATURE_ALGORITHM
        ):
            raise MedicationCatalogRuntimeError("CATALOG_INTEGRITY_INVALID")
        signer = self._signer
        if signer is None:
            raise MedicationCatalogRuntimeError("CATALOG_UNAVAILABLE")
        if (
            signer.key_identifier != release.artifact_key_id
            or signer.signature_algorithm != release.signature_algorithm
        ):
            raise MedicationCatalogRuntimeError("CATALOG_SIGNATURE_INVALID")

        entries = list(
            (
                await self.db.execute(
                    select(MedicationCatalogEntry)
                    .where(MedicationCatalogEntry.release_id == release.id)
                    .order_by(MedicationCatalogEntry.medication_code)
                )
            )
            .scalars()
            .all()
        )
        evidence_rows = list(
            (
                await self.db.execute(
                    select(MedicationCatalogEvidence)
                    .where(MedicationCatalogEvidence.release_id == release.id)
                    .order_by(
                        MedicationCatalogEvidence.entry_id,
                        MedicationCatalogEvidence.finding_dimension,
                        MedicationCatalogEvidence.evidence_sha256,
                    )
                )
            )
            .scalars()
            .all()
        )
        evidence: dict[UUID, list[MedicationCatalogEvidence]] = {
            entry.id: [] for entry in entries
        }
        for row in evidence_rows:
            evidence.setdefault(row.entry_id, []).append(row)

        manifest = build_release_manifest(release, entries, evidence)
        if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
            raise MedicationCatalogRuntimeError("CATALOG_MANIFEST_UNSUPPORTED")
        manifest_bytes = canonicalize_json(manifest)
        digest = sha256_hex(manifest_bytes)
        if (
            digest != release.integrity_digest
            or manifest_bytes != release.canonical_manifest.encode("utf-8")
        ):
            raise MedicationCatalogRuntimeError("CATALOG_PROJECTION_MISMATCH")
        try:
            signature = signature_from_text(release.artifact_signature)
            valid = await signer.verify_release_signature(
                bytes.fromhex(digest),
                signature,
            )
        except MedicationCatalogSigningError as exc:
            raise MedicationCatalogRuntimeError("CATALOG_UNAVAILABLE") from exc
        if not valid:
            raise MedicationCatalogRuntimeError("CATALOG_SIGNATURE_INVALID")
