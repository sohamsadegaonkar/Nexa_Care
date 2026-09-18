"""Bounded Treatment Session V1 WRITE_VITALS mutation staging.

This module does not expose an HTTP route and does not commit transactions.
Callers must arrive with an exact WRITE_VITALS TreatmentSessionV1Authority,
perform the final current-provider trust check, and commit only after this
service has staged the clinical row, timeline event, audit outbox event, and
durable idempotency completion in one transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clinical_session_gate import (
    TreatmentSessionV1Authority,
    TreatmentSessionV1GateDenied,
    lock_treatment_write_authority,
)
from app.models.patient_records import TimelineEvent, Vitals
from app.security.audit_context import AuditContext, AuditDomain
from app.security.clinical_access_policy import ClinicalAccessOperation
from app.services.audit_outbox import enqueue_audit_event

_OPERATION = "treatment.write_vitals.v1"
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")

_IDEMPOTENCY_SELECT = text(
    """
    SELECT request_hash, response_status, response_payload
    FROM public.mutation_idempotency
    WHERE tenant_id = :tenant_id
      AND operation = :operation
      AND idempotency_key = :idempotency_key
    """
)
_IDEMPOTENCY_RESERVE = text(
    """
    INSERT INTO public.mutation_idempotency
        (tenant_id, actor_id, operation, resource_id, idempotency_key,
         request_hash, created_at, retention_expires_at)
    VALUES
        (:tenant_id, :actor_id, :operation, :resource_id, :idempotency_key,
         :request_hash, now(), now() + interval '90 days')
    ON CONFLICT (tenant_id, operation, idempotency_key) DO NOTHING
    RETURNING id
    """
)
_IDEMPOTENCY_COMPLETE = text(
    """
    UPDATE public.mutation_idempotency
    SET response_status = 200,
        response_payload = CAST(:response_payload AS JSONB)
    WHERE tenant_id = :tenant_id
      AND operation = :operation
      AND idempotency_key = :idempotency_key
    """
)


class TreatmentVitalType(str, Enum):
    BLOOD_PRESSURE = "BP"
    HEART_RATE = "HR"
    TEMPERATURE = "temp"
    SPO2 = "SpO2"


@dataclass(frozen=True, slots=True)
class TreatmentVitalObservation:
    vital_type: TreatmentVitalType
    value: str
    unit: str
    recorded_at: datetime

    @property
    def timeline_summary(self) -> str:
        labels = {
            TreatmentVitalType.BLOOD_PRESSURE: "BP",
            TreatmentVitalType.HEART_RATE: "HR",
            TreatmentVitalType.TEMPERATURE: "Temperature",
            TreatmentVitalType.SPO2: "SpO2",
        }
        return f"Vitals recorded: {labels[self.vital_type]} {self.value} {self.unit}"


@dataclass(frozen=True, slots=True)
class TreatmentVitalWriteResult:
    record_id: uuid.UUID
    encounter_id: uuid.UUID
    vital_type: TreatmentVitalType
    recorded_at: datetime
    idempotent_replay: bool


class TreatmentVitalError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TreatmentVitalValidationError(TreatmentVitalError):
    pass


class TreatmentVitalIdempotencyConflict(TreatmentVitalError):
    pass


class TreatmentVitalUnavailable(TreatmentVitalError):
    pass


def _aware_recorded_at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TreatmentVitalValidationError("TREATMENT_VITAL_RECORDED_AT_INVALID")
    return value


def _bounded_positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 999:
        raise TreatmentVitalValidationError(f"TREATMENT_VITAL_{field}_INVALID")
    return value


def _finite_decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TreatmentVitalValidationError(
            f"TREATMENT_VITAL_{field}_INVALID"
        ) from exc
    if not parsed.is_finite() or abs(parsed) >= Decimal("1000"):
        raise TreatmentVitalValidationError(f"TREATMENT_VITAL_{field}_INVALID")
    return parsed


def _decimal_text(value: Decimal) -> str:
    text_value = format(value.normalize(), "f")
    if "." in text_value:
        text_value = text_value.rstrip("0").rstrip(".")
    return "0" if text_value in {"-0", ""} else text_value


def blood_pressure_observation(
    *,
    systolic_bp: int,
    diastolic_bp: int,
    recorded_at: datetime,
) -> TreatmentVitalObservation:
    systolic = _bounded_positive_int(systolic_bp, field="SYSTOLIC_BP")
    diastolic = _bounded_positive_int(diastolic_bp, field="DIASTOLIC_BP")
    return TreatmentVitalObservation(
        vital_type=TreatmentVitalType.BLOOD_PRESSURE,
        value=f"{systolic}/{diastolic}",
        unit="mmHg",
        recorded_at=_aware_recorded_at(recorded_at),
    )


def heart_rate_observation(
    *,
    beats_per_minute: int,
    recorded_at: datetime,
) -> TreatmentVitalObservation:
    heart_rate = _bounded_positive_int(beats_per_minute, field="HEART_RATE")
    return TreatmentVitalObservation(
        vital_type=TreatmentVitalType.HEART_RATE,
        value=str(heart_rate),
        unit="bpm",
        recorded_at=_aware_recorded_at(recorded_at),
    )


def temperature_observation(
    *,
    celsius: object,
    recorded_at: datetime,
) -> TreatmentVitalObservation:
    value = _finite_decimal(celsius, field="TEMPERATURE")
    return TreatmentVitalObservation(
        vital_type=TreatmentVitalType.TEMPERATURE,
        value=_decimal_text(value),
        unit="C",
        recorded_at=_aware_recorded_at(recorded_at),
    )


def spo2_observation(
    *,
    percentage: object,
    recorded_at: datetime,
) -> TreatmentVitalObservation:
    value = _finite_decimal(percentage, field="SPO2")
    if value < 0 or value > 100:
        raise TreatmentVitalValidationError("TREATMENT_VITAL_SPO2_INVALID")
    return TreatmentVitalObservation(
        vital_type=TreatmentVitalType.SPO2,
        value=_decimal_text(value),
        unit="%",
        recorded_at=_aware_recorded_at(recorded_at),
    )


def _validate_normalized_observation(
    observation: TreatmentVitalObservation,
) -> None:
    if not isinstance(observation.vital_type, TreatmentVitalType):
        raise TreatmentVitalValidationError("TREATMENT_VITAL_OBSERVATION_INVALID")
    _aware_recorded_at(observation.recorded_at)

    expected_units = {
        TreatmentVitalType.BLOOD_PRESSURE: "mmHg",
        TreatmentVitalType.HEART_RATE: "bpm",
        TreatmentVitalType.TEMPERATURE: "C",
        TreatmentVitalType.SPO2: "%",
    }
    if observation.unit != expected_units[observation.vital_type]:
        raise TreatmentVitalValidationError("TREATMENT_VITAL_UNIT_INVALID")

    if observation.vital_type is TreatmentVitalType.BLOOD_PRESSURE:
        match = re.fullmatch(r"(\d{1,3})/(\d{1,3})", observation.value)
        if match is None:
            raise TreatmentVitalValidationError("TREATMENT_VITAL_BP_INVALID")
        systolic = _bounded_positive_int(int(match.group(1)), field="SYSTOLIC_BP")
        diastolic = _bounded_positive_int(int(match.group(2)), field="DIASTOLIC_BP")
        if observation.value != f"{systolic}/{diastolic}":
            raise TreatmentVitalValidationError("TREATMENT_VITAL_VALUE_NOT_CANONICAL")
        return

    if observation.vital_type is TreatmentVitalType.HEART_RATE:
        if re.fullmatch(r"\d{1,3}", observation.value) is None:
            raise TreatmentVitalValidationError("TREATMENT_VITAL_HEART_RATE_INVALID")
        heart_rate = _bounded_positive_int(
            int(observation.value), field="HEART_RATE"
        )
        if observation.value != str(heart_rate):
            raise TreatmentVitalValidationError("TREATMENT_VITAL_VALUE_NOT_CANONICAL")
        return

    parsed = _finite_decimal(
        observation.value,
        field=(
            "TEMPERATURE"
            if observation.vital_type is TreatmentVitalType.TEMPERATURE
            else "SPO2"
        ),
    )
    if observation.value != _decimal_text(parsed):
        raise TreatmentVitalValidationError("TREATMENT_VITAL_VALUE_NOT_CANONICAL")
    if observation.vital_type is TreatmentVitalType.SPO2 and (
        parsed < 0 or parsed > 100
    ):
        raise TreatmentVitalValidationError("TREATMENT_VITAL_SPO2_INVALID")


def validate_treatment_vitals_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise TreatmentVitalValidationError("TREATMENT_VITAL_IDEMPOTENCY_KEY_INVALID")
    return value


def _canonical_request_hash(
    *,
    authority: TreatmentSessionV1Authority,
    observation: TreatmentVitalObservation,
) -> str:
    canonical = json.dumps(
        {
            "clinical_session_id": str(authority.session_id),
            "encounter_id": str(authority.encounter_id),
            "hospital_id": str(authority.hospital_id),
            "operation": _OPERATION,
            "patient_id": str(authority.patient_id),
            "provider_id": str(authority.provider_id),
            "recorded_at": observation.recorded_at.isoformat(),
            "unit": observation.unit,
            "value": observation.value,
            "vital_type": observation.vital_type.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _audit_idempotency_key(
    *,
    hospital_id: uuid.UUID,
    idempotency_key: str,
) -> str:
    digest = hashlib.sha256(
        f"{hospital_id}:{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"treatment-vitals:{digest}"


def _validate_audit_context(
    *,
    audit_context: AuditContext,
    authority: TreatmentSessionV1Authority,
) -> None:
    if (
        audit_context.domain is not AuditDomain.PATIENT_RECORD
        or audit_context.tenant_id is not None
        or audit_context.hospital_id != str(authority.hospital_id)
    ):
        raise TreatmentSessionV1GateDenied("TREATMENT_AUDIT_CONTEXT_MISMATCH")


def _replay_result(
    row: object,
    *,
    request_hash: str,
) -> TreatmentVitalWriteResult:
    if getattr(row, "request_hash", None) != request_hash:
        raise TreatmentVitalIdempotencyConflict("TREATMENT_VITAL_IDEMPOTENCY_KEY_REUSED")
    payload = getattr(row, "response_payload", None)
    if getattr(row, "response_status", None) != 200 or not isinstance(payload, dict):
        raise TreatmentVitalUnavailable("TREATMENT_VITAL_IDEMPOTENCY_STATE_INCOMPLETE")
    try:
        return TreatmentVitalWriteResult(
            record_id=uuid.UUID(str(payload["record_id"])),
            encounter_id=uuid.UUID(str(payload["encounter_id"])),
            vital_type=TreatmentVitalType(str(payload["vital_type"])),
            recorded_at=datetime.fromisoformat(str(payload["recorded_at"])),
            idempotent_replay=True,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TreatmentVitalUnavailable(
            "TREATMENT_VITAL_IDEMPOTENCY_STATE_INVALID"
        ) from exc


async def stage_treatment_vital_write(
    *,
    db: AsyncSession,
    authority: TreatmentSessionV1Authority,
    observation: TreatmentVitalObservation,
    idempotency_key: str,
    audit_context: AuditContext,
) -> TreatmentVitalWriteResult:
    """Stage one WRITE_VITALS mutation without committing the transaction."""

    validate_treatment_vitals_idempotency_key(idempotency_key)
    if not isinstance(observation, TreatmentVitalObservation):
        raise TreatmentVitalValidationError("TREATMENT_VITAL_OBSERVATION_INVALID")
    _validate_normalized_observation(observation)
    if authority.required_operation is not ClinicalAccessOperation.WRITE_VITALS:
        raise TreatmentSessionV1GateDenied("TREATMENT_OPERATION_NOT_AUTHORIZED")
    _validate_audit_context(audit_context=audit_context, authority=authority)

    encounter = await lock_treatment_write_authority(
        db=db,
        authority=authority,
        required_operation=ClinicalAccessOperation.WRITE_VITALS,
    )
    request_hash = _canonical_request_hash(
        authority=authority,
        observation=observation,
    )
    scope = str(authority.hospital_id)
    params = {
        "tenant_id": scope,
        "operation": _OPERATION,
        "idempotency_key": idempotency_key,
    }

    try:
        existing = (await db.execute(_IDEMPOTENCY_SELECT, params)).first()
    except Exception as exc:
        raise TreatmentVitalUnavailable(
            "TREATMENT_VITAL_IDEMPOTENCY_STORE_UNAVAILABLE"
        ) from exc
    if existing is not None:
        return _replay_result(existing, request_hash=request_hash)

    try:
        reservation = await db.execute(
            _IDEMPOTENCY_RESERVE,
            {
                **params,
                "actor_id": str(authority.provider_id),
                "resource_id": str(encounter.encounter_id),
                "request_hash": request_hash,
            },
        )
        reserved = reservation.first()
    except Exception as exc:
        raise TreatmentVitalUnavailable(
            "TREATMENT_VITAL_IDEMPOTENCY_STORE_UNAVAILABLE"
        ) from exc

    if reserved is None:
        try:
            concurrent = (await db.execute(_IDEMPOTENCY_SELECT, params)).first()
        except Exception as exc:
            raise TreatmentVitalUnavailable(
                "TREATMENT_VITAL_IDEMPOTENCY_STORE_UNAVAILABLE"
            ) from exc
        if concurrent is None:
            raise TreatmentVitalUnavailable(
                "TREATMENT_VITAL_IDEMPOTENCY_STATE_INCOMPLETE"
            )
        return _replay_result(concurrent, request_hash=request_hash)

    record_id = uuid.uuid4()
    vital = Vitals(
        id=record_id,
        patient_id=authority.patient_id,
        encounter_id=encounter.encounter_id,
        type=observation.vital_type.value,
        value=observation.value,
        unit=observation.unit,
        recorded_at=observation.recorded_at,
        source="manual",
        confidence=None,
        risk_level="LOW_RISK",
        source_document_id=None,
    )
    timeline = TimelineEvent(
        patient_id=authority.patient_id,
        event_type="VITALS",
        event_ref_id=record_id,
        occurred_at=observation.recorded_at,
        source="manual",
        summary=observation.timeline_summary,
    )
    db.add(vital)
    db.add(timeline)

    try:
        await db.flush()
        await enqueue_audit_event(
            db,
            audit_context=audit_context,
            idempotency_key=_audit_idempotency_key(
                hospital_id=authority.hospital_id,
                idempotency_key=idempotency_key,
            ),
            actor_id=str(authority.provider_id),
            event_type="PATIENT_RECORD_APPEND_SUCCESS",
            target_id=str(record_id),
            patient_id=str(authority.patient_id),
            metadata={
                "clinical_session_id": str(authority.session_id),
                "encounter_id": str(encounter.encounter_id),
                "operation": ClinicalAccessOperation.WRITE_VITALS.value,
                "record_type": "vitals",
            },
        )
        safe_response = {
            "encounter_id": str(encounter.encounter_id),
            "record_id": str(record_id),
            "recorded_at": observation.recorded_at.isoformat(),
            "status": "committed",
            "vital_type": observation.vital_type.value,
        }
        await db.execute(
            _IDEMPOTENCY_COMPLETE,
            {
                **params,
                "response_payload": json.dumps(
                    safe_response,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        )
    except TreatmentVitalError:
        raise
    except Exception as exc:
        raise TreatmentVitalUnavailable(
            "TREATMENT_VITAL_STAGE_UNAVAILABLE"
        ) from exc

    return TreatmentVitalWriteResult(
        record_id=record_id,
        encounter_id=encounter.encounter_id,
        vital_type=observation.vital_type,
        recorded_at=observation.recorded_at,
        idempotent_replay=False,
    )
