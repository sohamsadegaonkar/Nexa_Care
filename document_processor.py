from transformers import LayoutLMv3Processor, LayoutLMv3ForTokenClassification
from PIL import Image


def analyze_document_layout(image_path: str):
    try:
        image = Image.open(image_path).convert("RGB")
    except FileNotFoundError:
        return None
