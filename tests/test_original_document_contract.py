import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from pypdf import PdfWriter

from app.api.v2.original_document_routes import state
from app.services.original_document_access import signing_input, verify_device_decision
from app.services.original_document_preview import require_passive_pdf


@pytest.mark.parametrize(
    "field",
    [
        "patient_id",
        "provider_id",
        "hospital_id",
        "document_id",
        "history_id",
        "id",
        "session_hash",
        "source_hash",
        "nonce",
        "issued_at",
        "expires_at",
        "decision",
    ],
)
@pytest.mark.asyncio
async def test_signature_verifies_only_the_exact_issued_decision(field):
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id=uuid4(),
        patient_id=uuid4(),
        provider_id=uuid4(),
        hospital_id=uuid4(),
        document_id=uuid4(),
        history_id=uuid4(),
        session_hash="a" * 64,
        source_hash="b" * 64,
        nonce="c" * 64,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    private = ec.generate_private_key(ec.SECP256R1())
    key = SimpleNamespace(
        id=uuid4(),
        device_id=uuid4(),
        key_version=1,
        public_key_fingerprint="d" * 64,
        device_public_key=private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = key
    db.execute.return_value = result
    signature = private.sign(
        signing_input(row, key, "approved"), ec.ECDSA(hashes.SHA256())
    )
    await verify_device_decision(db, row, key.id, signature, "approved")
    decision = "approved"
    if field == "decision":
        decision = "denied"
    elif field in {"issued_at", "expires_at"}:
        setattr(row, field, now + timedelta(days=1))
    else:
        setattr(row, field, str(uuid4()))
    with pytest.raises(HTTPException) as exc:
        await verify_device_decision(db, row, key.id, signature, decision)
    assert exc.value.status_code == 403


def test_expired_or_revoked_approval_is_terminal():
    for status in ("pending", "approved", "opened", "revoked", "denied"):
        row = SimpleNamespace(
            status=status, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        assert state(row) == (status if status in ("revoked", "denied") else "expired")


def test_preview_rejects_attachments_and_accepts_passive_pdf():
    pdf = PdfWriter()
    pdf.add_blank_page(width=300, height=200)
    safe = io.BytesIO()
    pdf.write(safe)
    require_passive_pdf(safe.getvalue())
    pdf.add_attachment("sample.txt", b"synthetic test attachment")
    attached = io.BytesIO()
    pdf.write(attached)
    with pytest.raises(HTTPException) as exc:
        require_passive_pdf(attached.getvalue())
    assert exc.value.status_code == 415
