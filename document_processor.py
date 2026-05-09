from __future__ import annotations

from PIL import Image, ImageDraw

import torch
from transformers import AutoImageProcessor, TableTransformerForObjectDetection

# OCR for non-table areas and individual table cells
import pytesseract

TABLE_MODEL_NAME = "microsoft/table-transformer-detection"
STRUCTURE_MODEL_NAME = "microsoft/table-transformer-structure-recognition"

# Load once at import-time so the models are cached in memory for the lifetime of the server.
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

try:
    structure_processor: AutoImageProcessor | None = AutoImageProcessor.from_pretrained(
        STRUCTURE_MODEL_NAME
    )
    structure_model: TableTransformerForObjectDetection | None = (
        TableTransformerForObjectDetection.from_pretrained(STRUCTURE_MODEL_NAME)
    )
    if structure_model is not None:
        structure_model.eval()
except Exception:
    structure_processor = None
    structure_model = None


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


def analyze_and_extract_tables(image_path: str) -> list[list[list[str]]]:
    """Detect table structure and OCR each cell.

    Returns a list of tables, where each table is a 2D list (rows x columns).
    """

    try:
        image = Image.open(image_path).convert("RGB")
    except FileNotFoundError:
        return []

    if structure_processor is None or structure_model is None:
        return []

    inputs = structure_processor(images=image, return_tensors="pt")

    with torch.no_grad():
        outputs = structure_model(**inputs)

    target_sizes = torch.tensor([image.size[::-1]])
    results = structure_processor.post_process_object_detection(
        outputs,
        threshold=0.5,
        target_sizes=target_sizes,
    )[0]

    id2label = getattr(structure_model.config, "id2label", {})

    rows: list[list[float]] = []
    columns: list[list[float]] = []
    for box, label_id in zip(results.get("boxes", []), results.get("labels", [])):
        label_name = str(id2label.get(int(label_id), "")).lower()
        if "row" in label_name:
            rows.append(box.tolist())
        elif "column" in label_name:
            columns.append(box.tolist())

    if not rows or not columns:
        return []

    rows.sort(key=lambda b: b[1])
    columns.sort(key=lambda b: b[0])

    table_grid: list[list[str]] = []
    for row in rows:
        row_cells: list[str] = []
        for col in columns:
            x0 = max(row[0], col[0])
            y0 = max(row[1], col[1])
            x1 = min(row[2], col[2])
            y1 = min(row[3], col[3])
            if x1 <= x0 or y1 <= y0:
                row_cells.append("")
                continue
            cell = image.crop((int(x0), int(y0), int(x1), int(y1)))
            text = pytesseract.image_to_string(cell).strip()
            row_cells.append(text)
        table_grid.append(row_cells)

    return [table_grid]
