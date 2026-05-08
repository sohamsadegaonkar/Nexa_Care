from __future__ import annotations

from PIL import Image
from transformers import LayoutLMv3ForTokenClassification, LayoutLMv3Processor

MODEL_NAME = "microsoft/layoutlmv3-base"

# Load once at import-time so the model is cached in memory for the lifetime of the server.
# If loading fails, keep them as None so the app can still start and callers can handle it.
try:
    processor: LayoutLMv3Processor | None = LayoutLMv3Processor.from_pretrained(MODEL_NAME)
    model: LayoutLMv3ForTokenClassification | None = LayoutLMv3ForTokenClassification.from_pretrained(
        MODEL_NAME
    )
except Exception:
    processor = None
    model = None


def analyze_document_layout(image_path: str):
    try:
        image = Image.open(image_path).convert("RGB")
    except FileNotFoundError:
        return None
