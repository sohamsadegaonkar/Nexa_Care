#!/usr/bin/env python3
"""Safely render the portable Milestone 6 ECS task-definition template.

Deployment-specific account IDs, ARNs, domains, CIDRs and secret references
must be supplied at execution time. Rendered task definitions must not be
written inside the repository.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = (
    PROJECT_ROOT
    / "deploy"
    / "ecs"
    / "nexa-care-pilot-task-definition.template.json"
)

PLACEHOLDER_RE = re.compile(r"<[A-Z0-9_]+>")

FORBIDDEN_MATERIAL = (
    "postgresql://",
    "postgresql+asyncpg://",
    "redis://",
    "rediss://",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(PROJECT_ROOT.resolve())
        return True
    except ValueError:
        return False


def _normalize_placeholder(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("placeholder name is empty")
    if not value.startswith("<"):
        value = f"<{value}>"
    if not PLACEHOLDER_RE.fullmatch(value):
        raise ValueError(f"invalid placeholder name: {raw}")
    return value


def _parse_replacements(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}

    for item in items:
        if "=" not in item:
            raise ValueError("--set must use PLACEHOLDER=value")

        raw_name, value = item.split("=", 1)
        name = _normalize_placeholder(raw_name)

        if not value:
            raise ValueError(f"{name}: replacement may not be empty")

        if name in result:
            raise ValueError(f"{name}: supplied more than once")

        result[name] = value

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--values-file",
        type=Path,
        help=(
            "external JSON object containing placeholder/value pairs; "
            "the file must be outside the repository"
        ),
    )
    parser.add_argument(
        "--set",
        dest="replacements",
        action="append",
        default=[],
        metavar="PLACEHOLDER=value",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="fail if any placeholders remain",
    )
    args = parser.parse_args()

    template = args.template.resolve()
    output = args.output.resolve()

    if not template.is_file():
        raise SystemExit("ERROR: task-definition template does not exist")

    if _inside_repo(output):
        raise SystemExit(
            "ERROR: rendered task definitions must be written outside the repository"
        )

    replacements: dict[str, str] = {}

    if args.values_file is not None:
        values_path = args.values_file.resolve()

        if _inside_repo(values_path):
            raise SystemExit(
                "ERROR: deployment values file must be outside the repository"
            )

        if not values_path.is_file():
            raise SystemExit("ERROR: deployment values file does not exist")

        try:
            raw_values = json.loads(values_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"ERROR: deployment values file is invalid JSON: line {exc.lineno}"
            ) from exc

        if not isinstance(raw_values, dict):
            raise SystemExit(
                "ERROR: deployment values file must contain one JSON object"
            )

        for raw_name, raw_value in raw_values.items():
            if not isinstance(raw_name, str):
                raise SystemExit(
                    "ERROR: deployment values keys must be strings"
                )

            name = _normalize_placeholder(raw_name)

            if not isinstance(raw_value, str) or not raw_value:
                raise SystemExit(
                    f"ERROR: {name}: deployment value must be a non-empty string"
                )

            replacements[name] = raw_value

    cli_replacements = _parse_replacements(args.replacements)

    overlap = sorted(set(replacements) & set(cli_replacements))
    if overlap:
        raise SystemExit(
            "ERROR: replacement supplied by both values file and --set: "
            + ", ".join(overlap)
        )

    replacements.update(cli_replacements)

    text = template.read_text(encoding="utf-8-sig")
    available = set(PLACEHOLDER_RE.findall(text))

    unknown = sorted(set(replacements) - available)
    if unknown:
        raise SystemExit(
            "ERROR: replacement names are not present in template: "
            + ", ".join(unknown)
        )

    # Escape replacement values as JSON string content before textual
    # substitution. All template placeholders intentionally live in JSON
    # string fields.
    for placeholder, value in replacements.items():
        encoded = json.dumps(value, ensure_ascii=False)[1:-1]
        text = text.replace(placeholder, encoded)

    # Validate resulting document before writing anything.
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"ERROR: rendered task definition is invalid JSON: line {exc.lineno}"
        ) from exc

    lowered = text.lower()
    for forbidden in FORBIDDEN_MATERIAL:
        if forbidden.lower() in lowered:
            raise SystemExit(
                "ERROR: rendered output appears to contain forbidden raw "
                "credential/connection material"
            )

    unresolved = sorted(set(PLACEHOLDER_RE.findall(text)))

    if args.require_complete and unresolved:
        raise SystemExit(
            "ERROR: unresolved placeholders remain: " + ", ".join(unresolved)
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")

    print("PASS: task definition rendered outside repository")
    print(f"INFO: replacements applied: {len(replacements)}")

    if unresolved:
        print(f"INFO: unresolved placeholders: {len(unresolved)}")
        for placeholder in unresolved:
            print(f"  {placeholder}")
    else:
        print("PASS: no unresolved placeholders remain")

    print("INFO: no AWS operation was performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
