from __future__ import annotations

from PIL import Image, ImageDraw

# Tables-only layout detection (no OCR) using a pretrained object detection model.
import torch
from transformers import AutoImageProcessor, TableTransformerForObjectDetection

# OCR for non-table areas
import pytesseract
from pytesseract import Output

TABLE_MODEL_NAME = "microsoft/table-transformer-detection"


def ocr_from_path(path: str) -> str:
    return pytesseract.image_to_string(Image.open(path))


# Load once at import-time so the model is cached in memory for the lifetime of the server.
# If loading fails, keep them as None so the app can still start and callers can handle it.
try:
    table_processor: AutoImageProcessor | None = AutoImageProcessor.from_pretrained(TABLE_MODEL_NAME)
    table_model: TableTransformerForObjectDetection | None = (
        TableTransformerForObjectDetection.from_pretrained(TABLE_MODEL_NAME)
    )
    if table_model is not None:
        table_model.eval()
except Exception:
    table_processor = None
    table_model = None


def analyze_document_layout(image_path: str) -> dict | None:
    """Detect table regions in a document image.

    Returns:
        {
          "standard_text": [],
          "tables": [[x0, y0, x1, y1], ...]
        }

    Notes:
        - This performs object detection only (tables). No OCR/text extraction.
        - Bounding boxes are returned in absolute pixel coordinates.
    """

    try:
        image = Image.open(image_path).convert("RGB")
    except FileNotFoundError:
        return None

    if table_processor is None or table_model is None:
        return None

    inputs = table_processor(images=image, return_tensors="pt")

    with torch.no_grad():
        outputs = table_model(**inputs)

    # Convert model outputs to absolute pixel boxes: [x0, y0, x1, y1]
    # image.size is (width, height); target_sizes expects (height, width)
    target_sizes = torch.tensor([image.size[::-1]])
    results = table_processor.post_process_object_detection(
        outputs,
        threshold=0.5,
        target_sizes=target_sizes,
    )[0]

    tables: list[list[int]] = []
    for box in results["boxes"]:
        x0, y0, x1, y1 = box.tolist()
        tables.append([int(x0), int(y0), int(x1), int(y1)])

    return {"standard_text": [], "tables": tables}


async def extract_standard_text(image_path: str, layout_data: dict) -> str | None:
    """Mask detected tables and run OCR on the remaining regions.

    Engineering requirements:
        - Open image with PIL
        - Draw solid black rectangles over layout_data["tables"]
        - Run pytesseract OCR on the masked image
        - Return raw extracted text as a single string

    Args:
        image_path: Path to the image file.
        layout_data: Output of analyze_document_layout(). Must include a "tables" key.

    Returns:
        Extracted OCR text (raw). Returns None if the image is missing.
    """

    try:
        image = Image.open(image_path).convert("RGB")
    except FileNotFoundError:
        return None

    draw = ImageDraw.Draw(image)
    tables = layout_data.get("tables", []) if isinstance(layout_data, dict) else []

    for box in tables:
        # Expect [x0, y0, x1, y1]
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        x0, y0, x1, y1 = box
        draw.rectangle([x0, y0, x1, y1], fill="black")

    text = pytesseract.image_to_string(image)
    return text.strip()


def structure_table_text(image_path: str, table_box: list) -> list[list[str]]:
    """Extract table text into a row/column grid from a single table bounding box."""

    image = Image.open(image_path).convert("RGB")
    if not isinstance(table_box, (list, tuple)) or len(table_box) != 4:
        return []

    x0, y0, x1, y1 = [int(coord) for coord in table_box]
    cropped = image.crop((x0, y0, x1, y1))

    data = pytesseract.image_to_data(cropped, output_type=Output.DICT)

    words: list[tuple[int, int, str]] = []
    for left, top, text in zip(data.get("left", []), data.get("top", []), data.get("text", [])):
        if text and text.strip():
            words.append((int(left), int(top), text.strip()))

    # Sort by vertical position first to form rows
    words.sort(key=lambda item: item[1])

    rows: list[dict[str, object]] = []
    for left, top, text in words:
        placed = False
        for row in rows:
            if abs(top - int(row["top"])) <= 10:
                row["items"].append((left, text))
                placed = True
                break
        if not placed:
            rows.append({"top": top, "items": [(left, text)]})

    table_grid: list[list[str]] = []
    for row in rows:
        items = sorted(row["items"], key=lambda item: item[0])
        table_grid.append([text for _, text in items])

    return table_grid
