from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from hal0.lifecycle.catalog import LifecycleCatalog

ROOT = Path(__file__).parents[2]
COMPILER = ROOT / "scripts" / "compile-lifecycle-catalog.py"


def _compile(output: Path, *mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(COMPILER), *mode, "--output", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_compile_refuses_absent_and_stale_output_then_writes_canonical_json(tmp_path: Path) -> None:
    output = tmp_path / "catalog.json"
    absent = _compile(output, "--check")
    assert absent.returncode == 1
    assert "absent" in absent.stderr

    written = _compile(output, "--write")
    assert written.returncode == 0
    first = output.read_bytes()
    assert first.endswith(b"\n")
    assert json.loads(first)["generated_format"] == "canonical-json-v1"

    output.write_text("{}\n")
    stale = _compile(output, "--check")
    assert stale.returncode == 1
    assert "stale" in stale.stderr

    assert _compile(output, "--write").returncode == 0
    assert output.read_bytes() == first
    assert _compile(output, "--check").returncode == 0


def test_bundled_catalog_loads_only_compiled_runtime_document() -> None:
    catalog = LifecycleCatalog.load_bundled()
    assert catalog.validate().errors == ()
