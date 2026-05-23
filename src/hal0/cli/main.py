"""hal0 CLI entry point.

Entry point declared in pyproject.toml:
    [project.scripts]
    hal0 = "hal0.cli.main:app"
"""

from __future__ import annotations

import json as jsonlib
import os
import sys
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

import hal0
from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    api_post,
    die,
)
from hal0.cli.agent_commands import app as agent_app
from hal0.cli.capabilities_commands import app as capabilities_app
from hal0.cli.config_commands import app as config_app
from hal0.cli.doctor_commands import app as doctor_app
from hal0.cli.memory_commands import app as memory_app
from hal0.cli.migrate_commands import app as migrate_app
from hal0.cli.model_commands import app as model_app
from hal0.cli.registry_commands import app as registry_app
from hal0.cli.slot_commands import app as slot_app
from hal0.cli.update_commands import update as _update_impl

console = Console()

# ---------------------------------------------------------------------------
# Root app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="hal0",
    help="hal0 — open-source home AI inference platform.",
    no_args_is_help=True,
    add_completion=True,
)

# Mount sub-apps
app.add_typer(slot_app, name="slot")
app.add_typer(model_app, name="model")
# Issue #258 — ``hal0 memory graph {status,enable,disable}`` ADR-0014 surface.
# Mounted between ``model`` and ``config`` so it sits alongside the other
# user-facing data subcommands rather than buried under operator surfaces.
app.add_typer(memory_app, name="memory")
app.add_typer(config_app, name="config")
app.add_typer(doctor_app, name="doctor")
app.add_typer(capabilities_app, name="capabilities")
app.add_typer(agent_app, name="agent")
app.add_typer(migrate_app, name="migrate")
app.add_typer(registry_app, name="registry")


# ---------------------------------------------------------------------------
# --version callback
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"hal0 {hal0.__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        help="Print version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """hal0 — open-source home AI inference platform."""


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Show system and slot summary."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        st = api_get("/api/status")
        slots = api_get("/api/slots")
        ups = api_get("/api/upstreams")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        Panel(
            f"[bold]{st.get('name', 'hal0')}[/bold] v{st.get('version', '?')}  "
            f"· slots={len(slots)} · upstreams={len(ups)}",
            border_style="cyan",
        )
    )
    table = Table(title="Slots")
    table.add_column("Name", style="bold")
    table.add_column("State")
    table.add_column("Model")
    table.add_column("Port", justify="right")
    for s in slots:
        table.add_row(
            s.get("name", "—"),
            s.get("status", "—"),
            s.get("model") or s.get("model_id") or "—",
            str(s.get("port") or "—"),
        )
    console.print(table)


@app.command()
def probe() -> None:
    """Re-run hardware detection and update hardware.json."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        hw = api_post("/api/hardware/probe")
    except CliApiError as exc:
        die(str(exc))
        return
    summary = {
        "cpu": hw.get("cpu_name"),
        "ram_mb": hw.get("ram_mb"),
        "unified_memory_mb": hw.get("unified_memory_mb"),
        "gpu": hw.get("gpu_name"),
        "gtt_total_mb": hw.get("gtt_total_mb"),
        "vram_total_mb": hw.get("vram_total_mb"),
        "npu": hw.get("npu_name"),
    }
    console.print(
        Panel(
            Syntax(
                jsonlib.dumps(summary, indent=2),
                "json",
                theme="ansi_dark",
                background_color="default",
            ),
            title="hardware probe",
            border_style="cyan",
        )
    )


# ---------------------------------------------------------------------------
# hal0 update — real implementation lives in hal0.cli.update_commands.
# Registered via app.command() so the function's typer.Options surface.
# ---------------------------------------------------------------------------

app.command(name="update")(_update_impl)


# ---------------------------------------------------------------------------
# hal0 serve  (Phase 0 — the only command that actually does something)
# ---------------------------------------------------------------------------


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host for the hal0 API."),
    port: int = typer.Option(8080, "--port", help="Bind port for the hal0 API."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev mode)."),
) -> None:
    """Start the hal0 API server (used by hal0-api.service)."""
    console.print(f"Starting hal0 API on [bold]{host}:{port}[/bold]")
    uvicorn.run("hal0.api:app", host=host, port=port, reload=reload)


# ---------------------------------------------------------------------------
# hal0 uninstall
# ---------------------------------------------------------------------------


@app.command()
def uninstall(
    keep_data: bool = typer.Option(
        False,
        "--keep-data",
        help="Preserve /var/lib/hal0/ (model cache, openwebui state, slot data).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip the DELETE confirmation prompt (also honours HAL0_FORCE=1).",
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Tear down a dev-mode install rooted at $PWD/.hal0ai (or $HAL0_PREFIX).",
    ),
) -> None:
    """Uninstall hal0 from this system.

    Thin wrapper around ``installer/uninstall.sh`` — the shell script is the
    source of truth and mirrors install.sh's path layout. We exec it so the
    script inherits the live TTY for its DELETE confirmation prompt.
    """
    import shutil

    # Editable install: src/hal0/__init__.py -> repo root is parents[2].
    repo_root = Path(hal0.__file__).resolve().parents[2]
    script = repo_root / "installer" / "uninstall.sh"
    if not script.is_file():
        die(
            f"uninstall.sh not found at {script}. "
            "This hal0 install looks packaged differently — run the script directly."
        )

    if not shutil.which("bash"):
        die("bash is required to run the uninstaller.")

    # The script's DELETE prompt needs an interactive stdin. Refuse early in
    # non-interactive contexts unless the caller has opted out of the prompt.
    force_env = os.environ.get("HAL0_FORCE") == "1"
    if not (keep_data or force or force_env) and not sys.stdin.isatty():
        die(
            "Refusing to uninstall non-interactively without --force or "
            "--keep-data — the shell script's DELETE prompt would hang."
        )

    argv = ["bash", str(script)]
    if keep_data:
        argv.append("--keep-data")
    if force:
        argv.append("--force")
    if dev:
        argv.append("--dev")

    os.execvp("bash", argv)
