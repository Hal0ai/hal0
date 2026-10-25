"""``DELETE /api/comfyui/files/{subdir}/{relpath}``: removing a file nobody in the catalog owns (ADR-0037).

``hal0.comfyui.inventory.delete_file`` decides what may go (``tests/comfyui/test_inventory_delete.py``).
These tests cover what the route adds: the URL decoding a client path goes through, the error
envelope, and the Open WebUI re-render when the file behind the ``img`` selection is deleted.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.comfyui import paths

_SDXL_CHECKPOINT = "sd_xl_base_1.0.safetensors"


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Model store and ComfyUI data root under ``tmp_path``."""
    root = tmp_path / "store"
    monkeypatch.setenv("HAL0_MODEL_STORE", str(root))
    monkeypatch.delenv("HAL0_COMFYUI_DATA_ROOT", raising=False)
    assert paths.models_root().is_relative_to(tmp_path)
    return root


@pytest.fixture(autouse=True)
def _reset_comfyui_module_state() -> Iterator[None]:
    from hal0.api.routes import comfyui as comfyui_mod

    comfyui_mod._reset_state()
    yield
    comfyui_mod._reset_state()


@pytest.fixture(autouse=True)
def rewires(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Open WebUI reconciles run, in order; the real one would restart whatever unit the box has."""
    calls: list[str] = []
    monkeypatch.setattr(
        "hal0.components.openwebui_arm.reconcile_openwebui_env_background",
        lambda: calls.append("owui"),
    )
    return calls


class _Orchestrator:
    """Just the ``get_state`` the route reads the ``img`` selection from."""

    def __init__(self, model: str | None = None, *, enabled: bool = True) -> None:
        self.model = model
        self.enabled = enabled
        self.broken = False

    async def get_state(self) -> dict[str, Any]:
        if self.broken:
            raise RuntimeError("state unreadable")
        selection: dict[str, Any] = {"enabled": self.enabled, "model": self.model}
        return {"selections": {"img": {"img": selection}}}


def _select(client: TestClient, model: str | None, *, enabled: bool = True) -> _Orchestrator:
    orchestrator = _Orchestrator(model, enabled=enabled)
    client.app.state.capability_orchestrator = orchestrator
    return orchestrator


def _touch(subdir: str, relpath: str, size: int = 4) -> Path:
    path = paths.models_root() / subdir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _delete(client: TestClient, subdir: str, relpath: str) -> Any:
    return client.delete(f"/api/comfyui/files/{subdir}/{relpath}")


def test_delete_file_ok(client: TestClient) -> None:
    mine = _touch("checkpoints", "mine.safetensors")
    other = _touch("checkpoints", "other.safetensors")

    resp = _delete(client, "checkpoints", "mine.safetensors")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": "checkpoints/mine.safetensors"}
    assert not mine.exists()
    assert other.exists()
    listed = client.get("/api/comfyui/files").json()["files"]
    assert [f["relpath"] for f in listed] == ["other.safetensors"]


def test_delete_file_ok_nested_with_the_slash_encoded_or_not(client: TestClient) -> None:
    first = _touch("loras", "styles/a.safetensors")
    second = _touch("loras", "styles/b.safetensors")

    assert _delete(client, "loras", "styles/a.safetensors").status_code == 200
    assert _delete(client, "loras", "styles%2Fb.safetensors").status_code == 200

    assert not first.exists()
    assert not second.exists()


@pytest.mark.parametrize(
    "relpath",
    [
        "%2e%2e%2fx",
        "..%2fx",
        "a%2f..%2f..%2fx",
        "%2Fetc%2Fpasswd",
        "%2e%2e",
        "%2e",
        ".hf_stage_sdxl%2Fpart.safetensors",
        ".hidden.safetensors",
    ],
)
def test_delete_file_rejects_traversal(client: TestClient, relpath: str) -> None:
    outside = paths.models_root() / "x"
    inside = _touch("checkpoints", "a/b.safetensors")
    partial = _touch("checkpoints", ".hf_stage_sdxl/part.safetensors")
    hidden = _touch("checkpoints", ".hidden.safetensors")
    outside.write_bytes(b"keep")

    resp = _delete(client, "checkpoints", relpath)

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "comfyui.file_path_invalid"
    assert outside.read_bytes() == b"keep"
    assert inside.exists()
    assert partial.exists()
    assert hidden.exists()


def test_delete_file_rejects_an_unknown_subdir(client: TestClient) -> None:
    mine = _touch("checkpoints", "mine.safetensors")

    resp = _delete(client, "nope", "mine.safetensors")

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "comfyui.file_path_invalid"
    assert mine.exists()


@pytest.mark.parametrize("name", ["put_checkpoints_here", "extra.yaml", "notes.txt"])
def test_delete_file_refuses_a_file_the_listing_does_not_show(
    client: TestClient, name: str
) -> None:
    """Only weight files are listed, so only they can be deleted: the rest is 400, still on disk."""
    other = _touch("checkpoints", name)
    weight = _touch("checkpoints", "mine.gguf")
    before = client.get("/api/comfyui/files").json()["files"]
    assert [f["relpath"] for f in before] == ["mine.gguf"]

    resp = _delete(client, "checkpoints", name)

    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "comfyui.file_path_invalid"
    assert "weight file" in error["message"]
    assert other.exists()
    assert client.get("/api/comfyui/files").json()["files"] == before

    assert _delete(client, "checkpoints", "mine.gguf").status_code == 200
    assert not weight.exists()
    assert other.exists()


def test_delete_file_rejects_symlink(client: TestClient, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere" / "real.safetensors"
    real.parent.mkdir()
    real.write_bytes(b"real")
    link = paths.models_root() / "checkpoints" / "linked.safetensors"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    resp = _delete(client, "checkpoints", "linked.safetensors")

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "comfyui.file_symlink"
    assert link.is_symlink()
    assert real.read_bytes() == b"real"


def test_listing_marks_a_symlinked_weight_so_the_ui_can_hide_delete(
    client: TestClient, tmp_path: Path
) -> None:
    _touch("checkpoints", "plain.safetensors")
    real = tmp_path / "elsewhere" / "real.safetensors"
    real.parent.mkdir()
    real.write_bytes(b"real")
    (paths.models_root() / "checkpoints" / "linked.safetensors").symlink_to(real)

    rows = {f["relpath"]: f for f in client.get("/api/comfyui/files").json()["files"]}

    assert rows["plain.safetensors"]["symlink"] is False
    assert rows["linked.safetensors"]["symlink"] is True


def test_delete_file_409_catalog_owned(client: TestClient) -> None:
    owned = _touch("checkpoints", _SDXL_CHECKPOINT)

    resp = _delete(client, "checkpoints", _SDXL_CHECKPOINT)

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "comfyui.file_catalog_owned"
    (listed,) = client.get("/api/comfyui/files").json()["files"]
    assert error["details"]["models"] == listed["models"] == ["sdxl-lightning"]
    assert owned.exists()


def test_delete_file_403_on_permission_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = _touch("checkpoints", "mine.safetensors")

    def denied(self: Path, *args: Any, **kwargs: Any) -> None:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "unlink", denied)

    resp = _delete(client, "checkpoints", "mine.safetensors")

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "comfyui.file_permission"
    assert mine.exists()


def test_delete_file_404(client: TestClient) -> None:
    resp = _delete(client, "checkpoints", "nope.safetensors")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "comfyui.file_not_found"


# ── what follows a delete: Open WebUI names the img selection ───────────────


def test_deleting_the_file_behind_the_img_selection_rewires_open_webui(
    client: TestClient, rewires: list[str]
) -> None:
    _touch("checkpoints", "mine.safetensors")
    _select(client, "ckpt/mine")

    assert _delete(client, "checkpoints", "mine.safetensors").status_code == 200

    assert rewires == ["owui"]


def test_deleting_a_nested_selected_checkpoint_rewires_open_webui(
    client: TestClient, rewires: list[str]
) -> None:
    _touch("checkpoints", "sub/mine.safetensors")
    _select(client, "ckpt/sub/mine")

    assert _delete(client, "checkpoints", "sub%2Fmine.safetensors").status_code == 200

    assert rewires == ["owui"]


@pytest.mark.parametrize(
    ("subdir", "relpath", "selected", "enabled"),
    [
        ("checkpoints", "mine.safetensors", "ckpt/other", True),
        ("checkpoints", "mine.safetensors", "sdxl-lightning", True),
        ("checkpoints", "mine.safetensors", None, True),
        ("checkpoints", "mine.safetensors", "ckpt/mine", False),
        ("loras", "mine.safetensors", "ckpt/mine", True),
    ],
)
def test_deleting_any_other_file_leaves_open_webui_alone(
    client: TestClient,
    rewires: list[str],
    subdir: str,
    relpath: str,
    selected: str | None,
    enabled: bool,
) -> None:
    _touch(subdir, relpath)
    _select(client, selected, enabled=enabled)

    assert _delete(client, subdir, relpath).status_code == 200

    assert rewires == []


def test_deleting_without_an_orchestrator_leaves_open_webui_alone(
    client: TestClient, rewires: list[str]
) -> None:
    client.app.state.capability_orchestrator = None
    _touch("checkpoints", "mine.safetensors")

    assert _delete(client, "checkpoints", "mine.safetensors").status_code == 200

    assert rewires == []


def test_deleting_a_checkpoint_rewires_open_webui_when_the_selection_cannot_be_read(
    client: TestClient, rewires: list[str]
) -> None:
    """An unreadable selection may be this file; the re-render is a no-op when nothing changed."""
    _touch("checkpoints", "mine.safetensors")
    _select(client, "ckpt/mine").broken = True

    assert _delete(client, "checkpoints", "mine.safetensors").status_code == 200

    assert rewires == ["owui"]


def test_a_refused_delete_rewires_nothing(client: TestClient, rewires: list[str]) -> None:
    _touch("checkpoints", _SDXL_CHECKPOINT)
    _select(client, "ckpt/sd_xl_base_1.0")

    assert _delete(client, "checkpoints", _SDXL_CHECKPOINT).status_code == 409
    assert _delete(client, "checkpoints", "nope.safetensors").status_code == 404
    assert _delete(client, "checkpoints", "%2e%2e%2fx").status_code == 400

    assert rewires == []


def test_the_delete_succeeds_when_the_open_webui_rewire_breaks(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise RuntimeError("owui broke")

    monkeypatch.setattr("hal0.components.openwebui_arm.reconcile_openwebui_env_background", broken)
    mine = _touch("checkpoints", "mine.safetensors")
    _select(client, "ckpt/mine")

    resp = _delete(client, "checkpoints", "mine.safetensors")

    assert resp.status_code == 200
    assert not mine.exists()
