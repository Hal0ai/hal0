"""Tests for podman cgroup mem probe in hal0.slots.capacity.

Covers :func:`hal0.slots.capacity._container_cgroup_mem_bytes` — the
function that reads live ``memory.current`` for a podman/docker container
named ``hal0-slot-<name>``.  All subprocess and file I/O is mocked so
these tests run without a real container runtime.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from hal0.slots.capacity import _container_cgroup_mem_bytes, build_per_slot

# ── helpers ────────────────────────────────────────────────────────────────


def _make_proc(returncode: int = 0, stdout: bytes = b"") -> AsyncMock:
    """Return a fake asyncio.subprocess.Process mock."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


# ── _container_cgroup_mem_bytes ─────────────────────────────────────────────


class TestContainerCgroupMemBytes:
    """Unit tests for the podman cgroup probe."""

    @pytest.mark.asyncio
    async def test_returns_bytes_on_happy_path(self, tmp_path):
        """Full happy-path: podman inspect → PID → cgroup path → memory.current."""
        cg_rel = "system.slice/libpod-abc123.scope"
        cg_line = f"0::/{cg_rel}\n"
        mem_current = "12345678\n"

        def _open_side_effect(path, *args, **kwargs):
            # /proc/<pid>/cgroup: contains "cgroup" but NOT "memory"
            if "cgroup" in str(path) and "memory" not in str(path):
                return mock_open(read_data=cg_line)()
            # /sys/fs/cgroup/.../memory.current: contains "memory.current"
            if "memory.current" in str(path):
                return mock_open(read_data=mem_current)()
            raise FileNotFoundError(path)

        with (
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("builtins.open", side_effect=_open_side_effect),
        ):
            mock_exec.return_value = _make_proc(returncode=0, stdout=b"12345\n")
            result = await _container_cgroup_mem_bytes("primary")

        assert result == 12345678

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_runtime(self):
        """No podman or docker binary → 0."""
        with patch("shutil.which", return_value=None):
            result = await _container_cgroup_mem_bytes("primary")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_container_not_found(self):
        """Container inspect returns non-zero (container doesn't exist) → 0."""
        with (
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_exec.return_value = _make_proc(returncode=1, stdout=b"")
            result = await _container_cgroup_mem_bytes("primary")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_pid_is_zero(self):
        """Inspect returns PID 0 (stopped container) → 0."""
        with (
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_exec.return_value = _make_proc(returncode=0, stdout=b"0\n")
            result = await _container_cgroup_mem_bytes("primary")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_inspect_timeout(self):
        """asyncio.wait_for timeout during inspect → 0."""
        with (
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("asyncio.wait_for", side_effect=TimeoutError),
        ):
            mock_exec.return_value = _make_proc(returncode=0, stdout=b"12\n")
            result = await _container_cgroup_mem_bytes("primary")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_cgroup_v1(self):
        """cgroupv1 line lacks '::' → probe cannot walk path → 0."""
        cg_line = "1:cpu,cpuacct:/system.slice/podman-abc.scope\n"

        def _open_side_effect(path, *args, **kwargs):
            if "cgroup" in str(path):
                return mock_open(read_data=cg_line)()
            raise FileNotFoundError(path)

        with (
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("builtins.open", side_effect=_open_side_effect),
        ):
            mock_exec.return_value = _make_proc(returncode=0, stdout=b"99\n")
            result = await _container_cgroup_mem_bytes("primary")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_memory_current_unreadable(self):
        """memory.current read fails (OSError) → 0."""
        cg_line = "0::/system.slice/libpod-abc.scope\n"

        def _open_side_effect(path, *args, **kwargs):
            if "cgroup" in str(path) and "memory" not in str(path):
                return mock_open(read_data=cg_line)()
            raise OSError("no such file")

        with (
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("builtins.open", side_effect=_open_side_effect),
        ):
            mock_exec.return_value = _make_proc(returncode=0, stdout=b"42\n")
            result = await _container_cgroup_mem_bytes("primary")
        assert result == 0

    @pytest.mark.asyncio
    async def test_docker_fallback_when_podman_absent(self):
        """Falls back to docker when podman is not found."""
        cg_rel = "system.slice/docker-xyz.scope"
        cg_line = f"0::/{cg_rel}\n"
        mem_current = "999999\n"

        def _which(cmd):
            return "/usr/bin/docker" if cmd == "docker" else None

        def _open_side_effect(path, *args, **kwargs):
            if "cgroup" in str(path) and "memory" not in str(path):
                return mock_open(read_data=cg_line)()
            if "memory.current" in str(path):
                return mock_open(read_data=mem_current)()
            raise FileNotFoundError(path)

        with (
            patch("shutil.which", side_effect=_which),
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("builtins.open", side_effect=_open_side_effect),
        ):
            mock_exec.return_value = _make_proc(returncode=0, stdout=b"77\n")
            result = await _container_cgroup_mem_bytes("primary")

        assert result == 999999
        # Verify docker was called (not podman)
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "/usr/bin/docker"


# ── build_per_slot integration ──────────────────────────────────────────────


class TestBuildPerSlotContainerPath:
    """Verify build_per_slot uses cgroup bytes for container slots
    and falls back to file-size estimate when the cgroup probe is empty."""

    def _make_slot(self, name, state="ready", model_id="mymodel", backend="rocm"):
        slot = MagicMock()
        slot.name = name
        slot.state = state
        slot.model_id = model_id
        slot.backend = backend
        slot.metadata = {"provider": "llama-server", "backend": backend}
        return slot

    @pytest.mark.asyncio
    async def test_container_slot_uses_cgroup_bytes(self):
        """When cgroup exceeds the (zero) estimate, build_per_slot uses the cgroup value."""
        slot = self._make_slot("primary")
        cgroup_bytes = 20 * 1024 * 1024 * 1024  # 20 GiB

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=cgroup_bytes,
        ):
            result = await build_per_slot([slot])

        assert "primary" in result
        row = result["primary"]
        expected_mb = round(cgroup_bytes / (1024.0 * 1024.0), 1)
        assert row["mem_mb"] == expected_mb
        assert row["vram_mb"] == expected_mb
        assert row["ram_mb"] == 0.0

    @pytest.mark.asyncio
    async def test_container_under_report_uses_estimate_floor(self):
        """#672 regression: Strix Halo GTT weights not charged to cgroup.

        When the cgroup reports a small value (~2 GiB runtime/buffers) but the
        model is large (~22 GiB), mem_mb must be the registry estimate, NOT the
        too-low cgroup value — otherwise the map under-reports container memory.
        """
        slot = self._make_slot("primary-container")
        small_cgroup = 2 * 1024 * 1024 * 1024  # 2 GiB (GTT weights NOT charged)

        model_mock = MagicMock()
        model_mock.size_bytes = 22 * 1024 * 1024 * 1024  # 22 GiB model
        model_mock.model_dump = lambda: {}
        registry = MagicMock()
        registry.get = MagicMock(return_value=model_mock)

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=small_cgroup,
        ):
            result = await build_per_slot([slot], registry=registry)

        row = result["primary-container"]
        cgroup_mb = small_cgroup / (1024.0 * 1024.0)
        file_mb = model_mock.size_bytes / (1024 * 1024)
        # Estimate (file size + KV) must win over the under-reporting cgroup.
        assert row["mem_mb"] >= file_mb
        assert row["mem_mb"] > cgroup_mb

    @pytest.mark.asyncio
    async def test_container_cgroup_wins_when_above_estimate(self):
        """When the cgroup DOES account for weights it exceeds the estimate and wins."""
        slot = self._make_slot("primary-container")
        big_cgroup = 24 * 1024 * 1024 * 1024  # 24 GiB (weights charged + overhead)

        model_mock = MagicMock()
        model_mock.size_bytes = 22 * 1024 * 1024 * 1024  # 22 GiB model
        model_mock.model_dump = lambda: {}
        registry = MagicMock()
        registry.get = MagicMock(return_value=model_mock)

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=big_cgroup,
        ):
            result = await build_per_slot([slot], registry=registry)

        row = result["primary-container"]
        expected_mb = round(big_cgroup / (1024.0 * 1024.0), 1)
        assert row["mem_mb"] == expected_mb

    @pytest.mark.asyncio
    async def test_empty_cgroup_probe_falls_back_to_registry(self):
        """When cgroup probe returns 0, build_per_slot uses registry file size."""
        slot = self._make_slot("primary")
        model_mock = MagicMock()
        model_mock.size_bytes = 15 * 1024 * 1024 * 1024  # 15 GiB
        model_mock.model_dump = lambda: {}

        registry = MagicMock()
        registry.get = MagicMock(return_value=model_mock)

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await build_per_slot([slot], registry=registry)

        assert "primary" in result
        row = result["primary"]
        # Registry file size in MiB (no KV estimate for 0-token default context)
        file_mb = model_mock.size_bytes / (1024 * 1024)
        # KV estimate is added — just check it's ≥ file_mb
        assert row["mem_mb"] >= file_mb

    @pytest.mark.asyncio
    async def test_npu_slot_skips_cgroup_probe(self):
        """NPU/FLM slots use the FLM footprint path and never call the cgroup probe."""
        slot = self._make_slot("npu-chat", backend="flm")
        slot.metadata = {"provider": "flm", "backend": "flm"}

        flm_catalog = {
            "mymodel": {"footprint_gb": 4.5, "size_bytes": 0},
        }

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=99999,  # should never be called
        ) as mock_probe:
            result = await build_per_slot([slot], flm_catalog=flm_catalog)

        mock_probe.assert_not_called()
        assert "npu-chat" in result
        assert result["npu-chat"]["mem_mb"] == round(4.5 * 1024, 1)

    @pytest.mark.asyncio
    async def test_offline_slot_omitted(self):
        """Slots in non-resident states produce no row."""
        slot = self._make_slot("primary", state="offline")
        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await build_per_slot([slot])
        assert result == {}
