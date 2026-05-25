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


def test_install_state_bundle_null_before_pick(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """No bundle pick yet → ``bundle`` is null. Issue #214."""
    r = isolated_client.get("/api/install/state")
    assert r.status_code == 200
    assert r.json()["bundle"] is None


def test_install_state_bundle_echoes_chosen_tier(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """After ``mark_bundle_chosen``, state echoes the tier name. Issue #214."""
    from hal0.bundles import store as bundle_store

    bundle_store.mark_bundle_chosen("hal0-Pro", npu_opt_in=False)
    r = isolated_client.get("/api/install/state")
    assert r.status_code == 200
    body = r.json()
    assert body["bundle"] is not None
    assert body["bundle"]["name"] == "hal0-Pro"
    assert body["bundle"]["skipped"] is False


def test_install_state_bundle_skipped_branch(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """Skip path: ``bundle.skipped=True`` and ``name`` is empty. Issue #214."""
    from hal0.bundles import store as bundle_store

    bundle_store.mark_skipped()
    r = isolated_client.get("/api/install/state")
    body = r.json()
    assert body["bundle"]["skipped"] is True
    assert body["bundle"]["name"] == ""


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

    # Idempotent: a second call is fine.
    r2 = isolated_client.post("/api/install/complete")
    assert r2.status_code == 200


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


# ── PUT /api/install/slots/{slot}/model ────────────────────────────────────


@pytest.fixture
def isolated_app_client(
    tmp_hal0_home: str,
) -> Iterator[tuple[FastAPI, TestClient]]:
    """Like isolated_client, but also yields the app for state inspection."""
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield app, c


def test_set_slot_default_model_writes_toml_and_returns_path(
    isolated_app_client: tuple[FastAPI, TestClient], tmp_hal0_home: str
) -> None:
    """PUT /api/install/slots/primary/model rewrites the slot TOML."""
    import tomllib

    from hal0.registry.model import Model

    app, client = isolated_app_client
    app.state.model_registry.add(
        Model(
            id="qwen3.5-4b",
            name="Qwen 3.5 4B",
            path="/tmp/qwen3.5-4b.gguf",
            size_bytes=1,
            license="Apache-2.0",
            capabilities=["chat"],
        )
    )

    r = client.put(
        "/api/install/slots/primary/model",
        json={"model_id": "qwen3.5-4b"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "slot": "primary",
        "model_id": "qwen3.5-4b",
        "slot_path": str(Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "primary.toml"),
        "persisted": True,
    }

    cfg = tomllib.loads(Path(body["slot_path"]).read_text())
    assert cfg["model"]["default"] == "qwen3.5-4b"
    # Built-in defaults seeded by _assign_to_slot when creating the file.
    assert cfg["port"] == 8081
    assert cfg["backend"] == "vulkan"


def test_set_slot_default_model_preserves_existing_port_and_backend(
    isolated_app_client: tuple[FastAPI, TestClient], tmp_hal0_home: str
) -> None:
    """Existing port/backend in the TOML survive the model swap."""
    import tomllib

    import tomli_w

    from hal0.registry.model import Model

    app, client = isolated_app_client
    app.state.model_registry.add(
        Model(
            id="qwen3.5-9b",
            name="Qwen 3.5 9B",
            path="/tmp/qwen3.5-9b.gguf",
            size_bytes=1,
            license="Apache-2.0",
            capabilities=["chat"],
        )
    )
    slot_path = Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "primary.toml"
    slot_path.parent.mkdir(parents=True, exist_ok=True)
    slot_path.write_bytes(
        tomli_w.dumps(
            {
                "name": "primary",
                "port": 9999,
                "backend": "rocm",
                "provider": "llama-server",
                "model": {"default": "phi3-mini"},
            }
        ).encode()
    )

    r = client.put(
        "/api/install/slots/primary/model",
        json={"model_id": "qwen3.5-9b"},
    )
    assert r.status_code == 200, r.text

    cfg = tomllib.loads(slot_path.read_text())
    assert cfg["model"]["default"] == "qwen3.5-9b"
    assert cfg["port"] == 9999  # preserved
    assert cfg["backend"] == "rocm"  # preserved


def test_set_slot_default_model_rejects_unknown_model_id(
    isolated_app_client: tuple[FastAPI, TestClient],
) -> None:
    """An id not in the registry returns 400 install.pick_default_failed."""
    _, client = isolated_app_client
    r = client.put(
        "/api/install/slots/primary/model",
        json={"model_id": "does-not-exist"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "install.pick_default_failed"
    assert "not in the registry" in body["error"]["message"]


def test_set_slot_default_model_requires_non_empty_model_id(
    isolated_app_client: tuple[FastAPI, TestClient],
) -> None:
    _, client = isolated_app_client
    r = client.put(
        "/api/install/slots/primary/model",
        json={"model_id": "   "},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "install.pick_default_failed"


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
