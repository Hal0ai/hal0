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

from hal0.providers._gpu import resolve_gpu_device_paths


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
