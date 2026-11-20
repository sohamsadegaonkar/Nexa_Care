"""Offline contract tests for the non-deployable Azure review artifact."""

from copy import deepcopy
import json

import pytest

from scripts.render_azure_demo_plan import _SECRET_ENV, render_plan


def _spec():
    root = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/nexa-demo-test"
    return {
        "synthetic_only": True,
        "identity_id": root + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/nexa-demo-runtime",
        "identity_client_id": "22222222-2222-2222-2222-222222222222",
        "environment_id": root + "/providers/Microsoft.App/managedEnvironments/nexa-demo-env",
        "location": "centralindia",
        "image": "nexademotest.azurecr.io/nexa-care-api@sha256:" + "a" * 64,
        "storage_account": "nexademotest",
        "storage_container": "nexa-demo-documents",
        "secret_uris": {
            name: "https://nexa-demo-test.vault.azure.net/secrets/"
            + name.lower().replace("_", "-") + "/" + "b" * 32
            for name in _SECRET_ENV
        },
    }


def test_private_cost_bounded_review_candidate_without_aws_runtime():
    plan = render_plan(_spec())
    assert plan["status"] == "REVIEW_CANDIDATE_NOT_DEPLOYABLE"
    assert plan["blockers"]
    props = plan["candidate"]["properties"]
    assert props["configuration"]["ingress"]["external"] is False
    assert props["configuration"]["ingress"]["allowInsecure"] is False
    assert props["template"]["scale"] == {"minReplicas": 0, "maxReplicas": 1}
    env = {x["name"]: x for x in props["template"]["containers"][0]["env"]}
    assert env["ENCRYPTION_BACKEND"]["value"] == "local"
    assert env["DOCUMENT_EXTRACTION_PROVIDER"]["value"] == "demo"
    assert env["DOCUMENT_STORAGE_PROVIDER"]["value"] == "azure_blob"
    assert env["PATIENT_SOURCE_MALWARE_SCANNER"]["value"] == "clamd"
    assert not any(k.startswith("AWS_") or "_S3_" in k for k in env)
    for name in _SECRET_ENV:
        assert set(env[name]) == {"name", "secretRef"}
    assert "password=" not in json.dumps(plan)


@pytest.mark.parametrize(
    "key,value",
    [
        ("synthetic_only", False),
        ("storage_account", "campuspoolbackup"),
        ("storage_container", "campuspool-staging-backups"),
        ("image", "nexademotest.azurecr.io/nexa-care-api:latest"),
        ("identity_client_id", "not-a-uuid"),
        ("location", "centralindia;deploy"),
    ],
)
def test_rejects_unapproved_spec(key, value):
    spec = _spec()
    spec[key] = value
    with pytest.raises(ValueError):
        render_plan(spec)


def test_rejects_raw_secrets_and_unversioned_or_wrong_purpose_references():
    for replacement in (
        "secret-value",
        "https://nexa-demo-test.vault.azure.net/secrets/database-url",
        "https://nexa-demo-test.vault.azure.net/secrets/kek-root-secret/" + "b" * 32,
        "https://campuspool.vault.azure.net/secrets/database-url/" + "b" * 32,
    ):
        spec = deepcopy(_spec())
        spec["secret_uris"]["DATABASE_URL"] = replacement
        with pytest.raises(ValueError):
            render_plan(spec)


def test_rejects_cross_subscription_or_cross_project_environment():
    spec = _spec()
    spec["environment_id"] = spec["environment_id"].replace(
        "11111111-1111-1111-1111-111111111111",
        "33333333-3333-3333-3333-333333333333",
    )
    with pytest.raises(ValueError):
        render_plan(spec)
