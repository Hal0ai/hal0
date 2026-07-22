from __future__ import annotations

import copy
import tomllib
from pathlib import Path

import pytest

from hal0.lifecycle.catalog import LifecycleCatalog


class CatalogSource:
    def __init__(self, documents: dict[str, dict[str, object]]) -> None:
        self.documents = documents

    def runner(self, runner_id: str) -> dict[str, object]:
        return next(row for row in self.documents["runners"]["runners"] if row["id"] == runner_id)

    def package(self, package_id: str) -> dict[str, object]:
        return next(
            row for row in self.documents["packages"]["packages"] if row["id"] == package_id
        )


@pytest.fixture
def catalog_source() -> CatalogSource:
    data = Path(__file__).parents[2] / "src" / "hal0" / "lifecycle" / "data"
    documents = {
        name: tomllib.loads((data / f"{name}.toml").read_text())
        for name in ("packages", "runners", "models", "profiles", "bootstrap")
    }
    return CatalogSource(copy.deepcopy(documents))


@pytest.fixture
def catalog(catalog_source: CatalogSource) -> LifecycleCatalog:
    return LifecycleCatalog.from_documents(catalog_source.documents)
