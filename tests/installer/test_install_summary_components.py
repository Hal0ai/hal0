"""``--summary-json`` records the component choices (installer component picker, A4).

The python heredoc that writes the summary is lifted out of install.sh and run
directly, the same way the sibling installer tests slice bash fragments. The
``components`` key is additive to ``hal0.install-summary.v1``.
"""

from __future__ import annotations

import json
import os
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
