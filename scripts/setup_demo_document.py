"""Demo Document Setup & Pipeline Orchestrator Seeder (Days 12-13).

Sets up a compelling review-queue demo for canonical patient Aarav Sharma
(123e4567-e89b-12d3-a456-426614174001).

Runs background extraction producing a realistic mix:
  1. High-confidence safe observation (Fasting Glucose 140 mg/dL, conf=0.98) -> auto_approved
  2. Medium-confidence observation (Metformin 500mg, conf=0.92) -> needs_review
  3. Allergy sensitivity observation (Penicillin, conf=0.99) -> forced to HIGH_RISK -> needs_review
  4. Abnormal diagnostic lab (HbA1c 7.2%, conf=0.96) -> HIGH_RISK -> needs_review

Enforces hard environment guard: never executes in production.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_async_engine
from app.models.pipeline import (
    DocumentStorage,
    ExtractedFieldRecord,
    ExtractionJob,
    PipelineCommit,
    ReviewQueueItem,
)
from app.services.pipeline_orchestrator import process_extraction_job

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DEMO_PATIENT_ID = "123e4567-e89b-12d3-a456-426614174001"
DEMO_DOC_ID = "555e4567-e89b-12d3-a456-426614174555"


async def setup_demo_document(session: AsyncSession, reset: bool = False) -> dict[str, Any]:
    pid_uuid = uuid.UUID(DEMO_PATIENT_ID)
    doc_uuid = uuid.UUID(DEMO_DOC_ID)
    job_uuid = uuid.uuid4()
    now = datetime.now(timezone.utc)

    if reset:
        logger.info(" [!] --reset specified: purging existing extraction jobs and review queue state for demo document...")
        stmt_old_jobs = select(ExtractionJob).where(ExtractionJob.document_id == doc_uuid)
        old_jobs = (await session.execute(stmt_old_jobs)).scalars().all()
        for j in old_jobs:
            for qi in (await session.execute(select(ReviewQueueItem).where(ReviewQueueItem.job_id == j.id))).scalars().all():
                await session.delete(qi)
            for ef in (await session.execute(select(ExtractedFieldRecord).where(ExtractedFieldRecord.job_id == j.id))).scalars().all():
                await session.delete(ef)
            for pc in (await session.execute(select(PipelineCommit).where(PipelineCommit.job_id == j.id))).scalars().all():
                await session.delete(pc)
            await session.delete(j)
        await session.commit()
        logger.info("  -> Clean reset complete.")

    # 1. Ensure DocumentStorage exists
    stmt_ds = select(DocumentStorage).where(DocumentStorage.id == doc_uuid)
    res_ds = await session.execute(stmt_ds)
    if res_ds.scalar_one_or_none() is None:
        logger.info(" -> Staging demo document in DocumentStorage (s3://nexa-care-demo/aarav_sharma_clinical_panel.pdf)...")
        session.add(
            DocumentStorage(
                id=doc_uuid,
                patient_id=pid_uuid,
                storage_ref="s3://nexa-care-demo/aarav_sharma_clinical_panel.pdf",
                content_type="application/pdf",
                size=2048,
                uploaded_at=now,
            )
        )

    # 2. Create ExtractionJob
    logger.info(f" -> Initializing ExtractionJob {job_uuid} (status='queued')...")
    ej = ExtractionJob(
        id=job_uuid,
        patient_id=pid_uuid,
        document_id=doc_uuid,
        document_type="LAB_REPORT",
        status="queued",
        created_at=now,
    )
    session.add(ej)
    await session.commit()

    # 3. Execute background extraction orchestration
    logger.info(" -> Running AI pipeline orchestrator (PyTorch extraction + WS5 scoring + auto-approval gates)...")
    res = await process_extraction_job(str(job_uuid), session)

    # 4. Display results
    stmt_f = select(ExtractedFieldRecord).where(ExtractedFieldRecord.job_id == job_uuid)
    f_rows = (await session.execute(stmt_f)).scalars().all()

    logger.info("\n==========================================================================")
    logger.info(" 📄 DEMO DOCUMENT EXTRACTION SUMMARY")
    logger.info("==========================================================================")
    logger.info(f" Job ID:               {job_uuid}")
    logger.info(f" Final Job Status:     {res.get('status', 'scored')}")
    logger.info(f" Auto-Approved Fields: {res.get('auto_approved_count', 0)}")
    logger.info(f" Needs Review Fields:  {res.get('needs_review_count', 0)}")
    logger.info("--------------------------------------------------------------------------")
    for f in f_rows:
        logger.info(
            f" * [{f.status.upper().ljust(13)}] Field: {f.field_name.ljust(12)} | Value: {f.raw_value.ljust(24)} | Conf: {f.confidence:.2f} | Risk: {f.risk_level}"
        )
    logger.info("==========================================================================\n")
    return res


async def main() -> None:
    env = os.getenv("ENVIRONMENT", "development").lower().strip()
    if env in {"prod", "production"}:
        raise RuntimeError(f"Refusing to execute demo document setup in production environment ('{env}').")

    reset_flag = "--reset" in sys.argv
    logger.info("==========================================================================")
    logger.info(" 🔬 NEXA CARE CANONICAL DEMO DOCUMENT & REVIEW QUEUE SEEDER")
    logger.info("==========================================================================")
    engine = get_async_engine()
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        await setup_demo_document(session, reset=reset_flag)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
