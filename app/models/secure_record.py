"""Secure in-memory reconstruction model for consent-scoped reads.

The merged record is intentionally not a Pydantic model and intentionally has
no public raw-data serialization path. It exists only long enough to apply the
Redis capability scope to separately fetched vault and clinical shards.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

_REDACTED_REPR = "<SecureMergedRecord: [REDACTED]>"
_PII_NAMESPACE = "pii"
_CLINICAL_NAMESPACE = "clinical"


class SecureMergedRecord:
    """Ephemeral holder for separated PII and clinical shard payloads."""

    __slots__ = ("_pii", "_clinical")

    def __init__(self, pii: Mapping[str, Any], clinical: Mapping[str, Any]) -> None:
        self._pii = dict(pii)
        self._clinical = dict(clinical)

    def __repr__(self) -> str:
        return _REDACTED_REPR

    def __str__(self) -> str:
        return _REDACTED_REPR

    def __iter__(self):
        raise TypeError("SecureMergedRecord cannot be iterated or serialized directly.")

    def model_dump(self, *args: object, **kwargs: object) -> dict[str, Any]:
        """Disable Pydantic-style serialization for PHI safety."""

        raise TypeError(
            "SecureMergedRecord.model_dump() is disabled; use to_response(scope)."
        )

    def dict(self, *args: object, **kwargs: object) -> dict[str, Any]:
        """Disable legacy Pydantic-style serialization for PHI safety."""

        raise TypeError(
            "SecureMergedRecord.dict() is disabled; use to_response(scope)."
        )

    def json(self, *args: object, **kwargs: object) -> str:
        """Disable raw JSON serialization for PHI safety."""

        raise TypeError(
            "SecureMergedRecord.json() is disabled; use to_response(scope)."
        )

    def to_response(self, scope: list[str]) -> dict[str, Any]:
        """Return only fields explicitly named by the consent capability scope.

        Scope entries may be namespaced (``pii.patient_name`` or
        ``clinical.diagnoses``) or bare field names. Bare names are resolved
        only when they appear in exactly one shard, which prevents accidental
        cross-shard ambiguity.
        """

        response: dict[str, Any] = {}
        pii_fields: dict[str, Any] = {}
        clinical_fields: dict[str, Any] = {}

        for entry in scope:
            if not isinstance(entry, str):
                continue

            field = entry.strip()
            if not field:
                continue

            namespace, separator, key = field.partition(".")
            if separator:
                if namespace == _PII_NAMESPACE and key in self._pii:
                    pii_fields[key] = deepcopy(self._pii[key])
                elif namespace == _CLINICAL_NAMESPACE and key in self._clinical:
                    clinical_fields[key] = deepcopy(self._clinical[key])
                continue

            in_pii = field in self._pii
            in_clinical = field in self._clinical
            if in_pii and not in_clinical:
                pii_fields[field] = deepcopy(self._pii[field])
            elif in_clinical and not in_pii:
                clinical_fields[field] = deepcopy(self._clinical[field])

        if pii_fields:
            response[_PII_NAMESPACE] = pii_fields
        if clinical_fields:
            response[_CLINICAL_NAMESPACE] = clinical_fields

        return response
