"""``hal0 system-info`` — host / GPU / NPU / runtime evidence (§21.3).

A read-only, offline-capable evidence command. It gathers the facts an
operator (or a bug report) needs to characterise the box in one shot:

* host — hostname, kernel, distro, uptime
* cpu  — model, physical cores, logical threads
* memory — RAM total/available, swap, unified (UMA/GTT) pool
* gpus — every detected GPU (vendor, name, VRAM, driver, compute/vulkan)
* npu  — presence, name, driver, accel/render nodes, AIE columns
* runtime — hal0 version, python (version + interpreter), podman version,
  detected platform string

Unlike ``hal0 config hardware`` (a thin client over ``GET /api/hardware``
that needs the API up), this command runs the local hardware probe
directly, so it works during install / before the daemon is serving. It
never mutates anything and never raises on a partial probe — a missing
detector degrades to an empty section.
"""

from __future__ import annotations

import json as jsonlib
import platform as py_platform
import shutil
import subprocess
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

import hal0

console = Console()


# ── evidence gathering (impure seams, each best-effort) ───────────────────────


def _probe_hardware() -> Any | None:
    """Run the local hardware probe; return a ``HardwareInfo`` or ``None``.

    The probe never raises for individual detector failures (see
    :meth:`HardwareProbe.probe`), so ``None`` here means the probe machinery
    itself is unavailable — we degrade to a runtime-only report rather than
    failing the command.
    """
    try:
        from hal0.hardware.probe import HardwareProbe

        return HardwareProbe().probe()
    except Exception:  # pragma: no cover — probe is best-effort evidence
        return None


def _command_version(argv: tuple[str, ...]) -> str | None:
    """Run ``argv`` and return its first stdout line, or ``None`` when absent.

    Used for external toolchain versions (podman). Never raises: a missing
    binary or a non-zero exit yields ``None`` (rendered as ``not found``).
    """
    exe = shutil.which(argv[0])
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, *argv[1:]],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    first = (proc.stdout or proc.stderr or "").strip().splitlines()
    return first[0].strip() if first else None


# ── pure assembler ────────────────────────────────────────────────────────────


def build_system_info(
    hw: Any | None,
    *,
    hal0_version: str,
    python_version: str,
    python_executable: str,
    podman_version: str | None,
) -> dict[str, Any]:
    """Assemble the evidence payload (pure — takes gathered inputs).

    Kept separate from the impure probes so tests can exercise the shape
    with fixture data and no real hardware.
    """
    host: dict[str, Any] = {}
    cpu: dict[str, Any] = {}
    memory: dict[str, Any] = {}
    gpus: list[dict[str, Any]] = []
    npu: dict[str, Any] = {"present": False}
    platform_str = ""

    if hw is not None:
        host = {
            "hostname": getattr(hw, "hostname", "") or "",
            "kernel": getattr(hw, "kernel", "") or "",
            "distro": getattr(hw, "distro", "") or "",
            "uptime_s": int(getattr(hw, "uptime_s", 0) or 0),
        }
        cpu = {
            "model": getattr(hw, "cpu_model", "") or "",
            "cores": int(getattr(hw, "cpu_cores", 0) or 0),
            "threads": int(getattr(hw, "cpu_threads", 0) or 0),
        }
        memory = {
            "ram_mb": int(getattr(hw, "ram_mb", 0) or 0),
            "ram_available_mb": int(getattr(hw, "ram_available_mb", 0) or 0),
            "swap_mb": int(getattr(hw, "swap_mb", 0) or 0),
            "unified_memory_mb": int(getattr(hw, "unified_memory_mb", 0) or 0),
        }
        for g in getattr(hw, "gpus", []) or []:
            gpus.append(
                {
                    "vendor": getattr(g, "vendor", "") or "",
                    "index": int(getattr(g, "index", 0) or 0),
                    "name": getattr(g, "name", "") or "",
                    "vram_mb": int(getattr(g, "vram_mb", 0) or 0),
                    "driver": getattr(g, "driver", "") or "",
                    "compute_capable": bool(getattr(g, "compute_capable", False)),
                    "vulkan_capable": bool(getattr(g, "vulkan_capable", False)),
                }
            )
        hw_npu = getattr(hw, "npu", None)
        if hw_npu is not None:
            npu = {
                "present": bool(getattr(hw_npu, "present", False)),
                "vendor": getattr(hw_npu, "vendor", "") or "",
                "name": getattr(hw_npu, "name", "") or "",
                "driver": getattr(hw_npu, "driver", "") or "",
                "accel_path": getattr(hw_npu, "accel_path", "") or "",
                "render_path": getattr(hw_npu, "render_path", "") or "",
                "aie_columns": int(getattr(hw_npu, "aie_columns", 0) or 0),
                "validated": getattr(hw_npu, "validated", None),
            }
        platform_str = getattr(hw, "platform", "") or ""

    return {
        "hal0_version": hal0_version,
        "platform": platform_str,
        "host": host,
        "cpu": cpu,
        "memory": memory,
        "gpus": gpus,
        "npu": npu,
        "runtime": {
            "python_version": python_version,
            "python_executable": python_executable,
            "podman_version": podman_version,
        },
        # A truthful marker so a JSON consumer can tell a runtime-only report
        # (probe unavailable) from a full one without re-deriving it.
        "hardware_probe_ok": hw is not None,
    }


# ── rendering ─────────────────────────────────────────────────────────────────


def _mib_to_gib(mb: int) -> str:
    return f"{mb / 1024:.1f} GiB" if mb else "—"


def render_system_info(con: Console, data: dict[str, Any]) -> None:
    """Human-readable tables over the assembled evidence payload."""
    host = data.get("host") or {}
    cpu = data.get("cpu") or {}
    mem = data.get("memory") or {}
    runtime = data.get("runtime") or {}

    summary = Table(title="hal0 system-info", show_header=False)
    summary.add_column("Field", style="bold")
    summary.add_column("Value")
    summary.add_row("hal0", data.get("hal0_version") or "—")
    summary.add_row("platform", data.get("platform") or "—")
    summary.add_row("hostname", host.get("hostname") or "—")
    summary.add_row("kernel", host.get("kernel") or "—")
    summary.add_row("distro", host.get("distro") or "—")
    summary.add_row("cpu", cpu.get("model") or "—")
    cores = cpu.get("cores") or 0
    threads = cpu.get("threads") or 0
    summary.add_row("cores / threads", f"{cores} / {threads}" if cores or threads else "—")
    summary.add_row(
        "memory",
        f"{_mib_to_gib(mem.get('ram_available_mb') or 0)} avail "
        f"/ {_mib_to_gib(mem.get('ram_mb') or 0)} total",
    )
    if mem.get("unified_memory_mb"):
        summary.add_row("unified pool", _mib_to_gib(mem.get("unified_memory_mb") or 0))
    summary.add_row("swap", _mib_to_gib(mem.get("swap_mb") or 0))
    summary.add_row("python", runtime.get("python_version") or "—")
    summary.add_row("podman", runtime.get("podman_version") or "[dim]not found[/dim]")
    if not data.get("hardware_probe_ok"):
        summary.add_row("hardware probe", "[yellow]unavailable — runtime-only report[/yellow]")
    con.print(summary)

    gpus = data.get("gpus") or []
    if gpus:
        gtab = Table(title="GPUs")
        gtab.add_column("#", justify="right")
        gtab.add_column("Vendor")
        gtab.add_column("Name", style="bold")
        gtab.add_column("VRAM/GTT")
        gtab.add_column("Driver")
        gtab.add_column("Compute")
        gtab.add_column("Vulkan")
        for g in gpus:
            gtab.add_row(
                str(g.get("index", 0)),
                g.get("vendor") or "—",
                g.get("name") or "—",
                _mib_to_gib(g.get("vram_mb") or 0),
                g.get("driver") or "—",
                "yes" if g.get("compute_capable") else "no",
                "yes" if g.get("vulkan_capable") else "no",
            )
        con.print(gtab)
    else:
        con.print("[dim]GPUs: none detected.[/dim]")

    npu = data.get("npu") or {}
    if npu.get("present"):
        detail = npu.get("name") or "present"
        driver = npu.get("driver")
        cols = npu.get("aie_columns")
        extras = []
        if driver:
            extras.append(f"driver={driver}")
        if cols:
            extras.append(f"aie_columns={cols}")
        if npu.get("accel_path"):
            extras.append(f"accel={npu['accel_path']}")
        suffix = f"  ({', '.join(extras)})" if extras else ""
        con.print(f"[bold]NPU:[/bold] {detail}{suffix}")
    else:
        con.print("[dim]NPU: not present.[/dim]")


# ── command ───────────────────────────────────────────────────────────────────


def system_info_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the evidence payload as JSON instead of the human tables.",
    ),
) -> None:
    """Print host / GPU / NPU / runtime evidence for this box (read-only).

    Runs the local hardware probe directly (works offline, before the API is
    up) and adds runtime versions (hal0, python, podman). Always exits 0 —
    this is an evidence command, not a health gate; use ``hal0 doctor`` for
    pass/fail checks.
    """
    hw = _probe_hardware()
    data = build_system_info(
        hw,
        hal0_version=hal0.__version__,
        python_version=py_platform.python_version(),
        python_executable=sys.executable or "",
        podman_version=_command_version(("podman", "--version")),
    )
    if json_output:
        console.print_json(jsonlib.dumps(data))
    else:
        render_system_info(console, data)
    raise typer.Exit(0)


__all__ = [
    "build_system_info",
    "render_system_info",
    "system_info_cmd",
]
