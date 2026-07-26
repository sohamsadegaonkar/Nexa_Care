from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.v2.consent_routes import ConsentChallengeRequestPayload


def test_document_processing_requires_documents_scope():
    payload = ConsentChallengeRequestPayload(
        patient_id="patient-1",
        purpose="document_processing",
        scope="documents",
    )
    assert payload.purpose == "document_processing"
    assert payload.scope == "documents"


@pytest.mark.parametrize(
    ("purpose", "scope"),
    [
        ("document_processing", "clinical"),
        ("document_processing", "full"),
        ("treatment", "documents"),
        ("treatment", "arbitrary"),
    ],
)
def test_incompatible_or_unknown_purpose_scope_is_rejected(purpose: str, scope: str):
    with pytest.raises(ValidationError):
        ConsentChallengeRequestPayload(
            patient_id="patient-1",
            purpose=purpose,
            scope=scope,
        )
