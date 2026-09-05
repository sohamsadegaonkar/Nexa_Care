"""Zero-skip qualification gate for pytest JUnit XML reports.

Validates that an infrastructure qualification run executed tests and that:
1. tests > 0
2. failures == 0
3. errors == 0
4. skipped == 0

If any test was skipped, failed, or errored, or if zero tests were collected,
exits with a non-zero status code and prints the offending test details.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def verify_junit_xml(xml_path: Path | str) -> int:
    path = Path(xml_path)
    if not path.exists():
        print(f"ERROR: JUnit XML file not found at: {path}", file=sys.stderr)
        return 2

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as exc:
        print(f"ERROR: Failed to parse JUnit XML at {path}: {exc}", file=sys.stderr)
        return 2

    # Pytest output can have <testsuites> as root or a single <testsuite>
    if root.tag == "testsuites":
        testsuites = root.findall("testsuite")
    elif root.tag == "testsuite":
        testsuites = [root]
    else:
        print(
            f"ERROR: Unexpected root tag '{root.tag}' in JUnit XML at {path}",
            file=sys.stderr,
        )
        return 2

    total_tests = 0
    total_failures = 0
    total_errors = 0
    total_skipped = 0
    skipped_test_names: list[str] = []
    failed_test_names: list[str] = []

    for suite in testsuites:
        for case in suite.findall("testcase"):
            total_tests += 1
            classname = case.get("classname", "")
            name = case.get("name", "")
            full_name = f"{classname}::{name}" if classname else name

            skipped = case.find("skipped")
            if skipped is not None:
                total_skipped += 1
                msg = skipped.get("message", "no skip message")
                skipped_test_names.append(f"{full_name} (reason: {msg})")

            failure = case.find("failure")
            if failure is not None:
                total_failures += 1
                failed_test_names.append(full_name)

            error = case.find("error")
            if error is not None:
                total_errors += 1
                failed_test_names.append(full_name)

    print(f"=== JUnit Qualification Report: {path.name} ===")
    print(f"  Total tests executed : {total_tests}")
    print(f"  Failures             : {total_failures}")
    print(f"  Errors               : {total_errors}")
    print(f"  Skipped              : {total_skipped}")

    if total_tests == 0:
        print(
            "ERROR: Qualification failed: zero tests were executed.",
            file=sys.stderr,
        )
        return 1

    if total_skipped > 0:
        print(
            f"ERROR: Qualification failed: {total_skipped} test(s) skipped. "
            "A CI job claiming infrastructure qualification must have zero skipped tests.",
            file=sys.stderr,
        )
        for s in skipped_test_names:
            print(f"    - SKIPPED: {s}", file=sys.stderr)
        return 1

    if total_failures > 0 or total_errors > 0:
        print(
            f"ERROR: Qualification failed: {total_failures} failure(s) and "
            f"{total_errors} error(s) detected.",
            file=sys.stderr,
        )
        for f in failed_test_names:
            print(f"    - FAILED/ERRORED: {f}", file=sys.stderr)
        return 1

    print("QUALIFICATION PASSED: All tests executed with zero skips and zero failures.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python scripts/ci/assert_pytest_junit.py <path_to_junit_xml> ...")
        return 2

    exit_code = 0
    for xml_arg in args:
        rc = verify_junit_xml(xml_arg)
        if rc != 0:
            exit_code = rc

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
