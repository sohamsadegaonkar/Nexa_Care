"""Unit tests for scripts/ci/assert_pytest_junit.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from scripts.ci.assert_pytest_junit import main, verify_junit_xml


@pytest.fixture
def temp_xml_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "junit.xml"


def test_verify_junit_xml_passes_on_clean_report(temp_xml_file: Path):
    content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="3">
    <testcase classname="tests.test_example" name="test_one" time="0.01"/>
    <testcase classname="tests.test_example" name="test_two" time="0.02"/>
    <testcase classname="tests.test_example" name="test_three" time="0.03"/>
  </testsuite>
</testsuites>
"""
    temp_xml_file.write_text(content, encoding="utf-8")
    assert verify_junit_xml(temp_xml_file) == 0
    assert main([str(temp_xml_file)]) == 0


def test_verify_junit_xml_supports_single_testsuite_root(temp_xml_file: Path):
    content = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" errors="0" failures="0" skipped="0" tests="1">
  <testcase classname="tests.test_example" name="test_one" time="0.01"/>
</testsuite>
"""
    temp_xml_file.write_text(content, encoding="utf-8")
    assert verify_junit_xml(temp_xml_file) == 0


def test_verify_junit_xml_fails_when_zero_tests(temp_xml_file: Path):
    content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="0">
  </testsuite>
</testsuites>
"""
    temp_xml_file.write_text(content, encoding="utf-8")
    assert verify_junit_xml(temp_xml_file) == 1
    assert main([str(temp_xml_file)]) == 1


def test_verify_junit_xml_fails_when_skipped_tests_present(temp_xml_file: Path):
    content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="1" tests="2">
    <testcase classname="tests.test_example" name="test_passed" time="0.01"/>
    <testcase classname="tests.test_example" name="test_skipped" time="0.00">
      <skipped message="TEST_DATABASE_URL is not configured" type="pytest.skip"/>
    </testcase>
  </testsuite>
</testsuites>
"""
    temp_xml_file.write_text(content, encoding="utf-8")
    assert verify_junit_xml(temp_xml_file) == 1
    assert main([str(temp_xml_file)]) == 1


def test_verify_junit_xml_fails_when_failures_present(temp_xml_file: Path):
    content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="1">
    <testcase classname="tests.test_example" name="test_failed" time="0.01">
      <failure message="assert False">assert False</failure>
    </testcase>
  </testsuite>
</testsuites>
"""
    temp_xml_file.write_text(content, encoding="utf-8")
    assert verify_junit_xml(temp_xml_file) == 1


def test_verify_junit_xml_fails_when_errors_present(temp_xml_file: Path):
    content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1">
    <testcase classname="tests.test_example" name="test_errored" time="0.01">
      <error message="setup error">RuntimeError</error>
    </testcase>
  </testsuite>
</testsuites>
"""
    temp_xml_file.write_text(content, encoding="utf-8")
    assert verify_junit_xml(temp_xml_file) == 1


def test_verify_junit_xml_returns_2_on_missing_file(temp_xml_file: Path):
    assert verify_junit_xml(temp_xml_file) == 2


def test_verify_junit_xml_returns_2_on_malformed_xml(temp_xml_file: Path):
    temp_xml_file.write_text("<not-closed>", encoding="utf-8")
    assert verify_junit_xml(temp_xml_file) == 2


def test_main_with_no_args_returns_2():
    assert main([]) == 2
