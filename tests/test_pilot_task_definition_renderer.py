from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_pilot_task_definition.py"


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _template(tmp_path: Path, text: str = '{"a":"<ONE>","b":"<TWO>"}') -> Path:
    path = tmp_path / "template.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_default_template_is_json() -> None:
    assert json.loads(
        (ROOT / "deploy/ecs/nexa-care-pilot-task-definition.template.json").read_text(
            encoding="utf-8-sig"
        )
    )


def test_set_values_escape_json_and_require_complete(tmp_path: Path) -> None:
    output = tmp_path / "rendered.json"
    result = _run(
        tmp_path,
        "--template",
        str(_template(tmp_path)),
        "--output",
        str(output),
        "--set",
        'ONE=a"b',
        "--set",
        "TWO=value",
        "--require-complete",
    )
    assert result.returncode == 0
    assert json.loads(output.read_text())["a"] == 'a"b'


def test_values_file_and_negative_rejections(tmp_path: Path) -> None:
    template, output = _template(tmp_path), tmp_path / "out.json"
    values = tmp_path / "values.json"
    values.write_text('{"ONE":"x","TWO":"y"}', encoding="utf-8")
    assert (
        _run(
            tmp_path,
            "--template",
            str(template),
            "--output",
            str(output),
            "--values-file",
            str(values),
            "--require-complete",
        ).returncode
        == 0
    )
    malformed = tmp_path / "bad.json"
    malformed.write_text("{", encoding="utf-8")
    assert (
        _run(
            tmp_path,
            "--template",
            str(template),
            "--output",
            str(output),
            "--values-file",
            str(malformed),
        ).returncode
        != 0
    )
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    assert (
        _run(
            tmp_path,
            "--template",
            str(template),
            "--output",
            str(output),
            "--values-file",
            str(array),
        ).returncode
        != 0
    )
    assert (
        _run(
            tmp_path,
            "--template",
            str(template),
            "--output",
            str(output),
            "--set",
            "UNKNOWN=x",
        ).returncode
        != 0
    )
    assert (
        _run(
            tmp_path,
            "--template",
            str(template),
            "--output",
            str(output),
            "--set",
            "ONE=x",
            "--set",
            "ONE=y",
        ).returncode
        != 0
    )
    assert (
        _run(
            tmp_path,
            "--template",
            str(template),
            "--output",
            str(output),
            "--values-file",
            str(values),
            "--set",
            "ONE=x",
        ).returncode
        != 0
    )


def test_rejects_repo_paths_unresolved_and_forbidden_material(tmp_path: Path) -> None:
    template, output = _template(tmp_path), tmp_path / "out.json"
    assert (
        _run(
            tmp_path,
            "--template",
            str(template),
            "--output",
            str(output),
            "--set",
            "ONE=x",
            "--require-complete",
        ).returncode
        != 0
    )
    assert (
        _run(
            tmp_path,
            "--template",
            str(template),
            "--output",
            str(ROOT / "forbidden.json"),
            "--set",
            "ONE=x",
        ).returncode
        != 0
    )
    repo_values = ROOT / "deploy/ecs/pilot-runtime-contract.template.json"
    assert (
        _run(
            tmp_path,
            "--template",
            str(template),
            "--output",
            str(output),
            "--values-file",
            str(repo_values),
        ).returncode
        != 0
    )
    for value in (
        "postgresql://synthetic",
        "rediss://synthetic",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert (
            _run(
                tmp_path,
                "--template",
                str(_template(tmp_path, '{"a":"<ONE>"}')),
                "--output",
                str(output),
                "--set",
                f"ONE={value}",
            ).returncode
            != 0
        )
