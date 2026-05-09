from __future__ import annotations

from PIL import Image

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


def extract_document_data(image_path: str) -> dict:
    """Run Donut vision-to-text extraction and return a JSON payload."""

    if donut_processor is None or donut_model is None:
        return {}

    try:
        image = Image.open(image_path).convert("RGB")
    except FileNotFoundError:
        return {}

    with torch.no_grad():
        pixel_values = donut_processor(image, return_tensors="pt").pixel_values
        decoder_input_ids = donut_processor.tokenizer(
            "<s>", add_special_tokens=False, return_tensors="pt"
        ).input_ids
        outputs = donut_model.generate(
            pixel_values,
            decoder_input_ids=decoder_input_ids,
            max_length=donut_model.config.max_length,
            early_stopping=True,
            pad_token_id=donut_processor.tokenizer.pad_token_id,
            eos_token_id=donut_processor.tokenizer.eos_token_id,
        )

    sequence = donut_processor.batch_decode(outputs, skip_special_tokens=True)[0]
    sequence = sequence.replace(donut_processor.tokenizer.eos_token, "").replace(
        donut_processor.tokenizer.pad_token, ""
    )

    try:
        return donut_processor.token2json(sequence)
    except Exception:
        return {}
