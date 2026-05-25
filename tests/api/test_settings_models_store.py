"""Tests for /api/settings/models/store (single-source-of-truth field).

Exercises:
  * GET returns suggestions + current state + effective fallback.
  * POST happy path with no prior data → sets hal0.toml + lemonade config.
  * POST dry-run when prior path has data → returns ``needs_migration``.
  * POST migrate=true → moves files, propagates, persists, surfaces result.
  * Validation: relative path, missing, file-not-dir, non-writable.
  * Round-trip: getter reflects effective fallback when only legacy
    pull_root is set (PR-#313 compat).
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config import paths as cfg_paths


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def _seed_lemonade_config(tmp_hal0_home: str, value: str) -> Path:
    cfg = cfg_paths.var_lib() / "lemonade" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"extra_models_dir": value, "port": 13305}),
        encoding="utf-8",
    )
    return cfg


def test_get_store_returns_suggestions_and_effective_fallback(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    r = isolated_client.get("/api/settings/models/store")
    assert r.status_code == 200, r.text
    body = r.json()
    # No store set on a fresh install — fallback to pull_root (the
    # HAL0_HOME-rooted models_dir for tests).
    assert body["store"] is None
    assert body["fallback_active"] is True
    assert body["effective"] == str(cfg_paths.models_dir())
    assert isinstance(body["suggestions"], list)
    assert any(s["path"] == "/mnt/ai-models" for s in body["suggestions"])


def test_set_store_happy_path_no_prior_data(
    isolated_client: TestClient, tmp_hal0_home: str, tmp_path: Path
) -> None:
    target = tmp_path / "new-store"
    target.mkdir()
    _seed_lemonade_config(tmp_hal0_home, "/var/lib/hal0/models")

    r = isolated_client.post(
        "/api/settings/models/store",
        json={"path": str(target)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["config"]["models"]["store"] == str(target)
    assert body["state"]["effective"] == str(target)
    # No migration needed → migration block is None.
    assert body["migration"] is None
    # Lemonade config was updated.
    assert body["lemonade"]["changed"] is True
    assert body["lemonade"]["previous_extra_models_dir"] == "/var/lib/hal0/models"
    # In tests HAL0_HOME is set → restart_lemonade_service returns None
    # → response surfaces "unavailable".
    assert body["lemonade"]["restart"] == "unavailable"

    # hal0.toml persisted.
    toml_path = cfg_paths.hal0_toml()
    parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert parsed["models"]["store"] == str(target)

    # Lemonade config.json on disk now points at the new path.
    lemonade_cfg = json.loads(
        (cfg_paths.var_lib() / "lemonade" / "config.json").read_text(encoding="utf-8")
    )
    assert lemonade_cfg["extra_models_dir"] == str(target)


def test_set_store_dry_run_when_prior_path_has_data(
    isolated_client: TestClient, tmp_hal0_home: str, tmp_path: Path
) -> None:
    # Seed the default location with a fake model.
    default_store = cfg_paths.models_dir()
    default_store.mkdir(parents=True, exist_ok=True)
    (default_store / "Model").mkdir()
    (default_store / "Model" / "x.gguf").write_bytes(b"x" * 1024)

    target = tmp_path / "new-store"
    target.mkdir()

    r = isolated_client.post(
        "/api/settings/models/store",
        json={"path": str(target)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "needs_migration"
    assert body["plan"]["files_count"] == 1
    assert body["plan"]["size_bytes"] == 1024
    assert body["plan"]["source"] == str(default_store)
    assert body["plan"]["target"] == str(target)

    # Nothing should have moved or been persisted.
    assert (default_store / "Model" / "x.gguf").exists()
    assert not (target / "Model").exists()
    toml_path = cfg_paths.hal0_toml()
    if toml_path.exists():
        parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        assert parsed.get("models", {}).get("store", "") == ""


def test_set_store_migrate_true_moves_files(
    isolated_client: TestClient, tmp_hal0_home: str, tmp_path: Path
) -> None:
    default_store = cfg_paths.models_dir()
    default_store.mkdir(parents=True, exist_ok=True)
    (default_store / "Model").mkdir()
    (default_store / "Model" / "x.gguf").write_bytes(b"x" * 1024)
    _seed_lemonade_config(tmp_hal0_home, str(default_store))

    target = tmp_path / "new-store"
    target.mkdir()

    r = isolated_client.post(
        "/api/settings/models/store",
        json={"path": str(target), "migrate": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["migration"]["moved"] == ["Model"]
    assert body["migration"]["failed"] == []
    # File now lives in the new store, gone from the old.
    assert (target / "Model" / "x.gguf").read_bytes() == b"x" * 1024
    assert not (default_store / "Model").exists()


def test_migrate_endpoint_runs_move(
    isolated_client: TestClient, tmp_hal0_home: str, tmp_path: Path
) -> None:
    default_store = cfg_paths.models_dir()
    default_store.mkdir(parents=True, exist_ok=True)
    (default_store / "M").mkdir()
    (default_store / "M" / "x.gguf").write_bytes(b"x" * 10)

    target = tmp_path / "new"
    target.mkdir()

    r = isolated_client.post(
        "/api/settings/models/store/migrate",
        json={"path": str(target)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["migration"]["moved"] == ["M"]


def test_set_store_rejects_relative_path(isolated_client: TestClient) -> None:
    r = isolated_client.post(
        "/api/settings/models/store",
        json={"path": "relative/path"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "config.invalid"


def test_set_store_rejects_missing_path(isolated_client: TestClient) -> None:
    r = isolated_client.post(
        "/api/settings/models/store",
        json={"path": "/definitely/missing/zzz-12345"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "models.store_missing"


def test_set_store_rejects_file_not_directory(isolated_client: TestClient, tmp_path: Path) -> None:
    blob = tmp_path / "blob"
    blob.write_text("hi")
    r = isolated_client.post(
        "/api/settings/models/store",
        json={"path": str(blob)},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "models.store_not_directory"


def test_set_store_rejects_non_writable_path(isolated_client: TestClient, tmp_path: Path) -> None:
    ro = tmp_path / "readonly"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        r = isolated_client.post(
            "/api/settings/models/store",
            json={"path": str(ro)},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] in (
            "models.store_unwritable",
            "models.store_unreadable",
        )
    finally:
        ro.chmod(0o755)


def test_set_store_rejects_empty_path(isolated_client: TestClient) -> None:
    r = isolated_client.post(
        "/api/settings/models/store",
        json={"path": ""},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "config.invalid"


def test_set_store_idempotent_when_path_unchanged(
    isolated_client: TestClient, tmp_hal0_home: str, tmp_path: Path
) -> None:
    target = tmp_path / "store"
    target.mkdir()
    r1 = isolated_client.post(
        "/api/settings/models/store",
        json={"path": str(target)},
    )
    assert r1.status_code == 200
    r2 = isolated_client.post(
        "/api/settings/models/store",
        json={"path": str(target)},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "ok"
    # No move + lemonade already updated → changed=False on second hit.
    assert body["migration"] is None


def test_lemonade_admin_locked_value_follows_store(
    isolated_client: TestClient, tmp_hal0_home: str, tmp_path: Path
) -> None:
    """The Lemonade admin endpoint's locked value should track effective_store."""
    from hal0.api.routes.lemonade_admin import _locked_extra_models_dir

    target = tmp_path / "new-store"
    target.mkdir()

    isolated_client.post(
        "/api/settings/models/store",
        json={"path": str(target)},
    )
    # The locked value the admin validator now refuses to diverge from
    # is the new store path.
    assert _locked_extra_models_dir() == str(target)
