"""Shared NPU accel-node + group-GID helpers (xdna-runner §2).

flm and the xdna llama-server runner both need /dev/accel + /dev/dri plus
the render/video GIDs; this pins the shared hal0.providers._gpu helper so
the two paths cannot drift.
"""

from __future__ import annotations

from unittest.mock import patch

from hal0.providers._gpu import resolve_npu_device_paths, resolve_npu_group_ids


def test_resolve_npu_device_paths_falls_back_to_strix_halo_constants() -> None:
    with patch("hal0.providers._gpu.json.loads", side_effect=OSError("no hardware.json")):
        paths = resolve_npu_device_paths()
    assert paths == ["/dev/accel/accel0", "/dev/dri/renderD128"]


def test_resolve_npu_group_ids_skips_missing_groups() -> None:
    with patch("grp.getgrnam", side_effect=KeyError("no such group")):
        assert resolve_npu_group_ids() == []
