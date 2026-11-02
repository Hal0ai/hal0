"""``--summary-json`` records the component choices (installer component picker, A4).

The python heredoc that writes the summary is lifted out of install.sh and run
directly, the same way the sibling installer tests slice bash fragments. The
``components`` key is additive to ``hal0.install-summary.v1``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
_START = "<<'PYEOF' || warn \"could not write --summary-json"


def _summary_py() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    after = text.split(_START, 1)[1]
    return after.split("\nPYEOF", 1)[0].split("\n", 1)[1]


@pytest.fixture
def run_summary_py(tmp_path: Path):
    def _run(components: str) -> dict:
        out = tmp_path / "out" / "summary.json"
        env = {**os.environ, "HAL0_COMPONENTS_BLOB": components}
        args = [
            str(out), "1.2.3", "0", "0", "/opt/hal0", "/models",
            "0.0.0.0", "8080", "3001", "cls", "Class", "none", "0", "0", "5",
        ]
        subprocess.run(
            [sys.executable, "-I", "-", *args],
            input=_summary_py(), text=True, env=env, check=True,
            capture_output=True,
        )
        return json.loads(out.read_text(encoding="utf-8"))

    return _run


def test_summary_json_has_components_block(run_summary_py) -> None:
    data = run_summary_py(components="hermes=skipped\x1eopenwebui=selected\x1e")
    assert data["schema"] == "hal0.install-summary.v1"
    assert data["components"] == {"hermes": "skipped", "openwebui": "selected"}


def test_summary_json_components_keep_unavailable_reason(run_summary_py) -> None:
    data = run_summary_py(components="pi=unavailable:needs npm (Node.js)\x1e")
    assert data["components"] == {"pi": "unavailable:needs npm (Node.js)"}


def test_summary_json_components_empty_when_blob_missing(run_summary_py) -> None:
    assert run_summary_py(components="")["components"] == {}


# ── bash half: blob build + "Components skipped" summary line ───────────────

_SKIP_START = "# Components the operator (or a flag) left out"
_SKIP_END = "SUMMARY_LINES+=(\n    \"\"\n    \"$(printf '%sNext steps"
_BLOB_START = "    _summary_components_blob=\"\""
_BLOB_END = 'export HAL0_COMPONENTS_BLOB="${_summary_components_blob}"'
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _fragment(start: str, end: str, *, include_end: bool = False) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    head = text.index(start)
    tail = text.index(end, head)
    return text[head : tail + (len(end) if include_end else 0)]


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-c", "set -euo pipefail\n" + script],
        capture_output=True, text=True, timeout=60,
    )


def _skipped_lines(states: str, *, upgrade: int = 0) -> list[str]:
    proc = _bash(
        "declare -A COMPONENT_STATES=()\n"
        + states + "\n"
        + f"IS_UPGRADE={upgrade}\nDIM=''\nRST=''\nSUMMARY_LINES=()\n"
        + _fragment(_SKIP_START, _SKIP_END)
        + '\nfor l in "${SUMMARY_LINES[@]}"; do printf "L|%s\\n" "$l"; done\n'
    )
    assert proc.returncode == 0, proc.stderr
    return [_ANSI.sub("", ln[2:]) for ln in proc.stdout.splitlines() if ln.startswith("L|")]


def test_skipped_line_labels_and_order() -> None:
    lines = _skipped_lines(
        "COMPONENT_STATES[brain]=skipped; COMPONENT_STATES[openwebui]=skipped;"
        "COMPONENT_STATES[pi]=skipped; COMPONENT_STATES[hermes]=selected"
    )
    assert lines == ["", "Components skipped: Open WebUI, Pi agent, Brain model"]


def test_skipped_line_omitted_when_nothing_skipped() -> None:
    assert _skipped_lines("COMPONENT_STATES[hermes]=selected") == []


def test_skipped_line_omitted_on_upgrade() -> None:
    assert _skipped_lines("COMPONENT_STATES[pi]=skipped", upgrade=1) == []


def test_skipped_line_ignores_unavailable_rows() -> None:
    lines = _skipped_lines(
        "COMPONENT_STATES[pi]='unavailable:needs npm (Node.js)'; COMPONENT_STATES[brain]=skipped"
    )
    assert lines == ["", "Components skipped: Brain model"]


def test_skipped_line_empty_array_under_strict_mode() -> None:
    assert _skipped_lines("") == []


def test_skipped_line_wraps_inside_box_width() -> None:
    lines = _skipped_lines(
        "for c in openwebui comfyui hermes pi hindsight brain; do COMPONENT_STATES[$c]=skipped; done"
    )
    body = lines[1:]
    assert len(body) > 1
    assert body[0].startswith("Components skipped: ")
    assert all(len(ln) <= 76 for ln in body)
    assert all(ln.startswith(" " * 20) for ln in body[1:])
    flat = " ".join(ln.strip().removeprefix("Components skipped: ") for ln in body)
    assert flat == "Open WebUI, ComfyUI, Hermes agent, Pi agent, Hindsight memory, Brain model"


def _blob(states: str) -> str:
    proc = _bash(
        "declare -A COMPONENT_STATES=()\n" + states + "\n"
        + _fragment(_BLOB_START, _BLOB_END, include_end=True)
        + '\nprintf "%s" "$HAL0_COMPONENTS_BLOB"\n'
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_blob_round_trips_through_python_slice(run_summary_py) -> None:
    blob = _blob(
        "COMPONENT_STATES[hermes]=skipped; COMPONENT_STATES[openwebui]=selected;"
        "COMPONENT_STATES[pi]='unavailable:needs npm (Node.js)'"
    )
    assert run_summary_py(components=blob)["components"] == {
        "hermes": "skipped",
        "openwebui": "selected",
        "pi": "unavailable:needs npm (Node.js)",
    }


def test_blob_empty_array_under_strict_mode(run_summary_py) -> None:
    blob = _blob("")
    assert blob == ""
    assert run_summary_py(components=blob)["components"] == {}
