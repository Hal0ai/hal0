"""Shared fixtures for the wizard tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.install.wizard.catalog import Catalog, load_catalog

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
