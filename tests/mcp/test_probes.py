"""Unit tests for :mod:`hal0.mcp.probes` (issue #237).

Probes read from /sys + /proc + lsmod, all of which are awkward to fake
without monkeypatching. Each test substitutes either a tmp-path
filesystem root or a captured-argv subprocess shim so the assertions
stay deterministic regardless of the host the suite runs on.

The integration story is the dispatch round-trip via
:mod:`hal0.mcp.admin` — :func:`test_admin_dispatches_probe_in_process`
confirms a probe tool name in the admin catalog ends up calling our
in-process dispatcher (no REST hop, no httpx call).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.mcp import admin, probes
from hal0.mcp.approval_queue import ApprovalQueue

# ── gpu_target_version ───────────────────────────────────────────────────────


def _write_kfd_node(root: Path, node_id: int, target_version: int) -> None:
    node = root / "sys" / "class" / "kfd" / "kfd" / "topology" / "nodes" / str(node_id)
    node.mkdir(parents=True, exist_ok=True)
    (node / "properties").write_text(
        f"simd_count 80\ngfx_target_version {target_version}\ndevice_id 5510\n",
        encoding="utf-8",
    )


def test_gpu_target_version_decodes_strix_halo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Node 0 is CPU (target=0), node 1 is the iGPU (target=110501 → gfx1151).
    _write_kfd_node(tmp_path, 0, 0)
    _write_kfd_node(tmp_path, 1, 110501)
    monkeypatch.setattr(
        probes,
        "Path",
        lambda p: Path(str(tmp_path) + p) if str(p).startswith("/sys/class/kfd") else Path(p),
    )
    out = probes.gpu_target_version()
    assert out["present"] is True
    assert out["raw"] == 110501
    assert out["gfx"] == "gfx1151"


def test_gpu_target_version_missing_kfd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Point the probe at an empty tree — no kfd nodes.
    monkeypatch.setattr(
        probes,
        "Path",
        lambda p: Path(str(tmp_path) + p) if str(p).startswith("/sys/class/kfd") else Path(p),
    )
    out = probes.gpu_target_version()
    assert out["present"] is False
    assert "amdkfd" in out["reason"]
    assert out["gfx"] is None


# ── npu_status ───────────────────────────────────────────────────────────────


def test_npu_status_strix_halo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Fake the device node, the lsmod output, and the PCI driver dir.
    (tmp_path / "dev" / "accel").mkdir(parents=True)
    (tmp_path / "dev" / "accel" / "accel0").write_text("")
    pci_dir = tmp_path / "sys" / "bus" / "pci" / "drivers" / "amdxdna" / "0000:c0:00.1"
    pci_dir.mkdir(parents=True)
    (pci_dir.parent / "0000:c0:00.1" / "uevent").write_text(
        "DRIVER=amdxdna\nPCI_ID=1022:17F0\n", encoding="utf-8"
    )

    real_path = probes.Path

    def _path(p: Any) -> Path:
        s = str(p)
        if s.startswith("/dev/accel") or s.startswith("/sys/bus/pci"):
            return real_path(str(tmp_path) + s)
        return real_path(p)

    monkeypatch.setattr(probes, "Path", _path)

    def _fake_run(argv: list[str], *, timeout: float = 5.0) -> tuple[int, str, str]:
        if argv == ["lsmod"]:
            return (0, "amdxdna 159744 3\namd_pmf 106496 1 amdxdna\n", "")
        return (-1, "", "")

    monkeypatch.setattr(probes, "_run", _fake_run)

    out = probes.npu_status()
    assert out["present"] is True
    assert out["device_node"] == "/dev/accel/accel0"
    assert out["driver_loaded"] is True
    assert out["pci_id"] == "1022:17F0"
    assert out["xdna_gen"] == 2


def test_npu_status_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No /dev/accel, no PCI driver dir, no amdxdna in lsmod.
    real_path = probes.Path
    monkeypatch.setattr(probes, "Path", lambda p: real_path(str(tmp_path) + str(p)))
    monkeypatch.setattr(probes, "_run", lambda *a, **kw: (0, "ext4 100\n", ""))
    out = probes.npu_status()
    assert out["present"] is False
    assert out["driver_loaded"] is False
    assert out["pci_id"] is None
    assert out["xdna_gen"] is None


# ── model_store_probe ────────────────────────────────────────────────────────


def test_model_store_probe_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probes, "_run", lambda *a, **kw: (0, "zfs\n", ""))
    out = probes.model_store_probe(str(tmp_path))
    assert out["exists"] is True
    assert out["fstype"] == "zfs"
    assert out["writable"] is True
    assert out["total_bytes"] > 0
    assert out["is_uma_aware"] is True


def test_model_store_probe_missing_path() -> None:
    out = probes.model_store_probe("/nonexistent/path/xyz")
    assert out["exists"] is False
    assert "not found" in out["reason"]


def test_model_store_probe_nfs_not_uma_aware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probes, "_run", lambda *a, **kw: (0, "nfs4\n", ""))
    out = probes.model_store_probe(str(tmp_path))
    assert out["fstype"] == "nfs4"
    assert out["is_uma_aware"] is False


# ── env_report ───────────────────────────────────────────────────────────────


def test_env_report_returns_full_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    # Don't try to fake every sysfs file — just confirm the report
    # surfaces every documented section + the schema-version stamp.
    out = probes.env_report()
    for section in ("container", "cpu", "ram", "gpu", "npu", "network", "tooling"):
        assert section in out, f"env_report missing section {section!r}"
    assert out["schema_version"] == 1


def test_env_report_cpu_strix_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    real_read = probes._read_text

    def _read(path: Any, *, default: str = "") -> str:
        if str(path) == "/proc/cpuinfo":
            return "model name\t: AMD RYZEN AI MAX+ 395 w/ Radeon 8060S\nflags\t\t: avx2 avx512f\n"
        return real_read(path, default=default)

    monkeypatch.setattr(probes, "_read_text", _read)
    out = probes._probe_cpu()
    assert out["strix_halo"] is True
    assert "RYZEN AI MAX" in out["model"].upper()


# ── Dispatcher ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_probe_routes_each_tool() -> None:
    out = await probes.dispatch_probe("gpu_target_version", {})
    assert "gfx" in out  # may be None on non-AMD host; key must exist
    out = await probes.dispatch_probe("npu_status", {})
    assert "present" in out
    out = await probes.dispatch_probe("env_report", {})
    assert "schema_version" in out


@pytest.mark.asyncio
async def test_dispatch_probe_model_store_probe_requires_path() -> None:
    out = await probes.dispatch_probe("model_store_probe", {})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.missing_arg"


@pytest.mark.asyncio
async def test_dispatch_probe_unknown_tool() -> None:
    out = await probes.dispatch_probe("not_a_probe", {})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.unknown_probe"


# ── Admin integration ───────────────────────────────────────────────────────


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


def test_probe_tools_are_autonomous_read() -> None:
    for name in ("gpu_target_version", "npu_status", "env_report", "model_store_probe"):
        assert name in admin.AUTONOMOUS_READ_TOOLS


@pytest.mark.asyncio
async def test_admin_registers_probe_tools(queue: ApprovalQueue) -> None:
    server = admin.build_server(approval_queue=queue, base_url="http://t")
    tools = await server.list_tools()
    registered = {t.name for t in tools}
    for name in ("gpu_target_version", "npu_status", "env_report", "model_store_probe"):
        assert name in registered


@pytest.mark.asyncio
async def test_admin_dispatches_probe_in_process(queue: ApprovalQueue) -> None:
    # env_report doesn't take args + returns the dict shape regardless of
    # what /sys looks like. Round-tripping through admin.dispatch proves
    # the route is wired without needing the REST mock.
    out = await admin.dispatch(
        tool="env_report",
        args={},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert isinstance(out, dict)
    assert "schema_version" in out
