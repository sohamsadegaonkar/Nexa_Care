"""Return short-lived AWS credentials to the local SDK credential process."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from uuid import UUID


def credentials(environment: Mapping[str, str]) -> dict:
    """Use only the assigned Azure identity and the regional unsigned STS API."""
    from azure.identity import ManagedIdentityCredential
    from botocore import UNSIGNED
    from botocore.config import Config
    from botocore.session import Session

    role = environment["NEXA_AZURE_AWS_ROLE_ARN"]
    token_resource = environment["NEXA_AZURE_AWS_TOKEN_RESOURCE"]
    client_id = str(UUID(environment["NEXA_AZURE_AWS_IDENTITY_CLIENT_ID"]))
    if environment.get("ENVIRONMENT") != "staging":
        raise ValueError("STAGING_RUNTIME_REQUIRED")
    if not re.fullmatch(r"arn:aws:iam::[0-9]{12}:role/nexa-stg-[a-z0-9-]+", role):
        raise ValueError("STAGING_ROLE_REQUIRED")
    if not token_resource.startswith("api://") or token_resource != f"api://{UUID(token_resource[6:])}":
        raise ValueError("STAGING_AUDIENCE_INVALID")

    with ManagedIdentityCredential(client_id=client_id) as identity:
        token = identity.get_token(f"{token_resource}/.default").token
    sts = Session().create_client(
        "sts",
        region_name="ap-south-1",
        config=Config(
            signature_version=UNSIGNED,
            connect_timeout=5,
            read_timeout=10,
            retries={"total_max_attempts": 2},
        ),
    )
    try:
        response = sts.assume_role_with_web_identity(
            RoleArn=role,
            RoleSessionName="nexa-azure-staging",
            WebIdentityToken=token,
            DurationSeconds=3600,
        )["Credentials"]
    finally:
        sts.close()

    return {
        "Version": 1,
        "AccessKeyId": response["AccessKeyId"],
        "SecretAccessKey": response["SecretAccessKey"],
        "SessionToken": response["SessionToken"],
        "Expiration": response["Expiration"].isoformat(),
    }


def main() -> int:
    try:
        result = credentials(os.environ)
    except Exception:
        # The SDK captures stdout. Never write tokens, URLs or errors to stderr.
        print("AZURE_STAGING_AWS_CREDENTIALS_UNAVAILABLE", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
