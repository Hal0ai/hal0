"""ComfyUIProvider provisions its own data root (ADR-0036).

The provider owns the model-paths file and the GPU-gate custom node: it writes
``extra_model_paths.yaml`` when that file is absent or empty and syncs the node
from a package asset, both at container launch. The installer does neither.
"""

from __future__ import annotations

import importlib.resources
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from hal0.comfyui import paths as comfyui_paths
from hal0.providers.comfyui import (
    ComfyUIProvider,
    provision_data_root,
    render_extra_model_paths,
)

_NODE = "hal0_gpu_gate.py"


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "comfyui"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    return root


def _asset_bytes() -> bytes:
    return importlib.resources.files("hal0.comfyui.assets").joinpath(_NODE).read_bytes()


# ── render_extra_model_paths ──────────────────────────────────────────────────


def test_render_lists_every_subdir_in_sorted_order() -> None:
    lines = render_extra_model_paths().splitlines()
    assert lines[0] == "comfyui:"
    assert lines[1].strip() == "base_path: /root/comfy-models"
    assert [line.strip() for line in lines[2:]] == [
        f"{name}: {comfyui_paths.SUBDIRS[name]}" for name in sorted(comfyui_paths.SUBDIRS)
    ]
    # every entry sits inside the comfyui: block, and the file ends in a newline
    assert all(line.startswith(" ") for line in lines[1:])
    assert render_extra_model_paths().endswith("\n")


def test_render_is_valid_yaml_comfyui_accepts() -> None:
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(render_extra_model_paths())
    assert list(parsed) == ["comfyui"]
    section = parsed["comfyui"]
    assert section["base_path"] == "/root/comfy-models"
    assert {k: v for k, v in section.items() if k != "base_path"} == dict(comfyui_paths.SUBDIRS)


# ── provision_data_root ───────────────────────────────────────────────────────


def test_provision_creates_every_mounted_dir(data_root: Path) -> None:
    provision_data_root()
    for sub in comfyui_paths.SUBDIRS.values():
        assert (data_root / "models" / sub).is_dir()
    for sub in ("output", "input", "user", "custom_nodes", "workflows"):
        assert (data_root / sub).is_dir()


def test_provision_writes_yaml_when_absent(data_root: Path) -> None:
    provision_data_root()
    assert comfyui_paths.extra_model_paths_file().read_text() == render_extra_model_paths()


def test_provision_writes_yaml_when_empty(data_root: Path) -> None:
    yaml_path = comfyui_paths.extra_model_paths_file()
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text("")
    provision_data_root()
    text = yaml_path.read_text()
    assert "base_path: /root/comfy-models" in text
    assert "diffusion_models: diffusion_models" in text


def test_provision_writes_yaml_when_only_whitespace(data_root: Path) -> None:
    yaml_path = comfyui_paths.extra_model_paths_file()
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text(" \n\t\n")
    provision_data_root()
    assert yaml_path.read_text() == render_extra_model_paths()


def test_provision_keeps_operator_yaml(data_root: Path) -> None:
    yaml_path = comfyui_paths.extra_model_paths_file()
    yaml_path.parent.mkdir(parents=True)
    operator = "comfyui:\n  base_path: /custom\n"
    yaml_path.write_text(operator)
    provision_data_root()
    assert yaml_path.read_text() == operator


def test_provision_syncs_gpu_gate_node(data_root: Path) -> None:
    dest = comfyui_paths.custom_nodes_dir() / _NODE
    assert not dest.exists()
    provision_data_root()
    assert dest.read_bytes() == _asset_bytes()


def test_provision_replaces_a_stale_gpu_gate_node(data_root: Path) -> None:
    dest = comfyui_paths.custom_nodes_dir() / _NODE
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"# an older release's node\n")
    provision_data_root()
    assert dest.read_bytes() == _asset_bytes()


def test_provision_leaves_an_up_to_date_gpu_gate_node_untouched(data_root: Path) -> None:
    provision_data_root()
    dest = comfyui_paths.custom_nodes_dir() / _NODE
    os.utime(dest, (1_000_000, 1_000_000))
    provision_data_root()
    assert dest.stat().st_mtime == 1_000_000
    assert dest.read_bytes() == _asset_bytes()


def test_provision_leaves_other_custom_nodes_alone(data_root: Path) -> None:
    other = comfyui_paths.custom_nodes_dir() / "operator_node.py"
    other.parent.mkdir(parents=True)
    other.write_text("# the operator's own node\n")
    provision_data_root()
    assert other.read_text() == "# the operator's own node\n"


def test_provision_is_idempotent(data_root: Path) -> None:
    provision_data_root()
    first = {p: p.read_bytes() for p in data_root.rglob("*") if p.is_file()}
    provision_data_root()
    second = {p: p.read_bytes() for p in data_root.rglob("*") if p.is_file()}
    assert first == second
    assert len(first) == 2  # the model-paths file and the GPU-gate node


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
def test_provision_survives_readonly_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(locked / "comfyui"))
    try:
        with caplog.at_level(logging.WARNING, logger="hal0.providers.comfyui"):
            provision_data_root()  # best-effort: logs, never raises
    finally:
        locked.chmod(0o700)
    assert "comfyui.data_root_provision_failed" in caplog.text
    assert not (locked / "comfyui").exists()


@pytest.mark.parametrize("blocked", ["models/vae", "output", "user", "custom_nodes"])
def test_provision_one_failing_directory_skips_nothing_else(data_root: Path, blocked: str) -> None:
    """A plain file where one directory belongs fails that mkdir only.

    Every other mounted directory still exists afterwards (podman exits 125 on
    a missing bind-mount source), and the model-paths file and the node are
    still written when ``custom_nodes`` itself is the one that is blocked.
    """
    (data_root / "models").mkdir(parents=True)
    (data_root / blocked).write_text("not a directory")
    provision_data_root()

    expected = [
        data_root / "models",
        *(data_root / "models" / sub for sub in comfyui_paths.SUBDIRS.values()),
        *(data_root / sub for sub in ("output", "input", "user", "custom_nodes", "workflows")),
    ]
    missing = [
        str(path.relative_to(data_root))
        for path in expected
        if not path.is_dir() and path != data_root / blocked
    ]
    assert missing == []
    assert comfyui_paths.extra_model_paths_file().read_text() == render_extra_model_paths()
    if blocked != "custom_nodes":
        assert (comfyui_paths.custom_nodes_dir() / _NODE).read_bytes() == _asset_bytes()


# ── container_spec ────────────────────────────────────────────────────────────


@pytest.fixture
def img_cfg() -> dict[str, Any]:
    return {"port": 8188, "device": "gpu-rocm", "_paths": {}}


def test_container_spec_provisions_the_data_root(
    data_root: Path, img_cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.providers.comfyui.resolve_gpu_device_paths", lambda: [])
    ComfyUIProvider().container_spec(img_cfg, {})
    assert comfyui_paths.extra_model_paths_file().read_text() == render_extra_model_paths()
    assert (comfyui_paths.custom_nodes_dir() / _NODE).read_bytes() == _asset_bytes()


def test_container_spec_mount_sources_come_from_the_paths_module(
    data_root: Path, img_cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.providers.comfyui.resolve_gpu_device_paths", lambda: [])
    spec = ComfyUIProvider().container_spec(img_cfg, {})
    by_target = {m.target: m for m in spec.mounts}
    assert by_target["/root/comfy-models"].source == str(comfyui_paths.models_root())
    assert by_target["/opt/ComfyUI/custom_nodes"].source == str(comfyui_paths.custom_nodes_dir())
    assert by_target["/opt/ComfyUI/user"].source == str(comfyui_paths.user_dir())
    assert by_target["/opt/ComfyUI/extra_model_paths.yaml"].source == str(
        comfyui_paths.extra_model_paths_file()
    )
