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
    profile: str | None = typer.Option(
        None,
        "--profile",
        help=(
            "Runtime profile (flag bundle + engine). Default: inferred from "
            "--type/--hardware — embedding → embedding (--embedding), "
            "reranking → reranking (--reranking), tts/transcription → the "
            "engine hal0 ships for that device (none is inferred where it "
            "ships none). llm and image slots infer none."
        ),
    ),
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
    # Absent = let the create chokepoint infer the capability profile from
    # type/device (#1830); an explicit name always wins. Never send an empty
    # placeholder — that reads as a deliberate operator choice and suppresses
    # the inference.
    if profile:
        body["profile"] = profile
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
    profile: str | None = typer.Option(
        None,
        "--profile",
        help=(
            "Change the slot's runtime profile (flag bundle + engine) — e.g. "
            "'embedding' / 'reranking' to repair a profile-less capability slot "
            "created before the profile was inferred (#1830)."
        ),
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
        and profile is None
    ):
        console.print(
            "[bold yellow]No fields provided.[/bold yellow]  "
            "Pass at least one of --model, --port, --ctx-size, --provider, "
            "--hardware, --profile."
        )
        raise typer.Exit(code=2)

    payload: dict[str, Any] = {}
    if port is not None:
        payload["port"] = port
    if provider is not None:
        payload["provider"] = str(provider)
    if profile is not None:
        payload["profile"] = profile
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
        # Explicit: a direct Python call gets typer's OptionInfo sentinel for
        # any omitted parameter, which would then be POSTed as the profile.
        profile=None,
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
        mem = m.get("mem_rss_mb")
        table.add_row(
            slot_name,
            f"{tps:.1f}" if isinstance(tps, (int, float)) else "—",
            f"{kv * 100:.0f}" if isinstance(kv, (int, float)) else "—",
            f"{mem:.1f}" if isinstance(mem, (int, float)) else "—",
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
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Actually flip the artefacts. Without it the command is a DRY-RUN preview only.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt (for scripted use)."
    ),
    stop_services: bool = typer.Option(
        False,
        "--stop-services",
        help=(
            "Stop hal0-api and every active hal0-slot@* unit first (systemctl stop), "
            "then proceed. Without this flag --apply only WARNS and refuses to run "
            "while any of those units is active."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "Deprecated no-op: dry-run is now the bare default (see --apply). "
            "Kept as a hidden alias so existing scripts keep working."
        ),
        hidden=True,
    ),
) -> None:
    """One-shot: flip every name-keyed slot artefact to id-keyed (P3-runtime-db §11.1 M5).

    ``/etc/hal0/slots/<name>.toml`` -> ``<id>.toml``, the matching
    ``/var/lib/hal0/slots/<name>/state.json`` -> ``<id>/state.json``, and the
    Quadlet unit + podman container rename to match. DESTRUCTIVE and OPERATOR-
    RUN ONLY — never wired into any automatic boot/update path.

    DRY-RUN by default — prints the computed name->id plan and exits. Pass
    ``--apply`` to write (GH #1474: matches the sibling migrate-hw/
    migrate-caps/migrate-flags/migrate-enabled-removal contract instead of
    inverting it — a bare invocation never surprises with a real migration).

    Downtime window required for ``--apply``: stop ``hal0-api`` and every
    ``hal0-slot@*`` unit FIRST (or pass ``--stop-services``). Flipping
    artefact names under a live runtime is the halo143 split-brain scenario —
    the running process still resolves the OLD (name) paths while a second
    reader (a restart, a doctor scan) would see the NEW (id) ones.

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

    if not apply:
        identity = SlotIdentityStore(db_path=db_file)
        plan = _migrate_id_keying_dry_run_plan(config_dir=config_dir, identity=identity)
        console.print("[bold]Dry run — no files moved, no ids minted.[/bold]")
        if not plan:
            console.print("  [dim](no slot TOMLs found)[/dim]")
        for line in plan:
            console.print(f"  {line}")
        console.print("\n[dim]Re-run with --apply to write (stop hal0 first).[/dim]")
        return

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


# ── migrate-hw (spec-hw-slot-ownership §6 — hardware sticks to slots) ─────────
#
# Deploy-window manual trigger for the standalone one-shot fold in
# hal0.config.migrations.hw_slot_ownership (mirrors how slot_flags_fold would be
# run). NOT wired into any automatic boot/update path — the fold is an
# irreversible physical re-partition and must be operator-run inside a window.


@app.command("migrate-hw")
def slot_migrate_hw(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Actually write the fold. Without it the command is a DRY-RUN preview only.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt (for scripted use)."
    ),
    stop_services: bool = typer.Option(
        False,
        "--stop-services",
        help=(
            "Stop hal0-api and every active hal0-slot@* unit first (systemctl stop), "
            "then proceed. Without this flag --apply only WARNS and refuses to run "
            "while any of those units is active."
        ),
    ),
) -> None:
    """One-shot: unwind the flags-fold — hardware sticks to SLOTS (spec-hw-slot-ownership §6).

    Folds each slot's/model's physical facts onto the slot's typed hardware grid:
    model ``n_gpu_layers`` + a slot's nested ``[model].n_gpu_layers`` -> slot
    ``n_gpu_layers`` (NGL); model ``preferred_runner`` -> slot ``binary``;
    ``profile.image`` / ``slot.image`` deliberate pins -> slot ``image_pin``
    (former-default debris dropped). Then NULLs the folded-out model columns.
    Idempotent + re-runnable.

    DRY-RUN by default — prints the computed plan and exits. Pass ``--apply`` to
    write. DESTRUCTIVE + OPERATOR-RUN ONLY (deploy window): a timestamped backup
    of the slot config/state + registry DB is written BEFORE anything changes;
    it is never wired into any automatic boot/update path.
    """
    from hal0.config import paths
    from hal0.config.migrations.hw_slot_ownership import run_migration

    if not apply:
        lines = run_migration(deploy_window=False, dry_run=True)
        console.print("[bold]Dry run — no files written, no columns nulled.[/bold]")
        if not lines:
            console.print("  [dim](nothing to fold)[/dim]")
        for line in lines:
            console.print(f"  {line}")
        console.print("\n[dim]Re-run with --apply to write (stop hal0 first).[/dim]")
        return

    # --apply: a real deploy-window write. Guard against a live runtime — the
    # fold rewrites slot TOMLs the running process still resolves.
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
                "[red]✗[/red]  refusing to fold while hal0 is live — rewriting slot TOMLs "
                "under a running runtime split-brains it.\n"
                "        Stop hal0-api and every hal0-slot@* unit first, or re-run with "
                "--stop-services."
            )
            raise typer.Exit(1)

    if not yes:
        typer.confirm(
            "This rewrites slot TOMLs + profiles.toml and NULLs the model HW columns "
            "(a backup is taken first). Proceed?",
            abort=True,
        )

    backup_path = _backup_slot_state(
        config_dir=paths.slots_config_dir(),
        data_dir=paths.var_lib() / "slots",
        db_file=paths.db_path(),
        backup_root=paths.var_lib() / "backups",
    )
    console.print(f"[green]✓[/green]  backup written to {backup_path}")

    lines = run_migration(deploy_window=True, dry_run=False)
    console.print("\n[bold]Applied hardware-ownership fold:[/bold]")
    if not lines:
        console.print("  [dim](nothing to fold)[/dim]")
    for line in lines:
        console.print(f"  {line}")
    console.print(
        "\n[yellow]Restart hal0-api (and daemon-reload) to pick up the slot HW grid.[/yellow]"
    )


# ── migrate-caps (spec-hw-slot-ownership §1 — mtp/reasoning/vision stick to models) ──
#
# Deploy-window manual trigger for the standalone one-shot fold in
# hal0.config.migrations.model_owned_caps (mirrors migrate-hw's shape, reversed
# direction: slot debris -> model defaults). NOT wired into any automatic
# boot/update path.


@app.command("migrate-caps")
def slot_migrate_caps(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Actually write the fold. Without it the command is a DRY-RUN preview only.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt (for scripted use)."
    ),
    stop_services: bool = typer.Option(
        False,
        "--stop-services",
        help=(
            "Stop hal0-api and every active hal0-slot@* unit first (systemctl stop), "
            "then proceed. Without this flag --apply only WARNS and refuses to run "
            "while any of those units is active."
        ),
    ),
) -> None:
    """One-shot: mtp/enable_thinking/vision stick to MODELS (spec-hw-slot-ownership §1).

    Folds each slot's ``mtp`` / ``enable_thinking`` / ``vision`` tri-state override
    into its bound model's ``defaults`` (only when the model has no opinion yet —
    an existing curator-set default always wins); two slots bound to the same
    model that disagree report the conflict (first slot, stable file order, wins)
    rather than silently dropping it. Either way, the slot's own keys are then
    dropped (SlotConfig no longer declares them; the API rejects new writes of
    them). Idempotent + re-runnable.

    DRY-RUN by default — prints the computed plan and exits. Pass ``--apply`` to
    write. DESTRUCTIVE + OPERATOR-RUN ONLY (deploy window): a timestamped backup
    of the slot config/state + registry DB is written BEFORE anything changes;
    it is never wired into any automatic boot/update path.
    """
    from hal0.config import paths
    from hal0.config.migrations.model_owned_caps import run_migration

    if not apply:
        lines = run_migration(deploy_window=False, dry_run=True)
        console.print("[bold]Dry run — no files written, no model rows changed.[/bold]")
        if not lines:
            console.print("  [dim](nothing to fold)[/dim]")
        for line in lines:
            console.print(f"  {line}")
        console.print("\n[dim]Re-run with --apply to write (stop hal0 first).[/dim]")
        return

    # --apply: a real deploy-window write. Guard against a live runtime — the
    # fold rewrites slot TOMLs the running process still resolves.
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
                "[red]✗[/red]  refusing to fold while hal0 is live — rewriting slot TOMLs "
                "under a running runtime split-brains it.\n"
                "        Stop hal0-api and every hal0-slot@* unit first, or re-run with "
                "--stop-services."
            )
            raise typer.Exit(1)

    if not yes:
        typer.confirm(
            "This rewrites slot TOMLs and model defaults rows (a backup is taken first). Proceed?",
            abort=True,
        )

    backup_path = _backup_slot_state(
        config_dir=paths.slots_config_dir(),
        data_dir=paths.var_lib() / "slots",
        db_file=paths.db_path(),
        backup_root=paths.var_lib() / "backups",
    )
    console.print(f"[green]✓[/green]  backup written to {backup_path}")

    lines = run_migration(deploy_window=True, dry_run=False)
    console.print("\n[bold]Applied model-ownership fold:[/bold]")
    if not lines:
        console.print("  [dim](nothing to fold)[/dim]")
    for line in lines:
        console.print(f"  {line}")
    console.print(
        "\n[yellow]Restart hal0-api to pick up the model-owned mtp/reasoning/vision defaults.[/yellow]"
    )


# ── migrate-flags (spec-flags-ownership §5 — launch flags stick to models) ────
#
# Deploy-window manual trigger for the standalone one-shot fold in
# hal0.config.migrations.slot_flags_fold. NOT wired into any automatic
# boot/update path.
#
# #1396: the fold module has existed since the flags-ownership lane, but nothing
# ever exposed it — no CLI, no installer hook, only tests. Meanwhile the launch
# readers were already deleted (providers.container drops profile_flags /
# slot_parallel / extra_args; resolve_chat_template no longer consults the
# slot), so an upgraded box with a tuned slot silently launched WITHOUT that
# tune and had no supported way to recover it. This is that entry point.


@app.command("migrate-flags")
def slot_migrate_flags(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Actually write the fold. Without it the command is a DRY-RUN preview only.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt (for scripted use)."
    ),
    stop_services: bool = typer.Option(
        False,
        "--stop-services",
        help=(
            "Stop hal0-api and every active hal0-slot@* unit first (systemctl stop), "
            "then proceed. Without this flag --apply only WARNS and refuses to run "
            "while any of those units is active."
        ),
    ),
) -> None:
    """One-shot: fold each slot's flag/tune surface onto its bound MODEL.

    Materializes the effective per-slot tune — ``[server].extra_args``, the
    ``parallel`` sequence-slot count, and the per-slot ``chat_template``
    override, layered over the slot's profile flags — into the bound model's
    ``defaults``, which is the only tier the launch path still reads
    (spec-flags-ownership §1-§5). Idempotent + re-runnable.

    Two or more slots that fold DIVERGENT tunes onto one model are refused as a
    group: the command reports every conflict and writes nothing, so the
    operator resolves them (pick one tune, or split the model row) and re-runs.

    DRY-RUN by default — prints the computed plan and exits. Pass ``--apply`` to
    write. DESTRUCTIVE + OPERATOR-RUN ONLY (deploy window): a timestamped backup
    of the slot config/state + registry DB is written BEFORE anything changes;
    it is never wired into any automatic boot/update path.
    """
    from hal0.config import paths
    from hal0.config.migrations.slot_flags_fold import run_migration

    def _report_refusal(exc: Exception) -> None:
        # apply_fold_plan raises on divergent shares for BOTH dry-run and
        # apply, so both paths funnel here — an operator previewing a
        # conflicted box must get the conflict list, not a traceback.
        console.print("[red]✗[/red]  refusing to fold — divergent slot tunes share a model:")
        for part in str(exc).split(" | "):
            part = part.strip()
            if part:
                console.print(f"  {part}")
        console.print(
            "\n[dim]Resolve each conflict (pick one tune, or split the model row) and re-run.[/dim]"
        )

    if not apply:
        try:
            lines = run_migration(deploy_window=False, dry_run=True)
        except RuntimeError as exc:
            _report_refusal(exc)
            raise typer.Exit(1) from None
        console.print("[bold]Dry run — no files written, no registry rows updated.[/bold]")
        if not lines:
            console.print("  [dim](nothing to fold)[/dim]")
        for line in lines:
            console.print(f"  {line}")
        console.print("\n[dim]Re-run with --apply to write (stop hal0 first).[/dim]")
        return

    # --apply: a real deploy-window write. Guard against a live runtime — the
    # fold rewrites registry rows the running process still resolves.
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
                "[red]✗[/red]  refusing to fold while hal0 is live — rewriting model "
                "defaults under a running runtime split-brains it.\n"
                "        Stop hal0-api and every hal0-slot@* unit first, or re-run with "
                "--stop-services."
            )
            raise typer.Exit(1)

    # Surface a divergent-share refusal BEFORE the confirm prompt and the
    # backup — there is nothing to confirm if the run cannot proceed.
    try:
        run_migration(deploy_window=False, dry_run=True)
    except RuntimeError as exc:
        _report_refusal(exc)
        raise typer.Exit(1) from None

    if not yes:
        typer.confirm(
            "This rewrites the bound models' defaults from each slot's effective tune "
            "(a backup is taken first). Proceed?",
            abort=True,
        )

    backup_path = _backup_slot_state(
        config_dir=paths.slots_config_dir(),
        data_dir=paths.var_lib() / "slots",
        db_file=paths.db_path(),
        backup_root=paths.var_lib() / "backups",
    )
    console.print(f"[green]✓[/green]  backup written to {backup_path}")

    lines = run_migration(deploy_window=True, dry_run=False)
    console.print("\n[bold]Applied flags-ownership fold:[/bold]")
    if not lines:
        console.print("  [dim](nothing to fold)[/dim]")
    for line in lines:
        console.print(f"  {line}")
    console.print("\n[yellow]Restart hal0-api to pick up the model-owned launch tune.[/yellow]")


# ── migrate-enabled-removal (#1369 — model-presence is the activation signal) ──
#
# NOT a deploy-window operation, unlike the migrate-* commands above: the same
# sweep runs on every API boot (api._boot_slot_reconcile), it is idempotent, and
# it only rewrites slots that still carry the removed key. This command is here
# so an operator can preview or force it without an API restart — hence no
# backup / unit-stop ceremony.


@app.command("migrate-enabled-removal")
def slot_migrate_enabled_removal(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Actually write the sweep. Without it the command is a DRY-RUN preview only.",
    ),
) -> None:
    """One-shot: drop the removed ``enabled`` key from slot TOMLs (#1369).

    A slot is activated by binding a model, so ``enabled`` is gone from the
    schema. Leftover keys round-trip harmlessly EXCEPT on a slot that was
    ``enabled = false`` while still holding a ``[model].default`` — under the
    new rules that bound model reads as "on", so this clears the model to
    preserve the operator's intent. NPU trio shadows keep their placeholder
    model (their gate is the anchor's ``[npu]`` table) and only lose the key.

    Safe to run live and safe to re-run: the boot path already does exactly
    this, so a second pass finds no keys and rewrites nothing.
    """
    from hal0.config import paths
    from hal0.config.migrations.slot_enabled_removal import (
        migrate_slot_dir,
        migrate_slot_toml,
    )

    slots_dir = paths.slots_config_dir()
    if not apply:
        pending: list[str] = []
        for path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
            try:
                raw = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                console.print(f"  [yellow]![/yellow] {path.name}: unreadable ({exc})")
                continue
            after = migrate_slot_toml(raw)
            if after is None:
                continue
            cleared = raw.get("model", {}).get("default") and not after["model"]["default"]
            pending.append(
                f"{path.stem}: drop 'enabled'" + (" + clear [model].default" if cleared else "")
            )
        console.print("[bold]Dry run — no files written.[/bold]")
        if not pending:
            console.print("  [dim](nothing to sweep)[/dim]")
        for line in pending:
            console.print(f"  {line}")
        console.print("\n[dim]Re-run with --apply to write.[/dim]")
        return

    migrated = migrate_slot_dir(slots_dir)
    if not migrated:
        console.print("[green]✓[/green]  nothing to sweep — no slot carries 'enabled'.")
        return
    console.print(f"[green]✓[/green]  swept {len(migrated)} slot(s): {', '.join(migrated)}")
