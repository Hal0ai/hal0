"""The wizard's model list (installer/manifests/setup-models.json) must stay in
step with the curated registry and with ``suggest_models``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.install.suggest import suggest_models
from hal0.registry.curated import CURATED_MODELS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gen_setup_models.py"
MANIFEST = ROOT / "installer" / "manifests" / "setup-models.json"


def _rows(role: str) -> list[dict]:
    return [r for r in json.loads(MANIFEST.read_text())["models"] if r["role"] == role]


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


def test_starter_rows_match_registry():
    want = {
        m.id
        for m in CURATED_MODELS
        if m.capability == "chat" and not m.bundle_only and "npu" not in m.tags
    }
    assert {r["id"] for r in _rows("starter")} == want


def test_order_is_largest_first_within_role():
    for role in ("starter", "brain"):
        pools = [r["min_gpu_pool_gb"] for r in _rows(role)]
        assert pools == sorted(pools, reverse=True)
    roles = [r["role"] for r in json.loads(MANIFEST.read_text())["models"]]
    assert roles == sorted(roles, key=("starter", "brain").index)


@pytest.mark.parametrize("pool", [4, 8, 16, 24, 32, 64, 96, 128])
def test_first_fitting_row_matches_suggest(pool):
    fits = [r for r in _rows("starter") if r["min_gpu_pool_gb"] <= pool]
    picks = suggest_models("chat", _hw(pool), limit=1)
    assert fits and picks
    assert fits[0]["id"] == picks[0].model_id


def test_no_npu_or_bundle_only_rows():
    excluded = {m.id for m in CURATED_MODELS if m.bundle_only or "npu" in m.tags}
    assert not excluded & {r["id"] for r in json.loads(MANIFEST.read_text())["models"]}
