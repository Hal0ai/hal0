"""scripts/install-matrix/shape.py describes the install matrix shapes as data.

Later bash scripts read ``shape.py get <id>`` with ``read``, never ``eval``,
so every value must be free of newlines and ``=``. Runs the real script under
``python3 -I`` with no network and no root.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "install-matrix"
SHAPE_PY = SCRIPT_DIR / "shape.py"
SHAPES_JSON = SCRIPT_DIR / "shapes.json"

EXPECTED_IDS = [
    "u2604-cpu-unattended",
    "u2604-cpu-terminal",
    "u2604-gpu",
    "u2604-gpu-npu",
    "u2604-gpu-privileged",
    "u2404-cpu",
    "deb13-cpu",
    "fedora43-cpu",
    "u2604-box",
    "vm-u2604-cpu",
    "baremetal-amd",
    "upgrade-18x",
]

LINE = re.compile(r"^SHAPE_[A-Z_]+=[^=\n]*$")


def _run(*args):
    return subprocess.run(
        [sys.executable, "-I", str(SHAPE_PY), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _get(shape_id: str) -> dict[str, str]:
    proc = _run("get", shape_id)
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines())


def test_twelve_rows_in_order():
    proc = _run("list")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == EXPECTED_IDS


@pytest.mark.parametrize("shape_id", EXPECTED_IDS)
def test_get_prints_read_safe_lines(shape_id):
    proc = _run("get", shape_id)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines
    for line in lines:
        assert LINE.match(line), line
    assert [ln.split("=", 1)[0] for ln in lines] == [
        "SHAPE_ROW",
        "SHAPE_TEMPLATE",
        "SHAPE_UNPRIVILEGED",
        "SHAPE_HARDWARE",
        "SHAPE_ENTRY",
        "SHAPE_CORES",
        "SHAPE_MEMORY_MB",
        "SHAPE_DISK_GB",
        "SHAPE_RUNNABLE",
        "SHAPE_ANSWERS",
    ]


def test_unknown_id_exits_2():
    proc = _run("get", "no-such-shape")
    assert proc.returncode == 2
    assert "no-such-shape" in proc.stderr


def test_invalid_file_exits_2(tmp_path):
    data = json.loads(SHAPES_JSON.read_text(encoding="utf-8"))
    data["shapes"][0]["hardware"] = "quantum"
    bad = tmp_path / "shapes.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    for args in (("list",), ("get", "u2604-cpu-unattended")):
        proc = _run("--file", str(bad), *args)
        assert proc.returncode == 2
        assert "hardware" in proc.stderr


def test_cpu_shape_sizes():
    got = _get("u2604-cpu-unattended")
    assert got["SHAPE_CORES"] == "8"
    assert got["SHAPE_MEMORY_MB"] == "16384"
    assert got["SHAPE_DISK_GB"] == "60"


def test_gpu_shape_sizes():
    for shape_id in ("u2604-gpu", "u2604-gpu-npu", "baremetal-amd", "upgrade-18x"):
        got = _get(shape_id)
        assert (got["SHAPE_CORES"], got["SHAPE_MEMORY_MB"], got["SHAPE_DISK_GB"]) == (
            "8",
            "32768",
            "120",
        )


@pytest.mark.parametrize(
    ("shape_id", "answers"),
    [
        ("u2604-cpu-unattended", ""),
        ("u2604-cpu-terminal", "terminal-defaults.tsv"),
        ("u2604-gpu", ""),
        ("u2404-cpu", "cpu-consent.tsv"),
        ("deb13-cpu", "cpu-consent.tsv"),
        ("u2604-box", "cpu-consent.tsv"),
        ("vm-u2604-cpu", ""),
        ("upgrade-18x", ""),
    ],
)
def test_answers_file_by_hardware_and_entry(shape_id, answers):
    assert _get(shape_id)["SHAPE_ANSWERS"] == answers


def test_row_values_match_the_table():
    got = _get("u2604-gpu-privileged")
    assert got["SHAPE_ROW"] == "5"
    assert got["SHAPE_TEMPLATE"] == "ubuntu-26.04-standard"
    assert got["SHAPE_UNPRIVILEGED"] == "0"
    assert got["SHAPE_HARDWARE"] == "gpu"
    assert got["SHAPE_ENTRY"] == "oneliner"
    assert got["SHAPE_RUNNABLE"] == "yes"
    got = _get("upgrade-18x")
    assert got["SHAPE_TEMPLATE"] == "-"
    assert got["SHAPE_ENTRY"] == "update"
    assert got["SHAPE_RUNNABLE"] == "needs-existing-box"
    assert _get("fedora43-cpu")["SHAPE_TEMPLATE"] == "fedora-43-default"
