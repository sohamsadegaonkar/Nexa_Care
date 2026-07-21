from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class AssuranceLevel(str, Enum):
    """Canonical assurance levels for Nexa Care V2 consent grants."""

    STANDARD = "standard"  # Password + MFA only
    PUSH_BIOMETRIC = "push_biometric"  # Real push notification + biometric approval
    BREAK_GLASS = "break_glass"  # Emergency override


@dataclass(frozen=True, slots=True)
class AssuranceResult:
    """Outcome of an assurance verification attempt."""

    verified: bool
    actual_level: AssuranceLevel
    verification_timestamp: datetime
    request_id: Optional[str] = None
