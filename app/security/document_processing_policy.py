"""Trusted authorization policy for consent-bound document processing."""

from __future__ import annotations

from enum import StrEnum

DOCUMENT_PROCESSING_PURPOSE = "document_processing"
DOCUMENT_PROCESSING_SCOPE = "documents"
DOCUMENT_PROCESSING_GRANT_TYPE = "document_processing"


class DocumentProcessingOperation(StrEnum):
    UPLOAD_DOCUMENT = "upload_document"
    LIST_PROCESSING_JOBS = "list_processing_jobs"
    READ_JOB_STATUS = "read_job_status"
    READ_DOCUMENT_SOURCE = "read_document_source"
    REVIEW_EXTRACTED_FIELDS = "review_extracted_fields"
    ADJUDICATE_EXTRACTED_FIELD = "adjudicate_extracted_field"
    COMMIT_VERIFIED_FIELDS = "commit_verified_fields"


DOCUMENT_PROCESSING_OPERATIONS = frozenset(DocumentProcessingOperation)


def operations_for_grant(purpose: str, scope: str) -> tuple[str, ...]:
    """Resolve operations exclusively from trusted server policy."""
    if purpose != DOCUMENT_PROCESSING_PURPOSE or scope != DOCUMENT_PROCESSING_SCOPE:
        return ()
    return tuple(operation.value for operation in DocumentProcessingOperation)
