"""Offline candidate manifest renderer; never invokes ARM, Azure CLI or network.

Development/synthetic ONLY. Output is a review artifact, not permission to deploy.
Existing resources and version-pinned Key Vault secret references are required.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_SECRET_ENV = (
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "DATABASE_URL",
    "UPSTASH_REDIS_URL",
    "KEK_ROOT_SECRET",
    "DOCUMENT_STORAGE_ENCRYPTION_KEY",
    "HANDSHAKE_PEPPER_SECRET",
    "MFA_ENCRYPTION_KEY",
    "PII_ENCRYPTION_KEY",
    "PATIENT_JWT_SECRET",
    "OTP_RATE_LIMIT_HMAC_SECRET",
    "PROVIDER_REGISTRATION_IDEMPOTENCY_HMAC_SECRET",
    "PROVIDER_CONTACT_ASSURANCE_HMAC_SECRET",
    "PATIENT_GRANT_REFERENCE_HMAC_SECRET",
    "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
    "OPERATIONS_AUTH_TOKEN",
)
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ARM_PREFIX = rf"/subscriptions/({_UUID})/resourceGroups/(nexa-demo-[a-z0-9-]+)"
_IDENTITY = _ARM_PREFIX + r"/providers/Microsoft\.ManagedIdentity/userAssignedIdentities/(nexa-demo-[a-z0-9-]+)"
_ENVIRONMENT = _ARM_PREFIX + r"/providers/Microsoft\.App/managedEnvironments/(nexa-demo-[a-z0-9-]+)"
_VAULT_SECRET = re.compile(
    r"https://(nexa-demo-[a-z0-9-]{1,14})\.vault\.azure\.net/secrets/([a-z0-9-]+)/([0-9a-fA-F]{32})"
)


def _fail(code: str) -> None:
    raise ValueError(code)


def render_plan(spec: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "synthetic_only", "identity_id", "identity_client_id", "environment_id",
        "location", "image", "storage_account", "storage_container", "secret_uris",
    }
    if set(spec) != expected or spec.get("synthetic_only") is not True:
        _fail("SYNTHETIC_DEMO_PLAN_REQUIRED")
    identity = re.fullmatch(_IDENTITY, str(spec["identity_id"]), flags=re.IGNORECASE)
    environment = re.fullmatch(_ENVIRONMENT, str(spec["environment_id"]), flags=re.IGNORECASE)
    if not identity or not environment or identity.groups()[:2] != environment.groups()[:2]:
        _fail("DEDICATED_NEXA_ENVIRONMENT_AND_IDENTITY_REQUIRED")
    if not re.fullmatch(_UUID, str(spec["identity_client_id"])):
        _fail("IDENTITY_CLIENT_ID_REQUIRED")
    if not re.fullmatch(r"[a-z][a-z0-9]{2,24}", str(spec["location"])):
        _fail("EXPLICIT_AZURE_REGION_REQUIRED")
    # Require a prebuilt, separately approved immutable image from a dedicated ACR.
    image = str(spec["image"])
    if not re.fullmatch(r"nexademo[a-z0-9]*\.azurecr\.io/nexa-care-api@sha256:[0-9a-f]{64}", image):
        _fail("DEDICATED_DIGEST_PINNED_IMAGE_REQUIRED")
    account = str(spec["storage_account"])
    container = str(spec["storage_container"])
    if not re.fullmatch(r"nexademo[a-z0-9]{1,16}", account):
        _fail("DEDICATED_NEXA_STORAGE_ACCOUNT_REQUIRED")
    if not re.fullmatch(r"nexa-demo-[a-z0-9]+(?:-[a-z0-9]+)*", container) or len(container) > 63:
        _fail("DEDICATED_NEXA_STORAGE_CONTAINER_REQUIRED")
    refs = spec["secret_uris"]
    if not isinstance(refs, dict) or set(refs) != set(_SECRET_ENV):
        _fail("EXACT_SECRET_REFERENCE_SET_REQUIRED")
    vaults = set()
    secrets = []
    env = []
    for index, name in enumerate(_SECRET_ENV, start=1):
        uri = refs[name]
        match = _VAULT_SECRET.fullmatch(uri) if isinstance(uri, str) else None
        expected_name = name.lower().replace("_", "-")
        if not match or match.group(2) != expected_name:
            _fail("VERSION_PINNED_DEDICATED_VAULT_SECRET_REQUIRED")
        vaults.add(match.group(1))
        secrets.append({
            "name": f"nexa-secret-{index:02d}",
            "keyVaultUrl": uri,
            "identity": spec["identity_id"],
        })
        env.append({"name": name, "secretRef": f"nexa-secret-{index:02d}"})
    if len(vaults) != 1:
        _fail("ONE_DEDICATED_VAULT_REQUIRED")
    fixed = {
        "ENVIRONMENT": "development",
        "PORT": "8000",
        "DOCUMENT_STORAGE_PROVIDER": "azure_blob",
        "DOCUMENT_STORAGE_AZURE_SYNTHETIC_ONLY": "YES",
        "DOCUMENT_STORAGE_AZURE_ACCOUNT": account,
        "DOCUMENT_STORAGE_AZURE_CONTAINER": container,
        "DOCUMENT_STORAGE_AZURE_MANAGED_IDENTITY_CLIENT_ID": spec["identity_client_id"],
        "ENCRYPTION_BACKEND": "local",
        "DOCUMENT_EXTRACTION_PROVIDER": "demo",
        "DOCUMENT_AI_ASYNC_MULTIPAGE_ENABLED": "false",
        "AUTO_COMMIT": "false",
        "DATABASE_ECHO_SQL": "false",
        "DATABASE_SSL_CA_PATH": "/etc/ssl/certs/ca-certificates.crt",
        "PUSH_STATUS_TRANSPORT": "poll",
        "PATIENT_SOURCE_MALWARE_SCANNER": "clamd",
        "PATIENT_SOURCE_CLAMD_HOST": "127.0.0.1",
        "PATIENT_SOURCE_CLAMD_PORT": "3310",
    }
    env.extend({"name": k, "value": v} for k, v in fixed.items())
    # A review candidate only: scanner image, tenant ingress, TLS, exact schema,
    # enrollment and network/identity preflight require separate qualification.
    return {
        "status": "REVIEW_CANDIDATE_NOT_DEPLOYABLE",
        "synthetic_only": True,
        "blockers": [
            "VERIFY_ACCOUNT_COST_QUOTAS_AND_USAGE_TERMS",
            "VERIFY_PRIVATE_ENVIRONMENT_AND_EXACT_RESOURCE_RBAC",
            "QUALIFY_DIGEST_PINNED_API_AND_CLAMD_SIDECAR",
            "VERIFY_FRESH_DATABASE_TLS_AND_EXACT_SCHEMA_HEAD",
            "VERIFY_TLS_REDIS_AND_SECRET_CUSTODY",
            "VERIFY_BLOB_READBACK_AND_RETENTION",
            "COMPLETE_SIGNED_CONSENT_AND_DEVICE_REHEARSAL",
        ],
        "candidate": {
            "location": spec["location"],
            "identity": {
                "type": "UserAssigned",
                "userAssignedIdentities": {spec["identity_id"]: {}},
            },
            "properties": {
                "managedEnvironmentId": spec["environment_id"],
                "configuration": {
                    "activeRevisionsMode": "Single",
                    "ingress": {"external": False, "allowInsecure": False, "targetPort": 8000},
                    "secrets": secrets,
                    "registries": [{
                        "server": image.split("/", 1)[0],
                        "identity": spec["identity_id"],
                    }],
                },
                "template": {
                    "containers": [{
                        "name": "nexa-demo-api",
                        "image": image,
                        "env": env,
                        "resources": {"cpu": 0.5, "memory": "1Gi"},
                        "probes": [
                            {"type": "Liveness", "httpGet": {"path": "/healthz", "port": 8000}},
                            {"type": "Readiness", "httpGet": {"path": "/health", "port": 8000}},
                        ],
                    }],
                    "scale": {"minReplicas": 0, "maxReplicas": 1},
                },
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="JSON metadata only; no secret values")
    args = parser.parse_args()
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            _fail("INVALID_PLAN_SPEC")
        print(json.dumps(render_plan(spec), indent=2))
    except (OSError, ValueError, TypeError):
        print("AZURE_DEMO_PLAN_REJECTED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
