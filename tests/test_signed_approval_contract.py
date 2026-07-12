from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEVICE_KEYS = ROOT / 'nexa-client/packages/app/services/deviceKeys.ts'
CONSENT_SIGNING = ROOT / 'nexa-client/packages/app/services/consentSigning.ts'
VERIFIER = ROOT / 'app/services/signed_approval_verifier.py'
APPROVAL_ROOTS = [ROOT / 'nexa-client/packages/app/features/approval', ROOT / 'nexa-client/packages/app/features/patient', ROOT / 'nexa-client/apps/next/app/push-approval', ROOT / 'nexa-client/apps/expo/app/push-approval']
FIELDS = ['request_id', 'patient_id', 'provider_id', 'challenge_nonce', 'decision', 'scope', 'purpose', 'access_duration', 'expires_at']

def _without_comments(text: str) -> str:
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)

def test_frontend_and_backend_share_exact_nine_field_contract():
    frontend = DEVICE_KEYS.read_text(encoding='utf-8')
    backend = VERIFIER.read_text(encoding='utf-8')
    positions = [frontend.index(f'params.{field}') for field in FIELDS]
    assert positions == sorted(positions)
    backend_positions = [backend.index(field, backend.index('signing_input_9')) for field in FIELDS]
    assert backend_positions == sorted(backend_positions)
    assert '.join("|")' in frontend or ".join('|')" in frontend
    assert 'f"{request_id}|{patient_id}|{provider_id' in backend
    assert 'Prehashed(hashes.SHA256())' in backend

def test_reachable_patient_approval_has_no_legacy_responder():
    offenders = []
    for root in APPROVAL_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if path.suffix not in {'.ts', '.tsx'}:
                continue
            code = _without_comments(path.read_text(encoding='utf-8'))
            if '/respond' in code or 'respondToPushRequest' in code or 'getPushRequestStatus' in code:
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders
    facade = CONSENT_SIGNING.read_text(encoding='utf-8')
    assert 'approveSignedConsent' in facade
    assert 'denySignedConsent' in facade

def test_flagship_crypto_tests_do_not_mock_verifier():
    for rel in ['tests/test_signed_approval.py', 'tests/integration/test_consent_flow_qa.py']:
        code = (ROOT / rel).read_text(encoding='utf-8')
        assert 'patch("app.api.v2.consent_routes.SignedApprovalVerifier' not in code
        assert 'generate_private_key' in code or 'generate_p256_keypair' in code
