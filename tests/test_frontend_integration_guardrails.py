import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = [
    ROOT / "nexa-client/packages/app/api",
    ROOT / "nexa-client/packages/app/features",
    ROOT / "nexa-client/packages/app/services",
    ROOT / "nexa-client/packages/app/utils",
    ROOT / "nexa-client/apps/next/app",
    ROOT / "nexa-client/apps/expo/app",
]


def _sources():
    for root in SCAN:
        if root.exists():
            yield from (p for p in root.rglob("*") if p.suffix in {".ts", ".tsx"})


def test_frontend_transport_and_device_key_are_canonical():
    canonical = ROOT / "nexa-client/packages/app/utils/apiClient.ts"
    offenders = []
    key_modules = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if path != canonical and (
            "fetch(" in text or "from 'axios'" in text or 'from "axios"' in text
        ):
            offenders.append(str(path))
        if (
            "from '../utils/api'" in text
            or "from '../../utils/api'" in text
            or "http://localhost" in text
            or "http://127.0.0.1" in text
        ):
            offenders.append(str(path))
        if "randomPrivateKey(" in text or "p256.sign(" in text:
            key_modules.append(path.relative_to(ROOT).as_posix())
    assert not offenders
    assert sorted(set(key_modules)) == [
        "nexa-client/packages/app/services/deviceKeys.ts"
    ]
    assert not (ROOT / "nexa-client/packages/app/utils/deviceKey.ts").exists()


def test_unsafe_auth_stub_is_absent():
    assert not (ROOT / "app/api/v2/auth_routes_improved.py").exists()
    for path in (ROOT / "app/api").rglob("*.py"):
        assert "TODO: Add actual authentication logic here" not in path.read_text(
            encoding="utf-8"
        )


def test_expo_metro_uses_complete_sdk_54_defaults():
    metro = (ROOT / "nexa-client/apps/expo/metro.config.js").read_text(encoding="utf-8")
    assert "module.exports = getDefaultConfig(__dirname)" in metro
    assert "watchFolders" not in metro
    assert "nodeModulesPaths" not in metro
    assert "unstable_conditionNames" not in metro


def test_expo_native_dependencies_are_not_duplicated_or_internal():
    expo_package = json.loads(
        (ROOT / "nexa-client/apps/expo/package.json").read_text(encoding="utf-8")
    )
    app_package = json.loads(
        (ROOT / "nexa-client/packages/app/package.json").read_text(encoding="utf-8")
    )
    expo_direct = expo_package.get("dependencies", {}) | expo_package.get(
        "devDependencies", {}
    )

    assert "@expo/config-plugins" not in expo_direct
    assert "@expo/metro-config" not in expo_direct
    assert expo_package["dependencies"]["react-native-safe-area-context"] == "~5.6.0"
    assert app_package["dependencies"]["react-native-safe-area-context"] == "~5.6.0"


def test_expo_entry_initializes_tamagui_native_without_env_mutation():
    entry = (ROOT / "nexa-client/apps/expo/index.js").read_text(encoding="utf-8")
    assert entry.lstrip().startswith("import '@tamagui/native/setup-zeego'")
    assert "process.env.EXPO_OS" not in entry


def test_expo_babel_does_not_execute_native_screens_during_build():
    babel = (ROOT / "nexa-client/apps/expo/babel.config.js").read_text(encoding="utf-8")
    assert "@tamagui/babel-plugin" not in babel
