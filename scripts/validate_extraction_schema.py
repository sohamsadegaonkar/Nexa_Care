"""
Run this against a folder of real sample documents before trusting
document_processor.extract_document_data() + the PII/clinical sharding
split (app/services/sharding.py) with real patient data.

What this does NOT do: it has no labeled ground truth and no network
access to evaluate model accuracy. It cannot tell you whether the model's
*extracted values* are correct. What it DOES do: run the configured
extraction model against each sample file and report which *keys* it
produced, split into "known PII", "known clinical", and "unrecognized" --
making it visible when the model's actual output keys have drifted from
what split_pii_and_clinical_fields() expects, instead of that mismatch
silently misrouting data (which is exactly how aadhaar_abha_id almost
ended up in the clinical shard).

Usage:
    python scripts/validate_extraction_schema.py /path/to/sample/documents/

For each unrecognized key it reports, get a clinician or compliance
reviewer to confirm whether it's PII -- don't assume either way -- then
add it to PII_FIELD_NAMES (app/observability/redactor.py) or
CLINICAL_FIELD_NAMES (app/services/sharding.py) accordingly.

Also worth checking explicitly: the configured checkpoint
(naver-clova-ix/donut-base-finetuned-cord-v2, see document_processor.py)
was fine-tuned on CORD, a *receipt* dataset (store name, line items,
totals) -- not medical documents. If this script reports mostly
unrecognized keys, or empty output, across a real sample set, that's the
likely reason, and the fix is fine-tuning or swapping the model, not
loosening the field-name sets this script checks against.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

# Now your import will work
from document_processor import extract_document_data # noqa: E402
from app.services.sharding import split_pii_and_clinical_fields# noqa: E402

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf"}


def main(sample_dir: str) -> None:
    sample_path = Path(sample_dir)
    if not sample_path.is_dir():
        print(f"Not a directory: {sample_dir}")
        sys.exit(1)

    files = sorted(p for p in sample_path.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES)

    if not files:
        print(f"No documents with extensions {sorted(SUPPORTED_SUFFIXES)} found in {sample_dir}")
        return

    all_unrecognized: set[str] = set()
    empty_extractions = 0

    for file_path in files:
        extracted = extract_document_data(str(file_path))

        print(f"\n{file_path.name}")

        if not extracted:
            print("  -> extraction returned nothing (model failed to load, or produced no parseable output)")
            empty_extractions += 1
            continue

        vault, clinical, unrecognized = split_pii_and_clinical_fields(extracted)

        print(f"  -> vault keys:        {sorted(vault.keys())}")
        print(f"  -> clinical keys:     {sorted(clinical.keys())}")
        if unrecognized:
            print(f"  -> UNRECOGNIZED keys: {sorted(unrecognized.keys())}  <-- review these")
            all_unrecognized.update(unrecognized.keys())

    print("\n" + "=" * 60)
    print(f"{len(files)} file(s) checked, {empty_extractions} produced no output at all.")

    if all_unrecognized:
        print(
            f"\n{len(all_unrecognized)} unrecognized key name(s) seen across the sample "
            f"set: {sorted(all_unrecognized)}\n"
            "For each one: confirm with a clinician/compliance reviewer whether it's PII, "
            "then add it to the appropriate set -- do not assume either way."
        )
    else:
        print("\nNo unrecognized keys across this sample set.")

    print(
        "\nThis only reflects the files you pointed it at, and says nothing about whether "
        "the extracted *values* are accurate. It is not a substitute for evaluating "
        "against a properly labeled medical-document validation set."
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/validate_extraction_schema.py <sample_dir>")
        sys.exit(1)
    main(sys.argv[1])