"""Asynchronous medical document AI pipeline scaffolding.

This module intentionally does not load PyTorch, Donut, or any other heavy ML
model yet. It establishes the safe execution shape: synchronous inference runs
in a worker thread, extracted fields are routed through the authoritative
PII/clinical sharding function, and temporary files are always deleted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.sharding import split_pii_and_clinical_fields

logger = logging.getLogger("nexa_logger")


def _extract_medical_document_sync(file_path: str) -> dict[str, Any]:
    """Placeholder for future synchronous ML extraction.

    The real Donut/PyTorch call belongs here later. Keeping it behind this
    synchronous boundary lets ``process_medical_document_background`` run it via
    ``asyncio.to_thread`` so model inference cannot block the FastAPI event loop.
    """

    Path(file_path).stat()
    return {}


async def process_medical_document_background(
    file_path: str,
    provider_uid: str,
    db: AsyncSession,
) -> None:
    """Process one uploaded medical document in the background.

    The whole function is wrapped in ``try/finally`` so the uploaded temporary
    file is deleted even if extraction, sharding, or future persistence fails.
    Extracted data must pass through ``split_pii_and_clinical_fields`` before
    any database insertion to preserve Nexa Care's vault/clinical separation.
    """

    try:
        extracted = await asyncio.to_thread(_extract_medical_document_sync, file_path)
        vault_payload, clinical_payload, unrecognized_payload = split_pii_and_clinical_fields(
            extracted
        )

        if unrecognized_payload:
            # Fail-safe routing: unknown model keys may be PII. The future
            # persistence layer must write this merged payload to nexa_vault,
            # never to nexa_clinical, unless a reviewer explicitly classifies it.
            vault_payload.update(unrecognized_payload)

        # Future persistence boundary, intentionally read-only for this scaffold:
        #   await write_document_projection(
        #       db=db,
        #       provider_uid=provider_uid,
        #       vault_payload=vault_payload,
        #       clinical_payload=clinical_payload,
        #   )
        # Do not insert raw, unsplit extraction output into any database table.
        _ = db

        logger.info(json.dumps({
            "event": "document_ai_pipeline_scaffold_completed",
            "provider_uid": provider_uid,
            "vault_field_count": len(vault_payload),
            "clinical_field_count": len(clinical_payload),
            "unrecognized_field_count": len(unrecognized_payload),
        }))
    except Exception as exc:
        logger.exception(json.dumps({
            "event": "document_ai_pipeline_failed",
            "provider_uid": provider_uid,
            "exception": str(exc),
        }))
        raise
    finally:
        try:
            os.remove(file_path)
            logger.info(json.dumps({
                "event": "document_temp_file_deleted",
                "provider_uid": provider_uid,
            }))
        except FileNotFoundError:
            logger.warning(json.dumps({
                "event": "document_temp_file_missing_on_cleanup",
                "provider_uid": provider_uid,
            }))
        except OSError as exc:
            logger.critical(json.dumps({
                "event": "document_temp_file_cleanup_failed",
                "provider_uid": provider_uid,
                "exception": str(exc),
            }))

