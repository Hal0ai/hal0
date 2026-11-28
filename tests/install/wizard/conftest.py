"""Shared fixtures for the wizard tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hal0.install.wizard.catalog import Catalog, load_catalog
from hal0.install.wizard.facts import Disk, Facts, load_facts

LADDER_ROWS: list[dict[str, object]] = [
    {
        "role": "starter",
        "model": "small-1",
        "label": "Small",
        "lane": "cpu",
        "download_gb": 2.0,
        "min_gpu_pool_gb": None,
        "min_ram_gb": 8,
        "needs_account": False,
    },
    {
        "role": "starter",
        "model": "big-1",
        "label": "Big",
        "lane": "rocm",
        "download_gb": 17.0,
        "min_gpu_pool_gb": 24,
        "min_ram_gb": None,
        "needs_account": True,
    },
]


@pytest.fixture
def catalog_path() -> Path:
    return Path(__file__).resolve().parents[3] / "installer/manifests/setup-catalog.json"


@pytest.fixture
def catalog(catalog_path: Path) -> Catalog:
    return load_catalog(catalog_path)


@pytest.fixture
def ladder_models_path(tmp_path: Path) -> Path:
    path = tmp_path / "setup-models.json"
    path.write_text(json.dumps({"version": 1, "model_ladder": LADDER_ROWS}))
    return path


@pytest.fixture
def ladder_catalog(catalog_path: Path, ladder_models_path: Path) -> Catalog:
    return load_catalog(catalog_path, ladder_models_path)


def _facts(**changes: object) -> Facts:
    base = load_facts(None, disks=[Disk("/", 100.0, 500.0)], ram_gb=32.0)
    return replace(base, podman_runs=True, memory_python=True, npu_device=False, **changes)


@pytest.fixture
def usable_facts() -> Facts:
    return _facts()


@pytest.fixture
def two_disk_facts() -> Facts:
    return _facts(disks=(Disk("/", 100.0, 500.0), Disk("/data", 1500.0, 2000.0)))


@pytest.fixture
def consent_facts() -> Facts:
    return _facts(gpu_outcome="cpu_only_needs_consent", gpu_reason="no_device")


@pytest.fixture
def stop_facts() -> Facts:
    return _facts(gpu_outcome="stop", gpu_reason="broken_group")
