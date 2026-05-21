"""Tests for /api/config/models — GET defaults + PUT update/validate."""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """Per-test app whose lifespan resolves paths under tmp_hal0_home."""
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def test_get_models_config_returns_defaults(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    r = isolated_client.get("/api/config/models")
    assert r.status_code == 200, r.text
    body = r.json()
    # Defaults track HAL0_HOME (set by isolated_client → tmp_hal0_home).
    # In production (HAL0_HOME unset) these resolve to /var/lib/hal0/models.
    expected_models_dir = f"{tmp_hal0_home}/var-lib/hal0/models"
    assert body["roots"] == [expected_models_dir]
    assert body["pull_root"] == expected_models_dir
    assert body["auto_scan_on_start"] is True
    assert ".gguf" in body["file_extensions"]
    assert ".safetensors" in body["file_extensions"]


def test_put_models_config_persists(
    isolated_client: TestClient, tmp_hal0_home: str, tmp_path: Path
) -> None:
    new_root = tmp_path / "extra-models"
    new_root.mkdir()
    r = isolated_client.put(
        "/api/config/models",
        json={"roots": [str(new_root)], "auto_scan_on_start": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # pull_root is auto-included in roots so a save that only mentioned
    # an extra root still leaves the pull destination scannable.
    assert str(new_root) in body["roots"]
    assert body["pull_root"] in body["roots"]
    assert body["auto_scan_on_start"] is False
    assert "scan" in body
    assert isinstance(body["scan"]["added"], list)

    toml_path = Path(tmp_hal0_home) / "etc" / "hal0" / "hal0.toml"
    assert toml_path.exists(), f"expected hal0.toml at {toml_path}"
    parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert str(new_root) in parsed["models"]["roots"]
    assert parsed["models"]["auto_scan_on_start"] is False


def test_put_models_config_relative_path_rejected(isolated_client: TestClient) -> None:
    r = isolated_client.put(
        "/api/config/models",
        json={"roots": ["relative/path"]},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "config.invalid"
    details = body["error"]["details"]
    # Pydantic surfaces the failed field path; we don't pin the exact key
    # shape (depends on pydantic version) — just that we got something.
    assert details


def test_put_pull_root_persists_and_is_auto_scanned(
    isolated_client: TestClient, tmp_hal0_home: str, tmp_path: Path
) -> None:
    new_dir = tmp_path / "mnt-models"
    new_dir.mkdir()
    r = isolated_client.put(
        "/api/config/models",
        json={"pull_root": str(new_dir)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pull_root"] == str(new_dir)
    # pull_root must appear in the effective scan roots even though the
    # caller didn't pass it explicitly.
    assert str(new_dir) in body["roots"]

    toml_path = Path(tmp_hal0_home) / "etc" / "hal0" / "hal0.toml"
    parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert parsed["models"]["pull_root"] == str(new_dir)


def test_put_pull_root_relative_path_rejected(isolated_client: TestClient) -> None:
    r = isolated_client.put(
        "/api/config/models",
        json={"pull_root": "models"},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "config.invalid"
