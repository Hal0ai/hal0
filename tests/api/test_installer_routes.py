"""Tests for /api/install — first-run state + probe + complete.

Tests use ``tmp_hal0_home`` so writes land under a pytest tmp dir.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """TestClient whose lifespan resolves paths under tmp_hal0_home."""
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def test_install_state_first_run_true_on_empty_models_dir(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """Fresh install — no models dir, no sentinel — reports first_run=True."""
    r = isolated_client.get("/api/install/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["first_run"] is True
    assert body["has_models"] is False
    assert body["has_default_slot"] is False
    assert "openwebui_running" in body
    assert body["sentinel_path"].endswith(".first_run_done")


def test_install_state_first_run_false_after_model_present(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """Drop a file into /var/lib/hal0/models/ — first_run flips to False."""
    models_dir = Path(tmp_hal0_home) / "var-lib" / "hal0" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "qwen3-4b.gguf").write_bytes(b"\x00" * 16)

    r = isolated_client.get("/api/install/state")
    assert r.status_code == 200
    body = r.json()
    assert body["has_models"] is True
    assert body["first_run"] is False


def test_install_state_first_run_false_after_sentinel(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """Even with no models, a sentinel marks first_run as done."""
    var_lib = Path(tmp_hal0_home) / "var-lib" / "hal0"
    var_lib.mkdir(parents=True, exist_ok=True)
    (var_lib / ".first_run_done").write_text("done\n", encoding="utf-8")

    r = isolated_client.get("/api/install/state")
    body = r.json()
    assert body["has_models"] is False
    assert body["first_run"] is False


def test_install_state_has_default_slot_when_primary_toml_exists(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """A primary.toml on disk flips has_default_slot to True."""
    slots = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    (slots / "primary.toml").write_text(
        'name = "primary"\nport = 8081\nbackend = "vulkan"\nprovider = "llama-server"\n',
        encoding="utf-8",
    )
    r = isolated_client.get("/api/install/state")
    assert r.status_code == 200
    assert r.json()["has_default_slot"] is True


def test_install_state_has_default_slot_when_agent_toml_exists(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """An agent.toml on disk flips has_default_slot to True (canonical per ADR-0023)."""
    slots = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    (slots / "agent.toml").write_text(
        'name = "agent"\nport = 8081\nbackend = "vulkan"\nprovider = "llama-server"\n',
        encoding="utf-8",
    )
    r = isolated_client.get("/api/install/state")
    assert r.status_code == 200
    assert r.json()["has_default_slot"] is True


def test_install_state_has_no_bundle_field(isolated_client: TestClient, tmp_hal0_home: str) -> None:
    """The v1 ``bundle`` field is absent from /state (removed in Task 6.2)."""
    r = isolated_client.get("/api/install/state")
    assert r.status_code == 200
    assert "bundle" not in r.json()


def test_install_complete_writes_sentinel_atomically(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """POST /api/install/complete writes the marker file and flips first_run."""
    r = isolated_client.post("/api/install/complete")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["first_run"] is False
    sentinel = Path(body["sentinel_path"])
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8").strip() == "first_run_done"

    # Idempotent: re-running /complete returns 200, sentinel stays intact.
    r2 = isolated_client.post("/api/install/complete")
    assert r2.status_code == 200, r2.text
    assert r2.json()["first_run"] is False


# ── Idempotent re-provisioning (guard removed) ───────────────────────────────


def _write_sentinel(tmp_hal0_home: str) -> Path:
    var_lib = Path(tmp_hal0_home) / "var-lib" / "hal0"
    var_lib.mkdir(parents=True, exist_ok=True)
    sentinel = var_lib / ".first_run_done"
    sentinel.write_text("first_run_done\n", encoding="utf-8")
    return sentinel


def test_install_complete_idempotent_when_sentinel_present(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """A sentinel written by another path (e.g. /apply) is harmless — /complete is idempotent."""
    _write_sentinel(tmp_hal0_home)

    r = isolated_client.post("/api/install/complete")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["first_run"] is False
    assert Path(body["sentinel_path"]).exists()

    # GET /state keeps working and still reports first_run: false.
    state = isolated_client.get("/api/install/state")
    assert state.status_code == 200
    assert state.json()["first_run"] is False


def test_install_apply_idempotent_when_sentinel_present(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """/apply is idempotent — re-running provisions normally."""
    _write_sentinel(tmp_hal0_home)

    r = isolated_client.post(
        "/api/install/apply",
        json={"tier": "hal0-Default", "storage_dir": tmp_hal0_home, "npu_opt_in": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "model_ids" in body
    assert "slots" in body


def test_install_apply_selections_idempotent_when_sentinel_present(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """/apply-selections is idempotent — re-running provisions normally."""
    _write_sentinel(tmp_hal0_home)

    r = isolated_client.post(
        "/api/install/apply-selections",
        json={"storage_dir": tmp_hal0_home, "npu_opt_in": False, "extensions": {}, "slots": []},
    )
    assert r.status_code == 200, r.text


def test_install_apply_open_without_sentinel_still_validates_body(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """Without a sentinel, /apply is unaffected — normal validation still runs."""
    r = isolated_client.post(
        "/api/install/apply",
        json={"tier": "not-a-real-tier", "storage_dir": tmp_hal0_home},
    )
    # Unknown tier still 404s (not closed) — the endpoint behaves as before
    # first-run.
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] != "install.closed"


def test_install_probe_writes_hardware_json(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """POST /api/install/probe runs HardwareProbe and writes hardware.json."""
    r = isolated_client.post("/api/install/probe")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "hardware" in body
    assert "path" in body
    target = Path(body["path"])
    assert target.exists(), f"expected hardware.json at {target}"
    # Probed hardware always carries the canonical keys.
    info = body["hardware"]
    assert "cpu_model" in info
    assert "ram_mb" in info
    assert "gpus" in info
    assert "npu" in info
    assert info["probed_at"], "probed_at should be a non-empty ISO timestamp"


def test_install_curated_models_returns_catalogue(isolated_client: TestClient) -> None:
    """Curated-models endpoint surfaces the manifest the wizard renders."""
    r = isolated_client.get("/api/install/curated-models")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["custom_allowed"] is True
    ids = {m["id"] for m in body["models"]}
    # Catalogue must always include at least the three named picks.
    assert {"qwen3-4b", "llama32-3b", "phi3-mini"}.issubset(ids)
    # Every entry carries the wizard's required fields.
    for m in body["models"]:
        for key in (
            "id",
            "display_name",
            "description",
            "size_gb",
            "vram_gb_min",
            "license",
            "license_url",
            "hf_repo",
            "hf_file",
        ):
            assert key in m, f"missing {key!r} on curated entry {m.get('id')}"
