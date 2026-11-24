"""HTTP qualification with real PostgreSQL, Redis, encryption and a software test key."""

import asyncio
import base64
from pathlib import Path
import json
import os
from hashlib import sha256
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import text

from app.main import app
from app.security.patient_source_malware_scanner import (
    MalwareScanOutcome,
    MalwareScanResult,
)
from tests.integration.test_patient_provider_true_e2e import (
    _established_clinical_access,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres, pytest.mark.redis]


@pytest.fixture(autouse=True)
def override_deps():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def signed_decision(graph, card, decision):
    signed = {
        **card["context"],
        "decision": decision,
        "device_id": str(graph["device_id"]),
        "key_id": str(graph["device_key_id"]),
        "key_version": graph["device_key_version"],
        "key_fingerprint": graph["device_key_fingerprint"],
    }
    sig = graph["private_key"].sign(
        json.dumps(signed, sort_keys=True, separators=(",", ":")).encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    return {
        "request_id": card["request_id"],
        "decision": decision,
        "signature": base64.b64encode(sig).decode(),
        "key_id": signed["key_id"],
    }


async def test_single_document_patient_decision_and_audited_open(monkeypatch, tmp_path):
    report = (
        Path(__file__).resolve().parents[2]
        / "docs/demo/synthetic-hospital-x-report.pdf"
    ).read_bytes()
    monkeypatch.setenv("DOCUMENT_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("DOCUMENT_STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "DOCUMENT_STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "demo")
    if os.getenv("P1B_REHEARSAL_CLAMD") != "1":
        scanner_stub = AsyncMock()
        scanner_stub.scan.return_value = MalwareScanResult(
            MalwareScanOutcome.CLEAN, sha256(report).hexdigest()
        )
        monkeypatch.setattr(
            "app.services.patient_external_record_source_safety.get_patient_source_malware_scanner",
            lambda: scanner_stub,
        )
    async with _established_clinical_access("Synthetic Hospital Y") as ctx:
        client = ctx.client
        patient = {"Authorization": "Bearer " + ctx.patient_token}
        doctor = {**ctx.auth_headers, "X-Patient-Id": str(ctx.graph["patient_id"])}
        imports = "/api/v2/patient/me/external-records"
        api = "/api/v2/original-access/v1"
        upload = await client.post(
            imports,
            headers=patient,
            data={"category": "lab_report"},
            files={"file": ("synthetic.pdf", report, "application/pdf")},
        )
        assert upload.status_code == 202, upload.text
        item = imports + "/" + upload.json()["import_id"]
        process = await client.post(item + "/process", headers=patient)
        assert process.status_code == 200, process.text
        review = await client.get(item + "/review", headers=patient)
        assert review.status_code == 200, review.text
        assert [item["extracted_value"] for item in review.json()["items"]] == ["6.1%"]
        for evidence in review.json()["items"]:
            accepted = await client.post(
                item + "/review/" + evidence["review_item_id"],
                headers=patient,
                json={"decision": "accept"},
            )
            assert accepted.is_success, accepted.text
        saved = await client.post(item + "/save", headers=patient)
        assert saved.is_success, saved.text
        inventory = await client.get(
            "/api/v2/provider/records/documents", headers=doctor
        )
        assert inventory.is_success, inventory.text
        doc = inventory.json()["items"][0]["document_id"]
        new = await client.post(
            api + "/request", headers=doctor, json={"document_id": doc}
        )
        assert new.status_code == 201, new.text
        selection = {"request_id": new.json()["request_id"]}
        before = await client.post(api + "/open", headers=doctor, json=selection)
        assert before.status_code == 403
        cards = await client.get(api + "/mine", headers=patient)
        assert cards.is_success, cards.text
        card = next(
            c for c in cards.json() if c["request_id"] == selection["request_id"]
        )
        assert card["hospital"] == "Synthetic Hospital Y"
        assert card["context"]["document_id"] == doc
        graph = ctx.graph
        payload = signed_decision(graph, card, "approved")
        approved = await client.post(api + "/decision", headers=patient, json=payload)
        assert approved.is_success, approved.text
        duplicate = await client.post(api + "/decision", headers=patient, json=payload)
        assert duplicate.status_code == 409
        from app.api.v2 import original_document_routes as routes

        # Each failed dependency must leave the approved claim usable and release zero bytes.
        for dependency in ("get_document_storage", "enqueue_audit_event"):
            with monkeypatch.context() as fault:
                if dependency == "enqueue_audit_event":
                    fault.setattr(
                        routes,
                        dependency,
                        AsyncMock(side_effect=RuntimeError("outbox unavailable")),
                    )
                else:
                    fault.setattr(routes, dependency, lambda: None)
                refused = await client.post(
                    api + "/open", headers=doctor, json=selection
                )
                assert refused.status_code == 503
                assert report not in refused.content
        with monkeypatch.context() as fault:
            from app.services.patient_external_record_source_safety import (
                PatientSourceSafetyError,
            )

            fault.setattr(
                routes,
                "qualify_patient_source_for_extraction",
                AsyncMock(
                    side_effect=PatientSourceSafetyError(
                        "SOURCE_MALWARE_DETECTED", retryable=False
                    )
                ),
            )
            blocked = await client.post(api + "/open", headers=doctor, json=selection)
            assert blocked.status_code == 415
        opens = await asyncio.gather(
            *(
                client.post(api + "/open", headers=doctor, json=selection)
                for _ in range(2)
            )
        )
        assert sorted(r.status_code for r in opens) == [200, 403], [
            (r.status_code, r.text[:100]) for r in opens
        ]
        original = next(r for r in opens if r.status_code == 200)
        assert original.content == report
        assert "no-store" in original.headers["cache-control"]
        assert original.headers["content-type"] == "application/pdf"
        revoked = await client.post(api + "/revoke", headers=patient, json=selection)
        assert revoked.json()["status"] == "revoked"
        status = await client.post(api + "/status", headers=doctor, json=selection)
        assert status.json()["status"] == "revoked"
        assert (
            await client.post(api + "/open", headers=doctor, json=selection)
        ).status_code == 403
        # Prove prospective revocation independently of the already-consumed one-time claim.
        for decision in ("denied", "approved"):
            fresh = await client.post(
                api + "/request", headers=doctor, json={"document_id": doc}
            )
            assert fresh.is_success, fresh.text
            fresh_id = {"request_id": fresh.json()["request_id"]}
            history = await client.get(api + "/mine", headers=patient)
            card = next(
                c for c in history.json() if c["request_id"] == fresh_id["request_id"]
            )
            result = await client.post(
                api + "/decision",
                headers=patient,
                json=signed_decision(graph, card, decision),
            )
            assert result.json()["status"] == decision
            if decision == "approved":
                result = await client.post(
                    api + "/revoke", headers=patient, json=fresh_id
                )
                assert result.json()["status"] == "revoked"
            assert (
                await client.post(api + "/open", headers=doctor, json=fresh_id)
            ).status_code == 403

        async with ctx.engine.connect() as conn:
            events = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type FROM audit_outbox WHERE patient_id=:patient"
                        ),
                        {"patient": str(graph["patient_id"])},
                    )
                )
                .scalars()
                .all()
            )
        assert events.count("ORIGINAL_DOCUMENT_OPENED") == 1
        assert {
            "ORIGINAL_DOCUMENT_REQUESTED",
            "ORIGINAL_DOCUMENT_APPROVED",
            "ORIGINAL_DOCUMENT_REVOKED",
        } <= set(events)
