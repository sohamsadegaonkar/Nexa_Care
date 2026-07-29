"""AI Ingestion Pipeline Safety & Auto-Approval Guardrails (Workstream 4 & 5).

This module is not used by the runtime pipeline orchestrator. It preserves the
older boolean contract while three-lane field-evidence routing is owned by
``app.services.extraction_decision_engine``.
"""

from __future__ import annotations

from app.ai.auto_approval import should_auto_approve
from app.models.extracted_field import ExtractedField


def can_auto_approve(field: ExtractedField) -> bool:
    """Determine whether an extracted field qualifies for auto-approval.

    Delegate to the compatibility evaluator and return its boolean result.
    """
    return should_auto_approve(field).auto_approve
