"""hal0 slot subcommands — thin HTTP client to the hal0 API."""

from __future__ import annotations

import json as jsonlib
from enum import StrEnum
from typing import Any

import httpx
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
)

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
    The dispatcher routes by ``type`` (per ADR-0008 §6); without this
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
    try:
        with httpx.stream("GET", url + f"/api/slots/{name}/logs/stream", timeout=None) as r:
            for raw in r.iter_lines():
                if not raw or not raw.startswith("data:"):
                    continue
                payload = raw[5:].strip()
                try:
                    console.print(jsonlib.loads(payload))
                except ValueError:
                    console.print(payload)
    except (httpx.HTTPError, KeyboardInterrupt):
        return


@app.command("create")
def slot_create(
    name: str = typer.Argument(..., help="Slot name (e.g. primary, embed, stt)"),
    type_: SlotType = typer.Option(
        SlotType.llm,
        "--type",
        "-t",
        help=(
            "Slot type: llm | embedding | reranking | transcription | tts | image. "
            "Determines how the dispatcher routes requests (ADR-0008 §6)."
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
        help="Slot port (default: auto-assign next free port in 8081-8099).",
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
    # The `device` enum (gpu-vulkan / gpu-rocm / cpu / npu) derives
    # from the v0.1 hardware enum: vulkan/rocm → gpu-vulkan/gpu-rocm; cpu
    # stays cpu; npu has no v0.1 hardware equivalent (set --hardware via
    # the legacy schema upgrade path).
    device = {
        "vulkan": "gpu-vulkan",
        "rocm": "gpu-rocm",
        "cpu": "cpu",
    }.get(hw, hw)
    body: dict[str, Any] = {
        "name": name,
        "type": type_.value,
        "device": device,
        "backend": hw,  # SlotConfig.backend = hardware target (vulkan/rocm/cpu/...)
        "provider": str(provider),
        "model": {"default": model, "context_size": ctx_size},
    }
    if port is not None:
        body["port"] = port
    else:
        # Best-effort: pick first free port in 8081-8099 by asking the API.
        try:
            existing = api_get("/api/slots")
            used = {int(s.get("port") or 0) for s in existing}
            for p in range(8081, 8100):
                if p not in used:
                    body["port"] = p
                    break
        except CliApiError:
            body["port"] = 8081
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
        payload["backend"] = hardware.value
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
    """Delete a slot (DELETE /api/slots/{name})."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    if not force:
        typer.confirm(
            f"Delete slot {name!r}? This stops the unit and removes its config.",
            abort=True,
        )
    try:
        api_delete(f"/api/slots/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Deleted slot [bold]{name}[/bold].")


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
