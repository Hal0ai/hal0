"""ComfyUIProvider provisions its own data root (ADR-0037).

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
from hal0.providers import comfyui as comfyui_provider
from hal0.providers.comfyui import (
    ComfyUIProvider,
    provision_data_root,
    render_extra_model_paths,
)

_NODE = "hal0_gpu_gate.py"


@pytest.fixture(autouse=True)
def _fresh_provision_memo() -> Any:
    """The once-per-process memo must not leak between tests."""
    comfyui_provider._reset_provision_memo()
    yield
    comfyui_provider._reset_provision_memo()


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
    assert lines[1].strip() == "base_path: /opt/hal0-models"
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
    assert section["base_path"] == "/opt/hal0-models"
    assert {k: v for k, v in section.items() if k != "base_path"} == dict(comfyui_paths.SUBDIRS)


# ── provision_data_root ───────────────────────────────────────────────────────


def test_provision_creates_every_mounted_dir(data_root: Path) -> None:
    provision_data_root()
    for sub in comfyui_paths.SUBDIRS.values():
        assert (data_root / "models" / sub).is_dir()
    for sub in ("output", "input", "user", "custom_nodes", "workflows", "home"):
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
    assert "base_path: /opt/hal0-models" in text
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


def test_provision_moves_hal0s_own_legacy_models_mount(data_root: Path) -> None:
    """#3066 moved the models mount out of /root (not traversable as the hal0
    account). A yaml whose base_path is the mount hal0 itself wrote before is
    hal0's: the one line moves, every other line survives byte for byte."""
    yaml_path = comfyui_paths.extra_model_paths_file()
    yaml_path.parent.mkdir(parents=True)
    legacy = (
        "# operator note kept\n"
        "comfyui:\n"
        "    base_path: /root/comfy-models\n"
        "    checkpoints: checkpoints\n"
        "    my_extra_type: extra\n"
    )
    yaml_path.write_text(legacy)
    provision_data_root()
    assert yaml_path.read_text() == legacy.replace("/root/comfy-models", "/opt/hal0-models")


def test_provision_migration_is_a_one_time_rewrite(data_root: Path) -> None:
    yaml_path = comfyui_paths.extra_model_paths_file()
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text("comfyui:\n    base_path: /root/comfy-models\n")
    provision_data_root()
    moved = yaml_path.read_text()
    assert "/opt/hal0-models" in moved
    comfyui_provider._reset_provision_memo()
    provision_data_root()
    assert yaml_path.read_text() == moved


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_provision_reports_a_mount_source_hal0_cannot_write_once(
    data_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The container runs as the hal0 account (#3066): a data dir this process
    cannot write is one ComfyUI cannot write either. Reported once, with the
    remedy, never raised — provisioning stays best-effort."""
    provision_data_root()
    output = data_root / "output"
    output.chmod(0o555)
    try:
        comfyui_provider._reset_provision_memo()
        with caplog.at_level(logging.WARNING, logger="hal0.providers.comfyui"):
            provision_data_root()
            provision_data_root()
        hits = [r for r in caplog.records if "comfyui.data_dir_not_writable" in r.getMessage()]
        assert len(hits) == 1
        assert str(output) in hits[0].getMessage()
        assert "chgrp" in hits[0].getMessage()
    finally:
        output.chmod(0o755)


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


def _deny_writes_in(monkeypatch: pytest.MonkeyPatch, *directories: Path) -> None:
    """Make creating a directory, or a file in one, under ``directories`` fail with EACCES.

    A real ``chmod`` does nothing to root, which is who runs this suite in some containers,
    so the permission failure is injected at the two calls the provisioner makes instead:
    ``ensure_shared_dir`` for a directory and ``tempfile.mkstemp`` for the staged node.
    """

    def denied(path: Path) -> OSError:
        return PermissionError(13, "Permission denied", str(path))

    real_ensure = comfyui_provider.ensure_shared_dir
    real_mkstemp = comfyui_provider.tempfile.mkstemp

    def ensure(path: Path, *args: Any, **kwargs: Any) -> Any:
        if any(Path(path).is_relative_to(d) for d in directories):
            raise denied(path)
        return real_ensure(path, *args, **kwargs)

    def mkstemp(*args: Any, dir: Any = None, **kwargs: Any) -> Any:
        if dir is not None and any(Path(dir).is_relative_to(d) for d in directories):
            raise denied(dir)
        return real_mkstemp(*args, dir=dir, **kwargs)

    monkeypatch.setattr(comfyui_provider, "ensure_shared_dir", ensure)
    monkeypatch.setattr(comfyui_provider.tempfile, "mkstemp", mkstemp)


def test_provision_survives_readonly_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(locked / "comfyui"))
    _deny_writes_in(monkeypatch, locked)
    with caplog.at_level(logging.WARNING, logger="hal0.providers.comfyui"):
        provision_data_root()  # best-effort: logs, never raises
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


def test_provision_node_sync_runs_once_per_process(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The YAML and the node are written on the first render only (I1)."""
    writes: list[str] = []
    real_replace = os.replace

    def _spy(src: Any, dst: Any) -> None:
        writes.append(str(dst))
        real_replace(src, dst)

    monkeypatch.setattr("hal0.providers.comfyui.os.replace", _spy)
    provision_data_root()
    assert writes == [str(comfyui_paths.custom_nodes_dir() / _NODE)]

    # Second render: nothing is rewritten, even though both files now differ.
    node = comfyui_paths.custom_nodes_dir() / _NODE
    yaml_path = comfyui_paths.extra_model_paths_file()
    node.write_bytes(b"# edited after the first render\n")
    yaml_path.write_text("")
    provision_data_root()
    assert writes == [str(node)]
    assert node.read_bytes() == b"# edited after the first render\n"
    assert yaml_path.read_text() == ""


def test_container_spec_twice_provisions_once(
    data_root: Path, img_cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 30 s drift probe renders the spec over and over; only the first writes."""
    monkeypatch.setattr("hal0.providers.comfyui.resolve_gpu_device_paths", lambda: [])
    provider = ComfyUIProvider()
    provider.container_spec(img_cfg, {})
    node = comfyui_paths.custom_nodes_dir() / _NODE
    node.write_bytes(b"# edited after the first render\n")
    provider.container_spec(img_cfg, {})
    assert node.read_bytes() == b"# edited after the first render\n"


def test_provision_memo_is_per_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A moved store (another data root) is provisioned afresh."""
    first, second = tmp_path / "one", tmp_path / "two"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(first))
    provision_data_root()
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(second))
    provision_data_root()
    assert (second / "custom_nodes" / _NODE).read_bytes() == _asset_bytes()
    assert (second / "extra_model_paths.yaml").read_text() == render_extra_model_paths()


def test_provision_recreates_missing_dirs_on_every_render(data_root: Path) -> None:
    """Directories are the one step that is not memoised: podman needs every source."""
    provision_data_root()
    (data_root / "output").rmdir()
    provision_data_root()
    assert (data_root / "output").is_dir()


def test_provision_permission_warning_logged_once(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A read-only ``custom_nodes`` warns once, with the remedy, not on every render (I1)."""
    nodes = comfyui_paths.custom_nodes_dir()
    nodes.mkdir(parents=True)
    (nodes / _NODE).write_bytes(b"# installed as root, an older release's node\n")
    _deny_writes_in(monkeypatch, nodes)
    with caplog.at_level(logging.WARNING, logger="hal0.providers.comfyui"):
        provision_data_root()
        provision_data_root()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, warnings
    assert f"cannot update {nodes / _NODE}" in warnings[0]
    assert "writable by the hal0 user or group" in warnings[0]
    # The node was left as it was, and no temp file was littered next to it.
    assert (nodes / _NODE).read_bytes() == b"# installed as root, an older release's node\n"
    assert [p.name for p in nodes.iterdir()] == [_NODE]


def test_provision_heals_once_the_node_dir_becomes_writable(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed sync is retried (silently) on later renders; success is then memoised."""
    nodes = comfyui_paths.custom_nodes_dir()
    nodes.mkdir(parents=True)
    with monkeypatch.context() as denied:  # the directory is writable again once this exits
        _deny_writes_in(denied, nodes)
        provision_data_root()
        assert not (nodes / _NODE).exists()
    provision_data_root()
    assert (nodes / _NODE).read_bytes() == _asset_bytes()


def test_node_written_atomically(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The node is staged next to its destination and renamed into place (I1)."""
    calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def _spy(src: Any, dst: Any) -> None:
        calls.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr("hal0.providers.comfyui.os.replace", _spy)
    provision_data_root()
    dest = comfyui_paths.custom_nodes_dir() / _NODE
    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == dest
    assert src != dest
    assert src.parent == dest.parent  # same filesystem, so the rename is atomic
    assert dest.read_bytes() == _asset_bytes()
    assert dest.stat().st_mode & 0o777 == 0o644
    assert [p.name for p in dest.parent.iterdir()] == [_NODE]  # no leftover temp file


def test_provision_creates_dirs_umask_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Created directories land at 2775 under a 022 umask; existing ones are not re-moded (M8)."""
    root = tmp_path / "comfyui"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    monkeypatch.setattr("hal0.install.perms._shared_dir_roots", lambda: (tmp_path.resolve(),))
    (root / "models").mkdir(parents=True)
    (root / "models").chmod(0o750)
    old = os.umask(0o022)
    try:
        provision_data_root()
    finally:
        os.umask(old)
    assert (root / "output").stat().st_mode & 0o7777 == 0o2775
    assert (root / "models" / "vae").stat().st_mode & 0o7777 == 0o2775
    assert (root / "models").stat().st_mode & 0o7777 == 0o750  # pre-existing: untouched


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
    assert by_target["/opt/hal0-models"].source == str(comfyui_paths.models_root())
    assert by_target["/home/hal0"].source == str(data_root / "home")
    assert by_target["/opt/ComfyUI/custom_nodes"].source == str(comfyui_paths.custom_nodes_dir())
    assert by_target["/opt/ComfyUI/user"].source == str(comfyui_paths.user_dir())
    assert by_target["/opt/ComfyUI/extra_model_paths.yaml"].source == str(
        comfyui_paths.extra_model_paths_file()
    )
