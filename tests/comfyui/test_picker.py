"""hal0.comfyui.picker: the image rows of the capability picker (ADR-0037).

The picker lists the image catalog and the operator's own checkpoints, never LLM registry
rows. Install state is read from the real models tree (``HAL0_COMFYUI_DATA_ROOT`` points it
at ``tmp_path``), so "downloaded" here means the files really exist.
"""

from __future__ import annotations

import json
import logging
import os
import struct
from pathlib import Path
from typing import Any

import pytest

from hal0.comfyui import inventory, paths, picker
from hal0.comfyui.catalog import CATALOG, component_path, weights_gib
from hal0.config.schema import HardwareInfo

_ROW_KEYS = {
    "id",
    "backend",
    "provider",
    "size_gb",
    "capabilities",
    "downloaded",
    "pullable",
    "display_name",
    "license",
    "recommended",
}
_SDXL_KEY = "conditioner.embedders.1.x"


@pytest.fixture(autouse=True)
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "comfyui-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    assert paths.models_root().is_relative_to(tmp_path)
    return root


@pytest.fixture(autouse=True)
def pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 64 GB unified pool, so the starter pick is ``sdxl-lightning`` on every machine."""
    monkeypatch.setattr(picker, "load_hardware_info", lambda: HardwareInfo(unified_memory_mb=65536))


def _install(model_id: str) -> None:
    root = paths.models_root()
    for component in CATALOG[model_id].components:
        path = component_path(component, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def _checkpoint(relpath: str, size: int = 0) -> None:
    """A user SDXL checkpoint under ``checkpoints/`` (a header the sniffer reads as SDXL)."""
    path = paths.models_root() / "checkpoints" / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({_SDXL_KEY: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}})
    body = raw.encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(body)) + body + b"\x00\x00" + b"\x00" * size)


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in rows}


def _renderable_catalog_ids() -> list[str]:
    return [m.id for m in CATALOG.values() if m.api_renderable]


def test_one_row_per_api_renderable_catalog_model_and_backend() -> None:
    rows = picker.image_picker_rows(["gpu-vulkan"])

    assert [r["id"] for r in rows] == _renderable_catalog_ids()
    assert {r["backend"] for r in rows} == {"gpu-vulkan"}
    # The video, edit and upscale models the API cannot render are the editor's, not the picker's.
    assert not {m.id for m in CATALOG.values() if not m.api_renderable} & {r["id"] for r in rows}


def test_rows_fan_out_over_every_backend_id() -> None:
    rows = picker.image_picker_rows(["gpu-vulkan", "cpu"])

    assert len(rows) == 2 * len(_renderable_catalog_ids())
    assert [(r["id"], r["backend"]) for r in rows[:2]] == [
        (_renderable_catalog_ids()[0], "gpu-vulkan"),
        (_renderable_catalog_ids()[0], "cpu"),
    ]


def test_no_backend_ids_means_no_rows() -> None:
    _checkpoint("mine.safetensors")

    assert picker.image_picker_rows([]) == []


def test_catalog_row_shape_matches_what_the_picker_emits_elsewhere() -> None:
    row = _by_id(picker.image_picker_rows(["gpu-vulkan"]))["sdxl-lightning"]
    model = CATALOG["sdxl-lightning"]

    assert set(row) == _ROW_KEYS
    assert row["backend"] == "gpu-vulkan"
    assert row["provider"] == "comfyui"
    assert row["capabilities"] == ["image"]
    assert row["size_gb"] == weights_gib(model)  # GiB, like a user checkpoint row's size
    assert row["pullable"] is True
    assert row["display_name"] == model.label
    assert row["license"] == model.license


def test_downloaded_follows_the_install_state() -> None:
    assert not any(r["downloaded"] for r in picker.image_picker_rows(["gpu-vulkan"]))

    _install("sdxl-lightning")

    downloaded = {r["id"] for r in picker.image_picker_rows(["gpu-vulkan"]) if r["downloaded"]}
    assert downloaded == {"sdxl-lightning"}


def test_a_partial_install_is_not_downloaded() -> None:
    first = CATALOG["sdxl-lightning"].components[0]
    path = component_path(first, paths.models_root())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    row = _by_id(picker.image_picker_rows(["gpu-vulkan"]))["sdxl-lightning"]

    assert row["downloaded"] is False
    assert row["pullable"] is True


def test_exactly_one_model_is_recommended_per_backend() -> None:
    rows = picker.image_picker_rows(["gpu-vulkan", "cpu"])

    assert sorted((r["id"], r["backend"]) for r in rows if r["recommended"]) == [
        ("sdxl-lightning", "cpu"),
        ("sdxl-lightning", "gpu-vulkan"),
    ]


@pytest.mark.parametrize(
    ("hardware", "expected"),
    [
        (HardwareInfo(unified_memory_mb=128 * 1024), "qwen-image-lightning"),
        (HardwareInfo(ram_mb=8 * 1024), "sd-1.5-pruned-emaonly"),
        (HardwareInfo(), "sdxl-lightning"),
        (RuntimeError("hardware.json is unreadable"), "sdxl-lightning"),
    ],
    ids=["128gb", "8gb-ram", "unprobed", "unreadable"],
)
def test_recommended_follows_the_memory_pool(
    monkeypatch: pytest.MonkeyPatch, hardware: Any, expected: str
) -> None:
    def load() -> HardwareInfo:
        if isinstance(hardware, Exception):
            raise hardware
        return hardware

    monkeypatch.setattr(picker, "load_hardware_info", load)

    assert picker.recommended_id() == expected
    rows = picker.image_picker_rows(["gpu-vulkan"])
    assert [r["id"] for r in rows if r["recommended"]] == [expected]


@pytest.mark.parametrize(
    ("hardware", "expected"),
    [
        (HardwareInfo(unified_memory_mb=128 * 1024), "qwen-image-lightning"),
        (HardwareInfo(unified_memory_mb=64 * 1024), "sdxl-lightning"),
        (HardwareInfo(ram_mb=8 * 1024), "sd-1.5-pruned-emaonly"),
        # A pool that reads as zero is a box that was never probed: unknown, not small.
        (HardwareInfo(), "sdxl-lightning"),
        (HardwareInfo(ram_mb=0, unified_memory_mb=0), "sdxl-lightning"),
    ],
    ids=["128gb", "64gb", "8gb-ram", "unprobed", "zeroed"],
)
def test_recommended_for_is_the_one_owner_of_the_starter_pick(
    hardware: HardwareInfo, expected: str
) -> None:
    assert picker.recommended_for(hardware) == expected


# ── a guest capped below the host's memory pool (#3057) ─────────────────────
#
# On a unified-memory host an LXC guest still reads the host's pool as `unified_memory_mb`,
# while `ram_mb` (MemTotal through lxcfs) and `cgroup_max_mb` carry the guest's limit. ComfyUI
# loads the weights into process memory, which the guest's memory cgroup is charged for, so the
# starter pick follows the smaller of the two.


@pytest.mark.parametrize(
    ("hardware", "expected"),
    [
        # 128 GiB host, guest limited to 48 GiB: Qwen-Image (51 GB) would be OOM-killed.
        (HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=48 * 1024), "sdxl-lightning"),
        # A guest with 96 GiB still holds the big model.
        (HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=96 * 1024), "qwen-image-lightning"),
        # The cgroup cap wins when it is below MemTotal.
        (
            HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=128 * 1024, cgroup_max_mb=40 * 1024),
            "sdxl-lightning",
        ),
        # ... and is ignored when it is above the pool.
        (
            HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=128 * 1024, cgroup_max_mb=256 * 1024),
            "qwen-image-lightning",
        ),
        # `ram_mb` 0 is "no limit known", not a zero-byte guest.
        (HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=0), "qwen-image-lightning"),
        (
            HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=0, cgroup_max_mb=0),
            "qwen-image-lightning",
        ),
        # No unified memory: the limit and the pool are the same RAM.
        (HardwareInfo(ram_mb=8 * 1024), "sd-1.5-pruned-emaonly"),
        # A cap that leaves under 16 GiB is a small box whatever the host has.
        (HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=12 * 1024), "sd-1.5-pruned-emaonly"),
    ],
    ids=[
        "capped-48gb-guest",
        "96gb-guest",
        "cgroup-below-ram",
        "cgroup-above-pool",
        "ram-unprobed",
        "ram-and-cgroup-zero",
        "no-unified-memory",
        "12gb-guest",
    ],
)
def test_recommended_for_ranks_by_what_the_guest_can_use(
    hardware: HardwareInfo, expected: str
) -> None:
    assert picker.recommended_for(hardware) == expected


@pytest.mark.parametrize(
    ("hardware", "expected"),
    [
        (HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=48 * 1024), 48.0),
        (HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=0), 128.0),
        (
            HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=96 * 1024, cgroup_max_mb=40 * 1024),
            40.0,
        ),
        (HardwareInfo(ram_mb=8 * 1024), 8.0),
        (HardwareInfo(), None),
        (HardwareInfo(cgroup_max_mb=40 * 1024), None),
    ],
    ids=["ram-limit", "no-limit-known", "cgroup-limit", "ram-only", "unprobed", "cgroup-only"],
)
def test_image_pool_gb_is_the_smaller_of_the_pool_and_the_limit(
    hardware: HardwareInfo, expected: float | None
) -> None:
    assert picker.image_pool_gb(hardware) == expected


def test_the_agent_ladder_still_sees_the_host_pool() -> None:
    """Only the image pick follows the guest's limit: GTT is not charged to its cgroup."""
    from hal0.install.agent_model import pool_gb

    hardware = HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=48 * 1024)

    assert pool_gb(hardware) == 128.0


# ── a models tree the service user cannot read ──────────────────────────────
#
# `GET /api/capabilities` builds the picker synchronously for every capability, so a raise here
# is a 500 for the whole dashboard payload (voice and embed selections included).


def _denied(*_args: Any, **_kwargs: Any) -> Any:
    raise PermissionError(13, "Permission denied", "/srv/models/checkpoints")


def test_an_unreadable_tree_lists_every_catalog_model_as_not_downloaded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(picker, "install_state", _denied)
    caplog.set_level(logging.WARNING, logger="hal0.comfyui.picker")

    rows = picker.image_picker_rows(["gpu-vulkan"])

    assert [r["id"] for r in rows] == _renderable_catalog_ids()
    assert not any(r["downloaded"] for r in rows)
    assert all(r["pullable"] for r in rows)  # still installable: the install route names the fix
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # once per call, not once per model
    assert "/srv/models/checkpoints" in warnings[0].getMessage()


def test_an_unreadable_checkpoint_scan_drops_only_the_user_checkpoint_rows(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install("sdxl-lightning")
    _checkpoint("mine.safetensors")
    monkeypatch.setattr(picker.inventory, "list_files", _denied)
    caplog.set_level(logging.WARNING, logger="hal0.comfyui.picker")

    rows = _by_id(picker.image_picker_rows(["gpu-vulkan"]))

    assert list(rows) == _renderable_catalog_ids()  # no ckpt/ rows, no raise
    assert rows["sdxl-lightning"]["downloaded"] is True  # the catalog rows still read the disk
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "/srv/models/checkpoints" in warnings[0].getMessage()


def test_a_readable_tree_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    _install("sdxl-lightning")
    caplog.set_level(logging.WARNING, logger="hal0.comfyui.picker")

    picker.image_picker_rows(["gpu-vulkan"])

    assert caplog.records == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 directory")
def test_a_models_dir_the_user_cannot_traverse_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real thing, not an injected error: a directory without the execute bit.

    Whether ``Path.is_file()`` raises ``PermissionError`` below it depends on the Python version
    (it does on 3.12; on 3.14 it returns False), so the rows are asserted on every version and
    the warning only where the platform actually raised. The injected-denial tests above pin the
    warning everywhere.
    """
    root = paths.models_root()
    (root / "checkpoints").mkdir(parents=True)
    root.chmod(0o000)
    caplog.set_level(logging.WARNING, logger="hal0.comfyui.picker")
    try:
        try:
            (root / "checkpoints" / "probe.safetensors").is_file()
            platform_raises = False
        except PermissionError:
            platform_raises = True
        rows = picker.image_picker_rows(["gpu-vulkan"])
    finally:
        root.chmod(0o755)  # so tmp_path can be cleaned up

    assert [r["id"] for r in rows] == _renderable_catalog_ids()
    assert not any(r["downloaded"] for r in rows)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    if platform_raises:
        assert len(warnings) == 1
        assert str(root) in warnings[0].getMessage()
    else:
        assert warnings == []  # nothing raised, so there is nothing to report


def test_a_user_checkpoint_is_a_downloaded_unpullable_row_under_its_api_id() -> None:
    _checkpoint("sub/mine.safetensors", size=2048)
    api_id = next(f["api_id"] for f in inventory.list_files() if "api_id" in f)
    assert api_id == "ckpt/sub/mine"

    row = _by_id(picker.image_picker_rows(["gpu-vulkan"]))[api_id]

    assert set(row) == _ROW_KEYS
    assert row["backend"] == "gpu-vulkan"
    assert row["provider"] == "comfyui"
    assert row["capabilities"] == ["image"]
    assert row["downloaded"] is True
    assert row["pullable"] is False
    assert row["recommended"] is False
    assert row["license"] == ""
    assert row["display_name"] == "sub/mine.safetensors"
    assert row["size_gb"] == round(
        (Path(paths.models_root()) / "checkpoints" / "sub" / "mine.safetensors").stat().st_size
        / 1024**3,
        2,
    )


def test_user_checkpoint_ids_come_from_the_inventory_never_from_the_file_stem() -> None:
    """``a.safetensors`` and ``sub/a.safetensors`` share a stem; their ids must not."""
    _checkpoint("a.safetensors")
    _checkpoint("sub/a.safetensors")

    user_ids = [
        r["id"] for r in picker.image_picker_rows(["gpu-vulkan"]) if r["id"].startswith("ckpt/")
    ]

    assert sorted(user_ids) == ["ckpt/a", "ckpt/sub/a"]


def test_a_user_checkpoint_gets_one_row_per_backend() -> None:
    _checkpoint("mine.safetensors")

    rows = [r for r in picker.image_picker_rows(["gpu-vulkan", "cpu"]) if r["id"] == "ckpt/mine"]

    assert [r["backend"] for r in rows] == ["gpu-vulkan", "cpu"]


def test_a_checkpoint_of_unknown_family_is_not_listed() -> None:
    path = paths.models_root() / "checkpoints" / "junk.safetensors"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a safetensors file")

    assert not [r for r in picker.image_picker_rows(["gpu-vulkan"]) if r["id"].startswith("ckpt/")]


def test_catalog_rows_come_before_user_checkpoints() -> None:
    _checkpoint("mine.safetensors")

    ids = [r["id"] for r in picker.image_picker_rows(["gpu-vulkan"])]

    assert ids == [*_renderable_catalog_ids(), "ckpt/mine"]
