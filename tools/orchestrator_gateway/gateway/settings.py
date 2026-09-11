"""No network or environment access at module import."""

import json
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def validate_public_url(value: str) -> str:
    u = urlsplit(value)
    if (
        u.scheme != "https"
        or not u.hostname
        or u.username
        or u.password
        or u.query
        or u.fragment
        or u.path not in {"", "/"}
        or u.port not in {None, 443}
    ):
        raise ValueError("GATEWAY_PUBLIC_URL must be an HTTPS origin")
    if u.hostname in {"localhost", "example.com"} or u.hostname.endswith(
        (".example.com", ".invalid")
    ):
        raise ValueError("A real deployed HTTPS origin is required")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    public_url: str
    api_key: str = field(repr=False)
    journal_key: str = field(repr=False)
    journal_path: str
    app_id: str
    installation_id: str
    private_key: str = field(repr=False)
    required_checks: dict[str, int] = field(default_factory=dict)
    merge_enabled: bool = False
    write_limit: int = 20

    def __post_init__(self):
        validate_public_url(self.public_url)
        if (
            min(len(self.api_key), len(self.journal_key)) < 32
            or self.api_key == self.journal_key
        ):
            raise ValueError(
                "Independent secrets of at least 32 characters are required"
            )
        if (
            not self.app_id.isdigit()
            or not self.installation_id.isdigit()
            or not self.private_key
        ):
            raise ValueError("GitHub App credentials are required")
        if not self.journal_path or self.journal_path == ":memory:":
            raise ValueError("A durable journal path is required")
        if any(
            not k or type(v) is not int or v < 1
            for k, v in self.required_checks.items()
        ):
            raise ValueError(
                "Required checks must map names to positive GitHub App IDs"
            )
        if self.merge_enabled and not self.required_checks:
            raise ValueError("Merge requires pinned required checks")

    @classmethod
    def from_env(cls):
        return cls(
            public_url=os.environ["GATEWAY_PUBLIC_URL"].rstrip("/"),
            api_key=os.environ["GATEWAY_API_KEY"],
            journal_key=os.environ["GATEWAY_JOURNAL_HMAC_KEY"],
            journal_path=os.environ["GATEWAY_JOURNAL_PATH"],
            app_id=os.environ["GITHUB_APP_ID"],
            installation_id=os.environ["GITHUB_INSTALLATION_ID"],
            private_key=os.environ["GITHUB_APP_PRIVATE_KEY"],
            required_checks=json.loads(os.environ.get("GATEWAY_REQUIRED_CHECKS", "{}")),
            merge_enabled=os.environ.get("GATEWAY_MERGE_ENABLED", "false") == "true",
        )
