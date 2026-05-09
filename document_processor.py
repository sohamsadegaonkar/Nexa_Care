from __future__ import annotations

import re

from PIL import Image
from pdf2image import convert_from_path

import torch
from transformers import DonutProcessor, VisionEncoderDecoderModel

DONUT_MODEL_NAME = "naver-clova-ix/donut-base-finetuned-cord-v2"

# Load once at import-time so the model is cached in memory for the lifetime of the server.
# If loading fails, keep them as None so the app can still start and callers can handle it.
try:
    donut_processor: DonutProcessor | None = DonutProcessor.from_pretrained(DONUT_MODEL_NAME)
    donut_model: VisionEncoderDecoderModel | None = VisionEncoderDecoderModel.from_pretrained(
        DONUT_MODEL_NAME
    )
    if donut_model is not None:
        donut_model.eval()
except Exception:
    donut_processor = None
    donut_model = None


def prepare_image(file_path: str) -> Image.Image:
    """Load a PDF (first page) or image file and return an RGB PIL image."""

    if file_path.lower().endswith(".pdf"):
        pages = convert_from_path(file_path, first_page=1, last_page=1)
        if not pages:
            raise ValueError("PDF has no pages")
        return pages[0].convert("RGB")

    return Image.open(file_path).convert("RGB")


def _clean_sequence(sequence: str) -> str:
    sequence = sequence.replace("</s>", "")
    return re.sub(r"<s_[^>]+>", "", sequence).strip()


def extract_document_data(file_path: str) -> dict:
    """Run Donut vision-to-text extraction and return a JSON payload."""

    if donut_processor is None or donut_model is None:
        return {}

    try:
        image = prepare_image(file_path)

        with torch.no_grad():
            pixel_values = donut_processor(image, return_tensors="pt").pixel_values
            outputs = donut_model.generate(
                pixel_values,
                max_length=512,
                pad_token_id=donut_processor.tokenizer.pad_token_id,
                eos_token_id=donut_processor.tokenizer.eos_token_id,
            )

        sequence = donut_processor.batch_decode(outputs, skip_special_tokens=True)[0]
        sequence = _clean_sequence(sequence)
        return donut_processor.token2json(sequence)
    except Exception:
        return {}
