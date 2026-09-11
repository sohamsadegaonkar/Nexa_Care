"""Server-owned repository and content boundaries."""

import re

REPOSITORY = "sohamsadegaonkar/Nexa_Care"
MAIN = "main"
PREFIX = "orchestrator/"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_FILE_BYTES = 48_000
MAX_READ_BYTES = 1_000_000
TEXT_EXTENSIONS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".css",
        ".html",
        ".sql",
        ".sh",
        ".ps1",
        ".xml",
        ".svg",
        ".cfg",
        ".example",
    }
)


class Denied(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


def sha(value: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise Denied("INVALID_SHA", 422)
    return value


def branch(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"orchestrator/[a-z0-9][a-z0-9/_-]{0,100}", value
    ):
        raise Denied("FEATURE_BRANCH_REQUIRED", 403)
    if any(not part or part.startswith("-") for part in value.split("/")):
        raise Denied("INVALID_BRANCH", 422)
    return value


def ref(value: str) -> str:
    if value == MAIN or SHA_RE.fullmatch(value):
        return value
    return branch(value)


def path(value: str, *, write: bool = False) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", value):
        raise Denied("PATH_DENIED", 403)
    parts = value.split("/")
    if any(p in {"", ".", ".."} or p.startswith("-") for p in parts):
        raise Denied("PATH_DENIED", 403)
    lower = [p.lower() for p in parts]
    forbidden = {
        ".git",
        ".ssh",
        ".aws",
        ".azure",
        ".gcloud",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
    }
    if any(
        p in forbidden
        or re.search(
            r"secret|credential|private.?key|patient.?data|clinical.?data|dump", p
        )
        for p in lower
    ):
        raise Denied("PATH_DENIED", 403)
    if any(p.startswith(".env") for p in lower):
        raise Denied("PATH_DENIED", 403)
    name = lower[-1]
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if suffix not in TEXT_EXTENSIONS and name not in {
        "dockerfile",
        "makefile",
        ".gitignore",
        ".dockerignore",
        ".gitattributes",
    }:
        raise Denied("TEXT_FILE_REQUIRED", 403)
    # Automation cannot rewrite its own controls, CI, or deployment credentials.
    if write and (
        value.startswith(
            (".github/", "deploy/", "tools/orchestrator_gateway/", "docs/governance/")
        )
        or name == "agents.md"
    ):
        raise Denied("CONTROL_PATH_WRITE_DENIED", 403)
    return value


def clean_text(value: str, *, max_bytes: int = MAX_FILE_BYTES) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > max_bytes
        or "\x00" in value
    ):
        raise Denied("TEXT_SIZE_OR_ENCODING_DENIED", 422)
    # Defense in depth; this is not a complete DLP system. Repository must not
    # contain real patient records or secrets anywhere, including innocuous paths.
    if re.search(
        r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b",
        value,
    ):
        raise Denied("SENSITIVE_CONTENT_DENIED", 403)
    return value
