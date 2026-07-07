"""Legacy document processor compatibility shim.

Local Donut/PyTorch extraction has been removed for 512MB cloud deployments.
The supported path is now the remote-API V2 pipeline in ``app.ai``. This module
remains only so the older /api/v1/process-document import path does not pull
heavy ML dependencies at application startup.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def extract_document_data(file_path: str) -> dict:
    """Return no local extraction data; use /api/v2/documents/upload instead."""

    logger.warning(json.dumps({
        "event": "legacy_document_processor_disabled",
        "reason": "local_ml_removed_use_v2_remote_pipeline",
    }))
    _ = file_path
    return {}
