"""
Shared test setup.

document_processor.py loads a transformer checkpoint from the network at
*import* time (module-level `DonutProcessor.from_pretrained(...)`). Tests
should never need real OCR inference, and must run offline and fast, so we
substitute a lightweight stub into sys.modules before anything -- in
particular app.main -- imports it.

This is module-level code (not a fixture), so it runs when pytest imports
this conftest.py, which happens before pytest imports any test module in
this directory. That ordering is what makes the stub take effect in time.
"""
import sys
import types

if "document_processor" not in sys.modules:
    _stub = types.ModuleType("document_processor")
    _stub.extract_document_data = lambda file_path: {}
    sys.modules["document_processor"] = _stub