"""hal0_apply_answers: run the reader, check names, export all or nothing."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANSWERS_SH = ROOT / "installer" / "lib" / "answers.sh"
READER = ROOT / "installer" / "lib" / "answers_reader.py"


def _bash(script: str, tmp_path: Path, reader: Path | None = None) -> subprocess.CompletedProcess[str]:
    override = f'_HAL0_ANSWERS_READER="{reader}"' if reader else ""
    full = f"""
set -u
PY="{sys.executable}"
source "{ANSWERS_SH}"
{override}
{script}
"""
    return subprocess.run(
        ["bash", "-c", full], capture_output=True, text=True, check=False, cwd=tmp_path
    )


def _doc(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_exports_listed_names(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1, "memory": {"enabled": False}, "network": {"port": 9000}})
    proc = _bash(
        f'hal0_apply_answers "{doc}" file; echo "rc=$?"; '
        'echo "$HAL0_NONINTERACTIVE $HAL0_SKIP_HINDSIGHT $HAL0_PORT $HAL0_ANSWERS_APPLIED"; '
        'bash -c \'echo "child:$HAL0_PORT"\'',
        tmp_path,
    )
    assert proc.stdout.splitlines() == ["rc=0", "1 1 9000 1", "child:9000"], proc.stderr


def test_name_off_the_list_aborts(tmp_path: Path) -> None:
    fake = tmp_path / "fake_reader.py"
    fake.write_text(
        "print('HAL0_PORT=1234')\nprint('PATH=/tmp/x')\n", encoding="utf-8"
    )
    proc = _bash(
        'before="$PATH"; hal0_apply_answers /x/doc.json file; echo "rc=$?"; '
        '[[ "$PATH" == "$before" ]] && echo path-same; echo "port=${HAL0_PORT:-unset}"',
        tmp_path,
        reader=fake,
    )
    assert proc.stdout.splitlines() == ["rc=1", "path-same", "port=unset"], proc.stderr
    assert "not allowed" in proc.stderr


def test_terminal_name_refused_for_file_origin(tmp_path: Path) -> None:
    fake = tmp_path / "fake_reader.py"
    fake.write_text("print('HAL0_HERMES_TERMINAL=1')\n", encoding="utf-8")
    proc = _bash(
        'hal0_apply_answers /x/doc.json file; echo "rc=$?"; echo "${HAL0_HERMES_TERMINAL:-unset}"',
        tmp_path,
        reader=fake,
    )
    assert proc.stdout.splitlines() == ["rc=1", "unset"]


def test_value_with_spaces_and_equals_survives(tmp_path: Path) -> None:
    path = "/mnt/my models/a=b"
    doc = _doc(tmp_path, {"version": 1, "model_store": {"path": path, "flm_path": "/m/x="}})
    proc = _bash(
        f'hal0_apply_answers "{doc}" file; printf "[%s]\\n[%s]\\n" "$HAL0_MODELS_DIR" '
        '"$HAL0_FLM_MODELS_DIR"',
        tmp_path,
    )
    assert proc.stdout.splitlines() == [f"[{path}]", "[/m/x=]"], proc.stderr


def test_shell_syntax_in_value_is_data(tmp_path: Path) -> None:
    # Even if a reader let such a value through, bash only ever exports it.
    marker = tmp_path / "pwned"
    value = f"/mnt/$(touch {marker})`touch {marker}`;\"'*"
    fake = tmp_path / "fake_reader.py"
    fake.write_text(f"print('HAL0_MODELS_DIR=' + {value!r})\n", encoding="utf-8")
    proc = _bash(
        'hal0_apply_answers /x/doc.json file; printf "%s" "$HAL0_MODELS_DIR"',
        tmp_path,
        reader=fake,
    )
    assert proc.stdout == value
    assert not marker.exists()


def test_reader_rejection_exports_nothing_and_passes_messages(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1, "memory": {"enabled": False}, "network": {"port": 0}})
    proc = _bash(
        f'hal0_apply_answers "{doc}" file; echo "rc=$?"; '
        'echo "${HAL0_NONINTERACTIVE:-unset}/${HAL0_SKIP_HINDSIGHT:-unset}"',
        tmp_path,
    )
    assert proc.stdout.splitlines() == ["rc=2", "unset/unset"]
    assert "network.port" in proc.stderr


def test_bad_origin_and_missing_python(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1})
    proc = _bash(f'hal0_apply_answers "{doc}" bogus; echo "rc=$?"', tmp_path)
    assert proc.stdout.strip() == "rc=1"
    proc = _bash(f'PY=""; hal0_apply_answers "{doc}" file; echo "rc=$?"', tmp_path)
    assert proc.stdout.strip() == "rc=1"


def test_collect_answers_runs_apply_for_a_file(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1, "apps": {"hermes": {"terminal_tool": True}}})
    proc = _bash(
        f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; echo "rc=$?"; '
        'echo "$HAL0_ANSWERS_APPLIED ${HAL0_HERMES_TERMINAL:-unset}"',
        tmp_path,
    )
    assert proc.stdout.splitlines() == ["rc=0", "1 unset"], proc.stderr


def test_allow_lists_match() -> None:
    sh = ANSWERS_SH.read_text(encoding="utf-8")
    block = re.search(r"_HAL0_ANSWER_NAMES=\(\n(.*?)\n\)", sh, re.S)
    assert block
    sh_names = block.group(1).split()
    py = READER.read_text(encoding="utf-8")
    tup = re.search(r"ALLOWED_NAMES = \(\n(.*?)\n\)", py, re.S)
    assert tup
    py_names = re.findall(r'"(HAL0_[A-Z0-9_]+)"', tup.group(1))
    assert sh_names == py_names
    assert len(set(sh_names)) == len(sh_names) == 23
