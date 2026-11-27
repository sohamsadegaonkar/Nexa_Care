import sys
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from scripts import azure_staging_aws_credentials, run_azure_staging_migrations
from scripts.azure_staging_targets import check_database, check_redis


@pytest.fixture
def staging_environment() -> dict[str, str]:
    postgres = "nexa-stg-1234567890abc.postgres.database.azure.com"
    redis = "nexa-stg-1234567890abc.malaysiawest.redis.azure.net"
    return {
        "ENVIRONMENT": "staging",
        "NEXA_AZURE_STAGING_POSTGRES_HOST": postgres,
        "NEXA_AZURE_STAGING_REDIS_HOST": redis,
        "DATABASE_URL": f"postgresql+asyncpg://nexa_api_runtime:fixture@{postgres}/nexacare_staging",
        "MIGRATION_DATABASE_URL": f"postgresql+asyncpg://nexa_migrator:fixture@{postgres}/nexacare_staging",
        "UPSTASH_REDIS_URL": f"rediss://:fixture@{redis}:10000/0?ssl_check_hostname=true",
    }


def test_distinct_staging_database_roles_and_verified_redis(staging_environment):
    check_database(staging_environment, migration=False)
    check_database(staging_environment, migration=True)
    check_redis(staging_environment)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("nexa_migrator", "nexa_api_runtime"),
        ("nexa-stg-1234567890abc", "foreign-pg"),
        ("nexacare_staging", "nexacare_pilot"),
        ("/nexacare_staging", "/nexacare_staging?sslmode=disable"),
    ],
)
def test_wrong_migration_database_is_rejected(staging_environment, old, new):
    staging_environment["MIGRATION_DATABASE_URL"] = staging_environment[
        "MIGRATION_DATABASE_URL"
    ].replace(old, new)
    with pytest.raises(ValueError, match="AZURE_STAGING_DATABASE_TARGET_REJECTED"):
        check_database(staging_environment, migration=True)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("rediss://", "redis://"),
        ("ssl_check_hostname=true", "ssl_check_hostname=false"),
        ("ssl_check_hostname=true", "ssl_check_hostname=true&ssl_cert_reqs=none"),
        ("nexa-stg-1234567890abc", "foreign-cache"),
    ],
)
def test_redis_host_and_tls_are_bound(staging_environment, old, new):
    staging_environment["UPSTASH_REDIS_URL"] = staging_environment["UPSTASH_REDIS_URL"].replace(
        old, new
    )
    with pytest.raises(ValueError, match="AZURE_STAGING_REDIS_TARGET_REJECTED"):
        check_redis(staging_environment)


@pytest.mark.parametrize("region", ["centralindia", "southeastasia"])
def test_matching_redis_hosts_in_other_regions_are_rejected(staging_environment, region):
    for variable in ("NEXA_AZURE_STAGING_REDIS_HOST", "UPSTASH_REDIS_URL"):
        staging_environment[variable] = staging_environment[variable].replace(
            ".malaysiawest.", f".{region}."
        )
    with pytest.raises(ValueError, match="AZURE_STAGING_REDIS_TARGET_REJECTED"):
        check_redis(staging_environment)


def test_migration_runner_checks_the_target_before_enabling_the_lock(
    monkeypatch, staging_environment
):
    for name, value in staging_environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("NEXA_AZURE_STAGING_MIGRATION_LOCK", raising=False)
    migration = MagicMock(return_value=0)
    monkeypatch.setattr(run_azure_staging_migrations.run_pilot_migrations, "main", migration)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert run_azure_staging_migrations.main() == 1
    migration.assert_not_called()
    monkeypatch.setenv("ENVIRONMENT", "staging")
    assert run_azure_staging_migrations.main() == 0
    migration.assert_called_once_with()


def test_federation_exchange_uses_only_the_assigned_identity(monkeypatch):
    import botocore.session

    identity = MagicMock()
    identity.return_value.__enter__.return_value.get_token.return_value.token = "fixture"
    sts = MagicMock()
    sts.assume_role_with_web_identity.return_value = {
        "Credentials": {
            "AccessKeyId": "fixture-id",
            "SecretAccessKey": "fixture-key",
            "SessionToken": "fixture-session",
            "Expiration": datetime(2026, 11, 24, 8, tzinfo=UTC),
        }
    }
    session = MagicMock()
    session.return_value.create_client.return_value = sts
    azure_identity = ModuleType("azure.identity")
    azure_identity.ManagedIdentityCredential = identity
    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)
    monkeypatch.setattr(botocore.session, "Session", session)
    environment = {
        "ENVIRONMENT": "staging",
        "NEXA_AZURE_AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/nexa-stg-app",
        "NEXA_AZURE_AWS_TOKEN_RESOURCE": "api://11111111-1111-4111-a111-111111111111",
        "NEXA_AZURE_AWS_IDENTITY_CLIENT_ID": "22222222-2222-4222-a222-222222222222",
    }
    result = azure_staging_aws_credentials.credentials(environment)
    identity.assert_called_once_with(client_id=environment["NEXA_AZURE_AWS_IDENTITY_CLIENT_ID"])
    identity.return_value.__enter__.return_value.get_token.assert_called_once_with(
        f"{environment['NEXA_AZURE_AWS_TOKEN_RESOURCE']}/.default"
    )
    assert session.return_value.create_client.call_args.kwargs["region_name"] == "ap-south-1"
    assert sts.assume_role_with_web_identity.call_args.kwargs["RoleArn"] == environment[
        "NEXA_AZURE_AWS_ROLE_ARN"
    ]
    assert result["Version"] == 1
    assert result["Expiration"] == "2026-11-24T08:00:00+00:00"

    with pytest.raises(ValueError, match="STAGING_RUNTIME_REQUIRED"):
        azure_staging_aws_credentials.credentials({**environment, "ENVIRONMENT": "production"})
    assert sts.assume_role_with_web_identity.call_count == 1
