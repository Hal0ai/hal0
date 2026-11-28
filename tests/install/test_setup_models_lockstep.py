"""The wizard's model list (installer/manifests/setup-models.json) must stay in
step with the installer's own model lists."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hal0.comfyui.catalog import CATALOG
from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.install.agent_model import AGENT_MODEL_IDS, agent_model_for_hardware
from hal0.install.brain_model import BRAIN_MODEL_IDS
from hal0.registry.curated import get_curated

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gen_setup_models.py"
MANIFEST = ROOT / "installer" / "manifests" / "setup-models.json"


def _all() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["model_ladder"]


def _rows(role: str) -> list[dict]:
    return [r for r in _all() if r["role"] == role]


def _hw(pool_gb: float) -> HardwareInfo:
    mb = int(pool_gb * 1024)
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=mb,
        ram_available_mb=mb,
        unified_memory_mb=mb,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
    )


def test_committed_file_is_current():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, env=env
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_starter_rows_are_the_installer_ladder():
    assert [r["model"] for r in _rows("starter")] == list(AGENT_MODEL_IDS)


def test_rows_keep_source_order():
    assert [r["model"] for r in _rows("starter")] == list(AGENT_MODEL_IDS)
    assert [r["model"] for r in _rows("brain")] == list(BRAIN_MODEL_IDS)
    assert [r["model"] for r in _rows("image")] == [
        m.id for m in CATALOG.values() if m.api_renderable
    ]
    roles = [r["role"] for r in _all()]
    assert roles == sorted(roles, key=("starter", "brain", "image").index)


@pytest.mark.parametrize("pool", [4, 8, 16, 24, 25, 32, 64, 96, 128])
def test_first_fitting_row_is_what_the_installer_picks(pool):
    fits = [r for r in _rows("starter") if r["min_gpu_pool_gb"] <= pool + 0.01]
    picked = agent_model_for_hardware(_hw(pool))
    assert (fits[0]["model"] if fits else None) == picked
    if pool < min(r["min_gpu_pool_gb"] for r in _rows("starter")):
        assert picked is None and not fits


def test_every_row_has_a_curated_entry():
    for r in _all():
        if r["role"] != "image":
            assert get_curated(r["model"]) is not None, r["model"]


def test_image_rows_match_the_installer_catalogue():
    want = [m.id for m in CATALOG.values() if m.api_renderable]
    assert [r["model"] for r in _rows("image")] == want
    assert want


def test_every_row_has_role_model_label():
    for r in _all():
        for key in ("role", "model", "label"):
            assert isinstance(r.get(key), str) and r[key], (key, r)


def test_no_null_values():
    for r in _all():
        assert None not in r.values(), r


READER = ROOT / "installer" / "lib" / "answers_reader.py"


def _read(tmp_path: Path, doc: dict) -> subprocess.CompletedProcess:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", str(READER), "--origin", "file", str(path)],
        capture_output=True,
        text=True,
    )


def _cases() -> list[tuple[dict, str]]:
    out: list[tuple[dict, str]] = []
    for r in _all():
        m = r["model"]
        if r["role"] == "starter":
            out.append(({"models": {"starter": m}}, f"HAL0_AGENT_MODEL={m}"))
        elif r["role"] == "brain":
            out.append(({"models": {"brain": m}}, f"HAL0_BRAIN_MODEL={m}"))
        else:
            gen = {"mode": "scaffold_and_download", "models": [m]}
            out.append(({"gen": gen}, f"HAL0_IMAGE_MODELS={m}"))
    return out


def test_installer_reader_accepts_every_ladder_id(tmp_path):
    cases = _cases()
    assert cases
    for body, want in cases:
        proc = _read(tmp_path, {"version": 1, **body})
        assert proc.returncode == 0, (want, proc.stderr)
        assert want in proc.stdout.splitlines(), (want, proc.stdout)
