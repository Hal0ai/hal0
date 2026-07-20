"""hal0 slot subcommands — thin HTTP client to the hal0 API.

One deliberate exception: ``migrate-id-keying`` (bottom of file) is offline /
filesystem-direct, not an API call — it flips the on-disk layout the (stopped)
API reads on its next boot, so routing it through the API would be
nonsensical (see that command's docstring).
"""

from __future__ import annotations

import json as jsonlib
import subprocess
import tarfile
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_delete,
    api_get,
    api_post,
    api_put,
    die,
    follow_sse_logs,
)
from hal0.hardware.stats import SLOT_PORT_RANGE_END, SLOT_PORT_RANGE_START

app = typer.Typer(help="Manage inference slots.")
console = Console()


class SlotProvider(StrEnum):
    """Providers valid for a slot (mirrors PLAN.md §1 provider list).

    A *provider* is the inference engine binary that serves the slot
    (e.g. llama-server, flm). This is distinct from a slot's *hardware*
    backend (vulkan / rocm / cpu) which targets the compute device.
    """

    llama_server = "llama-server"
    flm = "flm"
    moonshine = "moonshine"
    kokoro = "kokoro"


# Back-compat alias — older code/docs referenced ``SlotBackend`` for what
# is semantically the provider. Keep the name importable so external
# callers don't break; new code should reference ``SlotProvider``.
SlotBackend = SlotProvider


class SlotType(StrEnum):
    """Slot type enum (#275 bug 3).

    Maps to the slot-type vocab from PLAN.md §1 v0.2 slot model:
    ``llm | embedding | reranking | transcription | tts | image``.
    The dispatcher routes by ``type``; without this
    flag the CLI couldn't create embedding/rerank/transcription/tts
    slots at all.
    """

    llm = "llm"
    embedding = "embedding"
    reranking = "reranking"
    transcription = "transcription"
    tts = "tts"
    image = "image"


class SlotHardware(StrEnum):
    """Hardware backends valid for a slot (mirrors SlotConfig.backend).

    See ``hal0.config.schema._VALID_BACKENDS``. ``vulkan`` works on any
    Vulkan-capable GPU (AMD/NVIDIA/Intel); ``rocm`` requires AMD with
    ROCm; ``cpu`` is the fallback.
    """

    vulkan = "vulkan"
    rocm = "rocm"
    cpu = "cpu"


#: The `device` enum (gpu-vulkan / gpu-rocm / cpu / npu) derives from the
#: v0.1 hardware enum: vulkan/rocm -> gpu-vulkan/gpu-rocm; cpu stays cpu;
#: npu has no v0.1 hardware equivalent. Shared by ``slot_create`` and
#: ``slot_edit`` so both CLI paths write ``device`` (the sole persisted
#: truth) instead of the deprecated ``backend`` mirror.
_HARDWARE_TO_DEVICE: dict[str, str] = {
    "vulkan": "gpu-vulkan",
    "rocm": "gpu-rocm",
    "cpu": "cpu",
}


def _detect_default_hardware() -> str:
    """Pick a sane default hardware backend from /etc/hal0/hardware.json.

    Falls back to ``"vulkan"`` when the probe file is missing or
    unreadable — that's the broadest match for AMD/NVIDIA/Intel GPUs and
    preserves the historical hardcoded default for users without a probe.
    """
    try:
        from hal0.config import paths as _paths
    except ImportError:
        return "vulkan"
    try:
        raw = _paths.hardware_json().read_text()
    except OSError:
        return "vulkan"
    try:
        data = jsonlib.loads(raw)
    except ValueError:
        return "vulkan"
    gpus = data.get("gpus") or []
    if not gpus:
        return "cpu"
    g = gpus[0] if isinstance(gpus[0], dict) else {}
    vendor = (g.get("vendor") or "").lower()
    if vendor == "amd" and g.get("compute_capable"):
        return "rocm"
    if g.get("vulkan_capable") or vendor in ("amd", "nvidia", "intel"):
        return "vulkan"
    return "cpu"


_STATE_STYLES = {
    "ready": "bold green",
    "serving": "bold green",
    "running": "bold green",
    "warming": "yellow",
    "starting": "yellow",
    "idle": "cyan",
    "error": "bold red",
    "offline": "dim",
    "unloading": "dim",
}


def _fmt_state(state: str | None) -> str:
    if not state:
        return "[dim]—[/dim]"
    style = _STATE_STYLES.get(state, "white")
    return f"[{style}]{state}[/{style}]"


@app.command("list")
def slot_list(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/slots JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """List all configured slots and their current state."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        slots = api_get("/api/slots")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(slots, indent=2))
        return
    table = Table(title="hal0 slots")
    table.add_column("Name", style="bold")
    table.add_column("State")
    table.add_column("Model")
    table.add_column("Backend")
    table.add_column("Port", justify="right")
    table.add_column("Kind", style="dim")
    if not slots:
        console.print("[dim]No slots configured.[/dim]")
        return
    for s in slots:
        table.add_row(
            s.get("name", "—"),
            _fmt_state(s.get("status") or s.get("state")),
            (s.get("model") or s.get("model_id") or "—") or "—",
            s.get("backend", "—") or "—",
            str(s.get("port") or "—"),
            s.get("kind", "—") or "—",
        )
    console.print(table)


def _drift_warning(status: dict[str, Any]) -> str | None:
    drift = status.get("config_drift") or {}
    if not isinstance(drift, dict) or drift.get("drifted") is not True:
        return None
    diffs = drift.get("diffs") or []
    parts: list[str] = []
    if isinstance(diffs, list):
        for diff in diffs:
            if not isinstance(diff, dict):
                continue
            parts.append(
                f"{diff.get('key')}: running={diff.get('running')} rendered={diff.get('rendered')}"
            )
    detail = "; ".join(parts) if parts else "running argv differs from rendered config"
    return f"WARN config drift: {detail}"


@app.command("status")
def slot_status(
    name: str = typer.Argument(..., help="Slot name to inspect"),
    json_out: bool = typer.Option(False, "--json", help="Emit raw /api/slots/{name} JSON."),
) -> None:
    """Show a slot status summary, including config-drift warnings."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        status = api_get(f"/api/slots/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(status, indent=2))
        return

    table = Table(title=f"hal0 slot: {name}")
    table.add_column("State")
    table.add_column("Model")
    table.add_column("Port", justify="right")
    table.add_row(
        _fmt_state(status.get("status") or status.get("state")),
        (status.get("model") or status.get("model_id") or "—") or "—",
        str(status.get("port") or "—"),
    )
    console.print(table)
    warning = _drift_warning(status)
    if warning:
        console.print(f"[bold yellow]{warning}[/bold yellow]")


@app.command("load")
def slot_load(
    name: str = typer.Argument(..., help="Slot name (e.g. primary)"),
    model: str | None = typer.Option(
        None, "--model", "-m", help="Model ref to assign before loading"
    ),
) -> None:
    """Load a slot (optionally assign a model first)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        body = {"model_id": model} if model else {}
        snap = api_post(f"/api/slots/{name}/load", json=body)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        f"Loaded [bold]{name}[/bold] → state={_fmt_state(snap.get('state'))} model={snap.get('model_id', '—')}"
    )


@app.command("unload")
def slot_unload(
    name: str = typer.Argument(..., help="Slot name to unload"),
) -> None:
    """Unload a running slot gracefully."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        snap = api_post(f"/api/slots/{name}/unload")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Unloaded [bold]{name}[/bold] → state={_fmt_state(snap.get('state'))}")


@app.command("restart")
def slot_restart(
    name: str = typer.Argument(..., help="Slot name to restart"),
) -> None:
    """Restart a slot (unload then load)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        snap = api_post(f"/api/slots/{name}/restart")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Restarted [bold]{name}[/bold] → state={_fmt_state(snap.get('state'))}")


@app.command("rename")
def slot_rename(
    name: str = typer.Argument(..., help="Current slot name"),
    new_name: str = typer.Argument(..., help="New slot name"),
) -> None:
    """Rename a slot in place (POST /api/slots/{name}/rename).

    The slot's ``id`` is stable across the rename — quadlets, port claims,
    and history stay bound to the id, not the label; only the display name
    changes.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        snap = api_post(f"/api/slots/{name}/rename", json={"new_name": new_name})
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Renamed [bold]{name}[/bold] → [bold]{snap.get('name', new_name)}[/bold]")


@app.command("swap")
def slot_swap(
    name: str = typer.Argument(..., help="Slot name to swap"),
    model: str = typer.Option(..., "--model", "-m", help="Model ref to swap in"),
    no_persist: bool = typer.Option(
        False,
        "--no-persist",
        help="Hot-swap only — don't update /etc/hal0/slots/<slot>.toml.",
    ),
) -> None:
    """Swap a slot's model, and (by default) make the change survive a restart.

    Two distinct steps:
      1. Hot-swap — POST /api/slots/{name}/swap          (runtime)
      2. Persist  — PUT  /api/install/slots/{name}/model (on-disk default)

    Pass ``--no-persist`` to skip step 2 (try a model briefly without
    changing the default).  If step 1 succeeds but step 2 fails, the
    runtime swap is left in place and the failure is surfaced so the
    operator can retry persist after fixing the cause.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        snap = api_post(f"/api/slots/{name}/swap", json={"model_id": model})
    except CliApiError as exc:
        die(str(exc))
        return

    swapped_id = snap.get("model_id", model)
    state = _fmt_state(snap.get("state"))

    if no_persist:
        console.print(
            f"Swapped [bold]{name}[/bold] → {swapped_id} state={state} "
            "[dim](runtime only; --no-persist set)[/dim]"
        )
        return

    try:
        api_put(f"/api/install/slots/{name}/model", json={"model_id": model})
    except CliApiError as exc:
        console.print(f"Swapped [bold]{name}[/bold] → {swapped_id} state={state}")
        console.print(f"[yellow]Warning:[/yellow] runtime swap succeeded but persist failed: {exc}")
        console.print(f"[dim]Change will revert on next restart of hal0-slot@{name}.service.[/dim]")
        raise typer.Exit(1) from None

    console.print(
        f"Swapped [bold]{name}[/bold] → {swapped_id} state={state} [dim](persisted)[/dim]"
    )


@app.command("logs")
def slot_logs(
    name: str = typer.Argument(..., help="Slot name whose logs to stream"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream logs (SSE tail)"),
    lines: int = typer.Option(200, "--lines", "-n", min=1, max=5000),
) -> None:
    """Print or follow logs for a slot."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    if not follow:
        try:
            data = api_get(f"/api/slots/{name}/logs", params={"lines": lines})
        except CliApiError as exc:
            die(str(exc))
            return
        console.print(data.get("logs") or "[dim]no logs[/dim]")
        return

    # Stream SSE — line-buffered passthrough.
    follow_sse_logs(f"/api/slots/{name}/logs/stream", console=console)


@app.command("create")
def slot_create(
    name: str = typer.Argument(..., help="Slot name (e.g. primary, embed, stt)"),
    type_: SlotType = typer.Option(
        SlotType.llm,
        "--type",
        "-t",
        help=(
            "Slot type: llm | embedding | reranking | transcription | tts | image. "
            "Determines how the dispatcher routes requests."
        ),
        case_sensitive=False,
    ),
    provider: SlotProvider = typer.Option(
        "llama-server",
        "--provider",
        help=(
            "[Legacy v0.1] Inference provider (engine) for the slot. "
            "Since v0.2, provider is determined by --type; "
            "this flag is preserved for backward-compat with older slot TOMLs."
        ),
        case_sensitive=False,
    ),
    hardware: SlotHardware | None = typer.Option(
        None,
        "--hardware",
        help=(
            "Hardware backend: vulkan | rocm | cpu. "
            "Default: auto-detected from /etc/hal0/hardware.json (vulkan if no probe). "
            "The `device` field is derived: vulkan→gpu-vulkan, rocm→gpu-rocm, cpu→cpu."
        ),
        case_sensitive=False,
    ),
    # HAL0-SUNSET: v1.0.0 — --backend renamed to --provider in v0.2; use --provider.
    backend: str | None = typer.Option(
        None,
        "--backend",
        "-b",
        help=(
            "[DEPRECATED] alias for --provider. "
            "Note: this flag historically named the provider, NOT the hardware "
            "backend. Use --provider / --hardware instead."
        ),
        hidden=True,
    ),
    model: str = typer.Option(..., "--model", "-m", help="Initial model ref to assign."),
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help=(
            "Slot port (default: auto-assign next free port in "
            f"{SLOT_PORT_RANGE_START}-{SLOT_PORT_RANGE_END})."
        ),
        min=1024,
        max=65535,
    ),
    ctx_size: int = typer.Option(4096, "--ctx-size", min=128),
) -> None:
    """Create a new slot config (POST /api/slots)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    # Back-compat: --backend was historically the provider name. Translate
    # with a deprecation warning so existing scripts keep working but the
    # user is nudged toward the corrected flags. We emit to stderr via
    # ``typer.echo(..., err=True)`` so stdout stays parseable for callers
    # piping the success line into other tools.
    if backend is not None:
        typer.echo(
            "[deprecated] --backend will be renamed to --provider in v0.2; use --provider",
            err=True,
        )
        try:
            provider = SlotProvider(backend)
        except ValueError:
            die(
                f"--backend {backend!r} is not a valid provider; "
                f"choose from {[p.value for p in SlotProvider]}"
            )
            return

    hw = hardware.value if hardware is not None else _detect_default_hardware()
    # npu has no v0.1 hardware equivalent (set --hardware via the legacy
    # schema upgrade path); unmapped tokens pass through unchanged.
    device = _HARDWARE_TO_DEVICE.get(hw, hw)
    body: dict[str, Any] = {
        "name": name,
        "type": type_.value,
        "device": device,
        "provider": str(provider),
        "model": {"default": model, "context_size": ctx_size},
    }
    if port is not None:
        body["port"] = port
    else:
        # Best-effort: pick first free port in the shared slot pool by asking
        # the API. Bounds come from hardware.stats so the CLI scan can never
        # drift from the API's _next_free_slot_port pool again.
        try:
            existing = api_get("/api/slots")
            used = {int(s.get("port") or 0) for s in existing}
            for p in range(SLOT_PORT_RANGE_START, SLOT_PORT_RANGE_END + 1):
                if p not in used:
                    body["port"] = p
                    break
        except CliApiError:
            body["port"] = SLOT_PORT_RANGE_START
    try:
        snap = api_post("/api/slots", json=body)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Created slot [bold]{name}[/bold] on port {snap.get('port')} (model={model})")


@app.command("edit")
def slot_edit(
    name: str = typer.Argument(..., help="Slot name to edit"),
    model: str | None = typer.Option(None, "--model", "-m"),
    port: int | None = typer.Option(None, "--port", "-p", min=1024, max=65535),
    ctx_size: int | None = typer.Option(None, "--ctx-size", min=128),
    provider: SlotProvider | None = typer.Option(
        None, "--provider", case_sensitive=False, help="Change the slot's inference provider."
    ),
    hardware: SlotHardware | None = typer.Option(
        None,
        "--hardware",
        case_sensitive=False,
        help="Change the slot's hardware backend (vulkan | rocm | cpu).",
    ),
    # HAL0-SUNSET: v1.0.0 — --backend renamed to --provider in v0.2; use --provider.
    backend: str | None = typer.Option(
        None,
        "--backend",
        "-b",
        hidden=True,
        help="[DEPRECATED] alias for --provider (historic, see `slot create --help`).",
    ),
) -> None:
    """Update one or more slot config fields (PUT /api/slots/{name}/config)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    # Back-compat: --backend mapped to provider historically.
    if backend is not None:
        typer.echo(
            "[deprecated] --backend will be renamed to --provider in v0.2; use --provider",
            err=True,
        )
        try:
            provider = SlotProvider(backend) if provider is None else provider
        except ValueError:
            die(
                f"--backend {backend!r} is not a valid provider; "
                f"choose from {[p.value for p in SlotProvider]}"
            )
            return

    if (
        model is None
        and port is None
        and ctx_size is None
        and provider is None
        and hardware is None
    ):
        console.print(
            "[bold yellow]No fields provided.[/bold yellow]  "
            "Pass at least one of --model, --port, --ctx-size, --provider, --hardware."
        )
        raise typer.Exit(code=2)

    payload: dict[str, Any] = {}
    if port is not None:
        payload["port"] = port
    if provider is not None:
        payload["provider"] = str(provider)
    if hardware is not None:
        payload["device"] = _HARDWARE_TO_DEVICE.get(hardware.value, hardware.value)
    if model is not None or ctx_size is not None:
        try:
            cfg = api_get(f"/api/slots/{name}/config")
        except CliApiError as exc:
            die(str(exc))
            return
        model_block = dict(cfg.get("model") or {})
        if model is not None:
            model_block["default"] = model
        if ctx_size is not None:
            model_block["context_size"] = ctx_size
        payload["model"] = model_block

    try:
        snap = api_put(f"/api/slots/{name}/config", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Updated [bold]{name}[/bold] → {snap.get('state', '—')}")


@app.command("delete")
def slot_delete(
    name: str = typer.Argument(..., help="Slot name to delete"),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Delete a slot (DELETE /api/slots/{name}).

    ``--force`` skips the confirm prompt AND deletes a seeded slot
    (primary/embed/stt/tts + the NPU trio), which is otherwise protected. A
    seeded slot may be re-seeded by a later install/update reconcile.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    if not force:
        typer.confirm(
            f"Delete slot {name!r}? This stops the unit and removes its config.",
            abort=True,
        )
    try:
        # ``force`` also bypasses the server-side seeded-slot guard.
        api_delete(f"/api/slots/{name}", params={"force": "true"} if force else None)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Deleted slot [bold]{name}[/bold].")


# HAL0-SUNSET: v1.0.0 — alias for `slot create`; drop the alias.
@app.command("add", hidden=True)
def slot_add(
    name: str = typer.Argument(..., help="Slot name (e.g. primary, embed, stt)"),
    type_: SlotType = typer.Option(SlotType.llm, "--type", "-t", case_sensitive=False),
    provider: SlotProvider = typer.Option("llama-server", "--provider", case_sensitive=False),
    hardware: SlotHardware | None = typer.Option(None, "--hardware", case_sensitive=False),
    backend: str | None = typer.Option(None, "--backend", "-b", hidden=True),
    model: str = typer.Option(..., "--model", "-m"),
    port: int | None = typer.Option(None, "--port", "-p", min=1024, max=65535),
    ctx_size: int = typer.Option(4096, "--ctx-size", min=128),
) -> None:
    """[DEPRECATED] alias for `slot create`; use `slot create` instead."""
    typer.echo(
        "[deprecated] `slot add` is renamed to `slot create`; use `slot create`.",
        err=True,
    )
    slot_create(
        name=name,
        type_=type_,
        provider=provider,
        hardware=hardware,
        backend=backend,
        model=model,
        port=port,
        ctx_size=ctx_size,
    )


# HAL0-SUNSET: v1.0.0 — alias for `slot delete`; drop the alias.
@app.command("remove", hidden=True)
def slot_remove(
    name: str = typer.Argument(..., help="Slot name to delete"),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """[DEPRECATED] alias for `slot delete`; use `slot delete` instead."""
    typer.echo(
        "[deprecated] `slot remove` is renamed to `slot delete`; use `slot delete`.",
        err=True,
    )
    slot_delete(name=name, force=force)


@app.command("show")
def slot_show(
    name: str = typer.Argument(..., help="Slot name to inspect"),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit raw JSON ({status, config}) for CI/pipe use (no Rich panel).",
    ),
) -> None:
    """Show full slot config + status (GET /api/slots/{name})."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        status = api_get(f"/api/slots/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    try:
        cfg = api_get(f"/api/slots/{name}/config")
    except CliApiError:
        cfg = None
    body = jsonlib.dumps({"status": status, "config": cfg}, indent=2)
    if json_out:
        typer.echo(body)
        return
    console.print(
        Panel(
            Syntax(body, "json", theme="ansi_dark", background_color="default"),
            title=f"slot: {name}",
            border_style="cyan",
        )
    )


@app.command("metrics")
def slot_metrics(
    name: str | None = typer.Argument(None, help="Slot name (omit to show all slots)."),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/slots/metrics JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """Show live per-slot runtime metrics (tok/s, KV%, mem, uptime, queue depth)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/slots/metrics")
    except CliApiError as exc:
        die(str(exc))
        return
    if name is not None:
        entry = data.get(name) if isinstance(data, dict) else None
        if entry is None:
            die(f"no metrics for slot {name!r} (unknown slot, or it has never served a request)")
            return
        data = {name: entry}
    if json_out:
        typer.echo(jsonlib.dumps(data, indent=2))
        return
    table = Table(title="hal0 slot metrics")
    table.add_column("Name", style="bold")
    table.add_column("tok/s", justify="right")
    table.add_column("KV%", justify="right")
    table.add_column("Mem MB", justify="right")
    table.add_column("Uptime s", justify="right")
    table.add_column("Reqs", justify="right")
    if not data:
        console.print("[dim]No slot metrics available.[/dim]")
        return
    for slot_name, m in data.items():
        if not isinstance(m, dict):
            continue
        tps = m.get("tokens_per_sec")
        kv = m.get("kv_cache_usage")
        table.add_row(
            slot_name,
            f"{tps:.1f}" if isinstance(tps, (int, float)) else "—",
            f"{kv * 100:.0f}" if isinstance(kv, (int, float)) else "—",
            str(m.get("mem_rss_mb", "—")),
            str(m.get("uptime_seconds", "—")),
            str(m.get("requests_processing", "—")),
        )
    console.print(table)


@app.command("capacity")
def slot_capacity(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/slots/capacity JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """Show per-slot resident memory and the slot-count budget."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/slots/capacity")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(data, indent=2))
        return
    budget = data.get("slot_budget") or {}
    used = budget.get("used_slots", "—")
    max_slots = budget.get("max_slots", 0)
    cap = "unlimited" if not max_slots else str(max_slots)
    console.print(f"Slot budget: [bold]{used}[/bold] / {cap}")
    per_slot = data.get("per_slot") or {}
    table = Table(title="Per-slot memory")
    table.add_column("Name", style="bold")
    table.add_column("State")
    table.add_column("Model")
    table.add_column("VRAM MB", justify="right")
    table.add_column("RAM MB", justify="right")
    table.add_column("Total MB", justify="right")
    if not per_slot:
        console.print("[dim]No slot capacity data.[/dim]")
        return
    for slot_name, s in per_slot.items():
        if not isinstance(s, dict):
            continue
        table.add_row(
            slot_name,
            _fmt_state(s.get("state")),
            s.get("model_id") or "—",
            str(s.get("vram_mb", "—")),
            str(s.get("ram_mb", "—")),
            str(s.get("mem_mb", "—")),
        )
    console.print(table)


# ── migrate-id-keying (P3-runtime-db inc4 / §11.1 M5 downtime window) ────────
#
# Offline / filesystem-direct — the only command in this file that is NOT an
# HTTP client. It flips every slot artefact's on-disk key from the mutable
# ``name`` to the stable ``id`` (:mod:`hal0.slots.migrate_id_keying`), which
# only the still-name-keyed runtime the (stopped) API reads at its next boot
# should ever see change. Running it against a LIVE api/slot set is the exact
# halo143 split-brain lesson the bilingual read/write layer (inc0-3) was built
# to avoid on the read side — this command is the write-side guard: refuse
# (or --stop-services) rather than flip artefacts out from under a running
# process.


def _active_hal0_units(
    *, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
) -> list[str]:
    """Every hal0-owned systemd unit currently active: ``hal0-api.service``
    plus any live ``hal0-slot@*.service`` instance.

    Best-effort: no systemd on this box (dev shell, CI, unit tests) is not an
    error — it just means nothing can be "active", so an empty list is the
    correct (not merely convenient) answer.
    """
    active: list[str] = []
    try:
        result = run(
            ["systemctl", "is-active", "--quiet", "hal0-api.service"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            active.append("hal0-api.service")
    except (OSError, subprocess.SubprocessError):
        return []

    try:
        result = run(
            [
                "systemctl",
                "list-units",
                "--type=service",
                "--state=active",
                "--no-legend",
                "--plain",
                "hal0-slot@*.service",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        for line in result.stdout.splitlines():
            fields = line.split()
            if fields:
                active.append(fields[0])
    except (OSError, subprocess.SubprocessError):
        pass
    return active


def _backup_slot_state(
    *, config_dir: Path, data_dir: Path, db_file: Path, backup_root: Path
) -> Path:
    """Pre-flight tar backup of everything the migration touches.

    The migrator has no undo (:mod:`hal0.slots.migrate_id_keying` module
    docstring: "destructive, idempotent") — this tarball IS the rollback
    path. Written BEFORE the migrator runs, timestamped so re-runs never
    clobber a prior backup. Tolerates a missing data_dir / db_file (a fresh
    box that has never loaded a slot) by simply skipping what's absent —
    ``config_dir`` is the only piece required to exist.
    """
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tar_path = backup_root / f"slot-id-keying-{ts}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        if config_dir.exists():
            tf.add(config_dir, arcname="etc-hal0-slots")
        if data_dir.exists():
            tf.add(data_dir, arcname="var-lib-hal0-slots")
        if db_file.exists():
            tf.add(db_file, arcname="hal0.db")
            # SQLite WAL/SHM sidecars carry uncommitted-but-durable writes —
            # a backup missing them can lose the tail of the identity table.
            for suffix in ("-wal", "-shm"):
                sidecar = db_file.with_name(db_file.name + suffix)
                if sidecar.exists():
                    tf.add(sidecar, arcname=f"hal0.db{suffix}")
    return tar_path


def _migrate_id_keying_dry_run_plan(*, config_dir: Path, identity: Any) -> list[str]:
    """The name→id plan a ``--dry-run`` reports, without moving a single file.

    :func:`hal0.slots.migrate_id_keying.migrate_slot_id_keying` has no
    dry-run mode of its own — every call mints identity rows and renames
    files. This mirrors its per-TOML classification (already id-keyed vs.
    name-keyed) read-only: an already-id-keyed stem is reported as a skip; a
    name-keyed slot reuses its identity row's id when :meth:`fold_identity`
    (or a prior partial run) already created one, else reports "new id"
    since the real id is only known once the migrator actually inserts the
    row (SQLite AUTOINCREMENT).
    """
    lines: list[str] = []
    for toml_path in sorted(config_dir.glob("*.toml")):
        if toml_path.name.startswith("."):
            continue
        try:
            raw = tomllib.loads(toml_path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as exc:
            lines.append(f"{toml_path.name}: unreadable ({exc}) — skip")
            continue
        slot_tbl = raw.get("slot") if isinstance(raw.get("slot"), dict) else raw
        name = str(slot_tbl.get("name") or toml_path.stem)
        existing_id = slot_tbl.get("id")
        if (
            isinstance(existing_id, int)
            and not isinstance(existing_id, bool)
            and toml_path.stem == str(existing_id)
        ):
            lines.append(f"{toml_path.name}: already id-keyed (id={existing_id}) — skip")
            continue
        row = identity.get_by_name(name)
        if row is not None:
            lines.append(f"{name} ({toml_path.name}) -> {row.id}.toml (existing identity row)")
        else:
            lines.append(
                f"{name} ({toml_path.name}) -> <new id>.toml (identity row will be minted)"
            )
    return lines


@app.command("migrate-id-keying")
def slot_migrate_id_keying(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt (for scripted use)."
    ),
    stop_services: bool = typer.Option(
        False,
        "--stop-services",
        help=(
            "Stop hal0-api and every active hal0-slot@* unit first (systemctl stop), "
            "then proceed. Without this flag the command only WARNS and refuses to "
            "run while any of those units is active."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the computed name->id plan and exit — no file is moved, no id is minted.",
    ),
) -> None:
    """One-shot: flip every name-keyed slot artefact to id-keyed (P3-runtime-db §11.1 M5).

    ``/etc/hal0/slots/<name>.toml`` -> ``<id>.toml``, the matching
    ``/var/lib/hal0/slots/<name>/state.json`` -> ``<id>/state.json``, and the
    Quadlet unit + podman container rename to match. DESTRUCTIVE and OPERATOR-
    RUN ONLY — never wired into any automatic boot/update path.

    Downtime window required: stop ``hal0-api`` and every ``hal0-slot@*`` unit
    FIRST (or pass ``--stop-services``). Flipping artefact names under a live
    runtime is the halo143 split-brain scenario — the running process still
    resolves the OLD (name) paths while a second reader (a restart, a doctor
    scan) would see the NEW (id) ones.

    A timestamped tar backup of ``/etc/hal0/slots``, ``/var/lib/hal0/slots``,
    and ``/var/lib/hal0/hal0.db`` is written to ``/var/lib/hal0/backups/``
    BEFORE anything moves — that tarball is the only rollback path (the
    migrator has no undo). Idempotent: re-running rolls a half-migrated tree
    forward to the same result, so a crash mid-run is safe to retry.
    """
    from hal0.config import paths
    from hal0.slots.identity import SlotIdentityStore
    from hal0.slots.migrate_id_keying import SubprocessSlotArtifactOps, migrate_slot_id_keying

    config_dir = paths.slots_config_dir()
    data_dir = paths.var_lib() / "slots"
    db_file = paths.db_path()

    active = _active_hal0_units()
    if active:
        console.print(
            "[yellow]![/yellow]  the following hal0 units are still active: " + ", ".join(active)
        )
        if stop_services:
            console.print("[dim]Stopping active units first (--stop-services)...[/dim]")
            for unit in active:
                subprocess.run(["systemctl", "stop", unit], check=False)
            active = _active_hal0_units()
        if active:
            console.print(
                "[red]✗[/red]  refusing to migrate while hal0 is live — flipping artefact "
                "names under a running runtime split-brains it (the halo143 lesson).\n"
                "        Stop hal0-api and every hal0-slot@* unit first, or re-run with "
                "--stop-services."
            )
            raise typer.Exit(1)

    if dry_run:
        identity = SlotIdentityStore(db_path=db_file)
        plan = _migrate_id_keying_dry_run_plan(config_dir=config_dir, identity=identity)
        console.print("[bold]Dry run — no files moved, no ids minted.[/bold]")
        if not plan:
            console.print("  [dim](no slot TOMLs found)[/dim]")
        for line in plan:
            console.print(f"  {line}")
        return

    if not yes:
        typer.confirm(
            "This flips every slot artefact from name-keyed to id-keyed on disk. "
            "It is destructive (no built-in undo — a backup is taken first) and "
            "must run while hal0 is stopped. Proceed?",
            abort=True,
        )

    backup_path = _backup_slot_state(
        config_dir=config_dir,
        data_dir=data_dir,
        db_file=db_file,
        backup_root=paths.var_lib() / "backups",
    )
    console.print(f"[green]✓[/green]  backup written to {backup_path}")

    identity = SlotIdentityStore(db_path=db_file)
    ops = SubprocessSlotArtifactOps()
    report = migrate_slot_id_keying(
        identity=identity,
        config_dir=config_dir,
        data_dir=data_dir,
        ops=ops,
    )

    if not report.migrations and not report.skipped_ids:
        console.print("[dim]No slot TOMLs found — nothing to migrate.[/dim]")
        return

    console.print(f"\n[bold]Migrated {len(report.migrations)} slot(s):[/bold]")
    for m in report.migrations:
        console.print(f"  {m.name} -> {m.slot_id}.toml")
    if report.skipped_ids:
        console.print(
            f"[dim]Already id-keyed (skipped): {', '.join(str(i) for i in report.skipped_ids)}[/dim]"
        )
    console.print(
        "\n[yellow]Restart hal0-api (and daemon-reload) to pick up the id-keyed layout.[/yellow]"
    )
