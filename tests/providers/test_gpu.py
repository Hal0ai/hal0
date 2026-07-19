"""Tests for GPU device-path resolution (podman/docker passthrough).

Podman cannot recurse a ``--device=/dev/dri`` *directory* the way docker
does (it errors ``no devices found in /dev/dri`` on hosts whose /dev/dri
holds non-standard nodes and no ``card0``). The provider must therefore
pass *explicit* device nodes (``/dev/dri/renderD128`` …). These tests
drive that enumeration.

Real char devices are needed to exercise the ``S_ISCHR`` filter; creating
one needs root (mknod), so we symlink to ``/dev/null`` — a real character
device — which keeps the tests hermetic and root-free.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from hal0.providers import _gpu
from hal0.providers._gpu import resolve_gpu_device_paths, resolve_gpu_group_ids


class TestResolveGpuDevicePaths:
    def test_enumerates_explicit_dri_nodes_not_the_directory(self, tmp_path) -> None:
        """Char-device nodes under /dev/dri are listed explicitly; the bare
        directory is never passed (that is what breaks podman)."""
        dri = tmp_path / "dri"
        dri.mkdir()
        (dri / "renderD128").symlink_to("/dev/null")  # real char device
        (dri / "amdgpu").symlink_to("/dev/null")
        (dri / "by-path").mkdir()  # subdir — must be excluded
        (dri / "README").write_text("not a device")  # regular file — excluded
        kfd = tmp_path / "kfd"
        kfd.symlink_to("/dev/null")

        paths = resolve_gpu_device_paths(kfd_path=str(kfd), dri_dir=str(dri))

        assert str(kfd) in paths
        assert str(dri / "renderD128") in paths
        assert str(dri / "amdgpu") in paths
        assert str(dri / "by-path") not in paths
        assert str(dri / "README") not in paths
        # The directory itself must NOT be emitted — the whole point of the fix.
        assert str(dri) not in paths

    def test_kfd_included_only_when_present(self, tmp_path) -> None:
        dri = tmp_path / "dri"
        dri.mkdir()
        (dri / "renderD128").symlink_to("/dev/null")
        missing_kfd = tmp_path / "no-kfd"

        paths = resolve_gpu_device_paths(kfd_path=str(missing_kfd), dri_dir=str(dri))

        assert str(missing_kfd) not in paths
        assert str(dri / "renderD128") in paths

    def test_falls_back_to_legacy_dirs_on_non_gpu_host(self, tmp_path) -> None:
        """When neither /dev/kfd nor /dev/dri exist (CI / no-GPU dev box),
        return the legacy directory paths so unit rendering stays
        deterministic — no container actually runs there."""
        paths = resolve_gpu_device_paths(
            kfd_path=str(tmp_path / "kfd"),
            dri_dir=str(tmp_path / "dri"),
        )
        assert paths == ["/dev/kfd", "/dev/dri"]


class TestResolveGpuGroupIds:
    """halo143 regression: ``getgrnam("render")`` resolves the group NAMED
    "render" (991 there), not the gid that actually owns ``renderD128``
    (993 there — named "clock" on that host). The kernel gates on the
    node's owning gid, not the name, so that must be the primary source.
    """

    def _fake_stat(self, table: dict[str, int]):
        real_stat = os.stat

        def _stat(path, *a, **kw):
            if path in table:
                return SimpleNamespace(st_gid=table[path])
            return real_stat(path, *a, **kw)

        return _stat

    def test_stat_based_gid_wins_over_mismatched_group_name(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _gpu,
            "resolve_gpu_device_paths",
            lambda: ["/dev/kfd", "/dev/dri/renderD128", "/dev/dri/card0"],
        )
        monkeypatch.setattr(
            _gpu.os,
            "stat",
            self._fake_stat({"/dev/dri/renderD128": 993, "/dev/dri/card0": 44}),
        )

        def _wrong_name_lookup(name):
            # Simulates halo143: gid 991 is really named "render" here, but
            # it is NOT the gid that owns renderD128 — must not be used
            # when the node stat already resolved a gid.
            raise AssertionError(
                f"getgrnam({name!r}) must not be consulted when the device "
                "node stat already resolved a gid"
            ) from None

        import grp

        monkeypatch.setattr(grp, "getgrnam", _wrong_name_lookup)

        assert resolve_gpu_group_ids() == [993, 44]

    def test_falls_back_to_group_name_when_node_absent(self, monkeypatch) -> None:
        """CI / no-GPU box: resolve_gpu_device_paths returns the legacy
        directory fallback (no renderD*/card* nodes) — name lookup applies."""
        monkeypatch.setattr(_gpu, "resolve_gpu_device_paths", lambda: ["/dev/kfd", "/dev/dri"])

        import grp

        monkeypatch.setattr(
            grp,
            "getgrnam",
            lambda name: SimpleNamespace(gr_gid={"render": 111, "video": 222}[name]),
        )

        assert resolve_gpu_group_ids() == [111, 222]

    def test_falls_back_to_probed_record_when_node_and_name_both_miss(self, monkeypatch) -> None:
        monkeypatch.setattr(_gpu, "resolve_gpu_device_paths", lambda: ["/dev/kfd", "/dev/dri"])

        import grp

        def _missing(name):
            raise KeyError(name)

        monkeypatch.setattr(grp, "getgrnam", _missing)
        monkeypatch.setattr(_gpu, "_probed_gpu_group_gids", lambda: {"render": 777, "video": 8})

        assert resolve_gpu_group_ids() == [777, 8]

    def test_falls_back_to_constants_when_nothing_resolves(self, monkeypatch) -> None:
        monkeypatch.setattr(_gpu, "resolve_gpu_device_paths", lambda: ["/dev/kfd", "/dev/dri"])

        import grp

        def _missing(name):
            raise KeyError(name)

        monkeypatch.setattr(grp, "getgrnam", _missing)
        monkeypatch.setattr(_gpu, "_probed_gpu_group_gids", lambda: {})

        assert resolve_gpu_group_ids() == [993, 44]

    def test_node_stat_failure_falls_through_to_group_name(self, monkeypatch) -> None:
        """A discovered node that can't be stat'd (raced away, permission
        denied) must not blow up — falls to the next source instead."""
        monkeypatch.setattr(
            _gpu, "resolve_gpu_device_paths", lambda: ["/dev/dri/renderD128", "/dev/dri/card0"]
        )

        def _raise(path, *a, **kw):
            raise OSError("gone")

        monkeypatch.setattr(_gpu.os, "stat", _raise)

        import grp

        monkeypatch.setattr(
            grp,
            "getgrnam",
            lambda name: SimpleNamespace(gr_gid={"render": 111, "video": 222}[name]),
        )

        assert resolve_gpu_group_ids() == [111, 222]

    def test_dedups_shared_gid_preserving_order(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _gpu,
            "resolve_gpu_device_paths",
            lambda: ["/dev/dri/renderD128", "/dev/dri/card0"],
        )
        monkeypatch.setattr(
            _gpu.os,
            "stat",
            self._fake_stat({"/dev/dri/renderD128": 993, "/dev/dri/card0": 993}),
        )

        assert resolve_gpu_group_ids() == [993]
