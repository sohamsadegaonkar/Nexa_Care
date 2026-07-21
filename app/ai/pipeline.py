"""Legacy document pipeline compatibility boundary.

The former path accepted an unbound file and could write OCR output directly
to authoritative shards. It is intentionally disabled; callers must use the
patient-bound staged pipeline under /api/v2/pipeline/documents/upload.
"""

from __future__ import annotations


class LegacyDocumentPipelineDisabled(RuntimeError):
    pass


async def process_medical_document_background(*args, **kwargs) -> None:
    _ = (args, kwargs)
    raise LegacyDocumentPipelineDisabled(
        "Unbound document processing is disabled; use the patient-bound staged pipeline"
    )
