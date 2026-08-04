"""Deterministic semantic grouping while retaining provider provenance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.models.ai_models import ProviderFieldEvidence


@dataclass(frozen=True)
class SemanticCandidate:
    evidence: tuple[ProviderFieldEvidence, ...]

    @property
    def representative(self) -> ProviderFieldEvidence:
        item = self.evidence[0]
        hashes = tuple(
            sorted(x.evidence_hash for x in self.evidence if x.evidence_hash)
        )
        block_ids = tuple(
            sorted({v for x in self.evidence for v in x.source_block_ids})
        )
        identity = hashes or tuple(
            sorted(
                f"{x.source_type}:{','.join(x.source_block_ids)}:{x.canonical_field_name}:{x.raw_value}"
                for x in self.evidence
            )
        )
        digest = hashlib.sha256(
            json.dumps(identity, separators=(",", ":")).encode()
        ).hexdigest()
        return item.model_copy(
            update={
                "evidence_hash": digest,
                "source_block_ids": block_ids,
                "supporting_evidence_hashes": hashes,
                "supporting_source_block_ids": block_ids,
            }
        )


def _overlaps(a: ProviderFieldEvidence, b: ProviderFieldEvidence) -> bool:
    if a.bounding_box is None or b.bounding_box is None:
        return False
    left = max(a.bounding_box.left, b.bounding_box.left)
    top = max(a.bounding_box.top, b.bounding_box.top)
    right = min(a.bounding_box.right, b.bounding_box.right)
    bottom = min(a.bounding_box.bottom, b.bounding_box.bottom)
    return right > left and bottom > top


def _same_occurrence(a: ProviderFieldEvidence, b: ProviderFieldEvidence) -> bool:
    if (
        a.canonical_field_name != b.canonical_field_name
        or a.raw_value != b.raw_value
        or a.normalized_value != b.normalized_value
        or a.normalized_unit != b.normalized_unit
        or a.page_number != b.page_number
    ):
        return False
    shared_blocks = bool(set(a.source_block_ids) & set(b.source_block_ids))
    same_table_row = bool(
        a.source_type == b.source_type == "CELL"
        and a.structured_value
        and a.structured_value == b.structured_value
        and _overlaps(a, b)
    )
    return shared_blocks or _overlaps(a, b) or same_table_row


def group_semantic_candidates(
    evidence: list[ProviderFieldEvidence],
) -> list[SemanticCandidate]:
    """Group only location-linked duplicates; equal values elsewhere stay distinct."""
    groups: list[list[ProviderFieldEvidence]] = []
    for item in evidence:
        matching = [
            index
            for index, group in enumerate(groups)
            if any(_same_occurrence(item, x) for x in group)
        ]
        if not matching:
            groups.append([item])
            continue
        target = matching[0]
        groups[target].append(item)
        for index in reversed(matching[1:]):
            groups[target].extend(groups.pop(index))
    return [SemanticCandidate(tuple(group)) for group in groups]
