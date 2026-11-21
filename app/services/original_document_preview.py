"""Only passive, self-contained PDFs may enter the local clinician viewer."""

from io import BytesIO

from fastapi import HTTPException
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

# Closed catalogue and page capabilities: disallow forms, document actions, annotations,
# embedded files, external streams, remote references and plugin/media behaviour.
_UNSAFE = frozenset(
    {
        "/AA",
        "/A",
        "/OpenAction",
        "/JS",
        "/JavaScript",
        "/Launch",
        "/AcroForm",
        "/Annots",
        "/EmbeddedFiles",
        "/EF",
        "/XFA",
        "/RichMedia",
        "/Movie",
        "/Sound",
        "/URI",
        "/GoToR",
        "/GoToE",
        "/SubmitForm",
        "/ImportData",
        "/FileAttachment",
        "/Filespec",
        "/Collection",
        "/AF",
        "/Ref",
        "/OPI",
        "/F",
        "/FFilter",
        "/FDecodeParms",
    }
)


def require_passive_pdf(data: bytes) -> None:
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        root = reader.trailer["/Root"]
        if set(root) - {
            "/Type",
            "/Pages",
            "/Version",
            "/Lang",
            "/Metadata",
            "/PageLayout",
            "/PageMode",
            "/ViewerPreferences",
            "/MarkInfo",
            "/StructTreeRoot",
        }:
            raise ValueError
        pending = [root]
        visited = set()
        count = 0
        while pending:
            obj = pending.pop()
            count += 1
            if count > 50000:
                raise ValueError
            if isinstance(obj, IndirectObject):
                identity = (obj.idnum, obj.generation)
                if identity not in visited:
                    visited.add(identity)
                    pending.append(obj.get_object())
            elif isinstance(obj, DictionaryObject):
                if _UNSAFE.intersection(obj.keys()) or str(obj.get("/Type")) in _UNSAFE:
                    raise ValueError
                pending.extend(obj.values())
            elif isinstance(obj, ArrayObject):
                pending.extend(obj)
    except Exception:
        raise HTTPException(415, "PDF is unavailable for safe preview") from None
