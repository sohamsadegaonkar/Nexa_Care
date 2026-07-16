#!/usr/bin/env python3
"""Generate independent patient-auth secrets into ignored runtime env files.

Existing non-placeholder values are preserved. Values are never printed.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

SECRET_NAMES = ("PATIENT_JWT_SECRET", "OTP_RATE_LIMIT_HMAC_SECRET")
PLACEHOLDER = re.compile(r"GENERATED_|change-me|REPLACE_WITH|<[^>]+>", re.I)


def _values(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$", line)
        if match:
            result[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return result


def _usable(value: str | None) -> bool:
    return bool(value and len(value.encode("utf-8")) >= 32 and not PLACEHOLDER.search(value))


def _replace(path: Path, replacements: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written: set[str] = set()
    output: list[str] = []
    for line in lines:
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in replacements:
            name = match.group(1)
            output.append(f"{name}={replacements[name]}")
            written.add(name)
        else:
            output.append(line)
    for name, value in replacements.items():
        if name not in written:
            output.append(f"{name}={value}")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output) + "\n")
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely provision patient-auth runtime secrets")
    parser.add_argument("env_files", nargs="*", default=[".env.alpha", ".env"])
    args = parser.parse_args()
    paths = [Path(item).resolve() for item in args.env_files]

    for path in paths:
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(path)],
            check=False,
        ).returncode == 0
        if not ignored:
            raise SystemExit(f"Refusing to write non-ignored environment file: {path.name}")

    current = {path: _values(path) for path in paths}
    selected: dict[str, str] = {}
    for name in SECRET_NAMES:
        existing = {values[name] for values in current.values() if _usable(values.get(name))}
        if len(existing) > 1:
            raise SystemExit(f"Conflicting existing {name} values; no files changed")
        selected[name] = next(iter(existing), secrets.token_urlsafe(48))

    for path in paths:
        _replace(path, selected)
        print(f"updated={path.name} variables={','.join(SECRET_NAMES)} values=redacted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
