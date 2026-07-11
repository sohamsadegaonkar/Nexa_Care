from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = [ROOT / 'nexa-client/packages/app/features', ROOT / 'nexa-client/packages/app/services', ROOT / 'nexa-client/packages/app/utils', ROOT / 'nexa-client/apps/next/app', ROOT / 'nexa-client/apps/expo/app']

def _sources():
    for root in SCAN:
        if root.exists():
            yield from (p for p in root.rglob('*') if p.suffix in {'.ts', '.tsx'})

def test_frontend_transport_and_device_key_are_canonical():
    canonical = ROOT / 'nexa-client/packages/app/utils/apiClient.ts'
    offenders = []
    key_modules = []
    for path in _sources():
        text = path.read_text(encoding='utf-8')
        if path != canonical and ('fetch(' in text or "from 'axios'" in text or 'from "axios"' in text):
            offenders.append(str(path))
        if "from '../utils/api'" in text or "from '../../utils/api'" in text or 'http://localhost' in text or 'http://127.0.0.1' in text:
            offenders.append(str(path))
        if 'randomPrivateKey(' in text or 'p256.sign(' in text:
            key_modules.append(str(path.relative_to(ROOT)))
    assert not offenders
    assert sorted(set(key_modules)) == ['nexa-client/packages/app/services/deviceKeys.ts']
    assert not (ROOT / 'nexa-client/packages/app/utils/deviceKey.ts').exists()

def test_unsafe_auth_stub_is_absent():
    assert not (ROOT / 'app/api/v2/auth_routes_improved.py').exists()
    for path in (ROOT / 'app/api').rglob('*.py'):
        assert 'TODO: Add actual authentication logic here' not in path.read_text(encoding='utf-8')
