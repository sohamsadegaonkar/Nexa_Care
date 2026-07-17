"""Dry-run-first inventory/quarantine for explicitly demo-provenance pipeline jobs.

This script never deletes clinical data and never guesses from a patient's name,
identifier, filename, or medical value.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import or_, select

from app.core.database import get_session_factory
from app.models.pipeline import DocumentStorage, ExtractionJob


async def run(apply: bool) -> int:
    async with get_session_factory()() as db:
        rows = (await db.execute(
            select(ExtractionJob).join(DocumentStorage, DocumentStorage.id == ExtractionJob.document_id)
            .where(or_(
                ExtractionJob.extractor_provider == "demo",
                DocumentStorage.source_system.in_(["demo", "alpha-demo", "synthetic-seed"]),
            ))
            .with_for_update() if apply else
            select(ExtractionJob).join(DocumentStorage, DocumentStorage.id == ExtractionJob.document_id)
            .where(or_(
                ExtractionJob.extractor_provider == "demo",
                DocumentStorage.source_system.in_(["demo", "alpha-demo", "synthetic-seed"]),
            ))
        )).scalars().all()
        print(f"candidate_jobs={len(rows)} mode={'apply' if apply else 'dry-run'}")
        for job in rows:
            print(f"job_id={job.id} document_id={job.document_id} status={job.status}")
            if apply and job.status != "committed":
                job.status = "quarantined"
                job.error_code = "EXPLICIT_DEMO_PROVENANCE"
                job.retryable = False
        if apply:
            await db.commit()
        return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-quarantine", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.apply_quarantine and args.confirm != "QUARANTINE_DEMO_PIPELINE_ROWS":
        parser.error("--apply-quarantine requires --confirm QUARANTINE_DEMO_PIPELINE_ROWS")
    asyncio.run(run(args.apply_quarantine))


if __name__ == "__main__":
    main()
