"""AI Ingestion Pipeline Safety & Auto-Approval Guardrails (Workstream 4 & 5).

Thin compatibility shim that delegates to the canonical auto-approval
decision engine in ``app/ai/auto_approval.py``.  All auto-approval logic
lives in :func:`should_auto_approve`; this wrapper preserves the existing
``can_auto_approve()`` return-type contract (``bool``) used by the pipeline
orchestrator.
"""
from __future__ import annotations

from app.ai.auto_approval import should_auto_approve
from app.models.extracted_field import ExtractedField


def can_auto_approve(field: ExtractedField) -> bool:
    """Determine whether an extracted field qualifies for auto-approval.

    Delegates to :func:`should_auto_approve` (the single source of truth)
    and returns the boolean decision.
    """
    return should_auto_approve(field).auto_approve
