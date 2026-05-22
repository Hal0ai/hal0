"""hal0 config subcommands — thin HTTP client to the hal0 API."""

from __future__ import annotations

import json as jsonlib
import os
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, die

app = typer.Typer(help="Inspect and manage hal0 configuration.")
console = Console()


def _hal0_toml_path() -> Path:
    """Return the on-disk hal0.toml path, honouring HAL0_HOME."""
    base = os.environ.get("HAL0_HOME")
    if base:
        return Path(base) / "etc" / "hal0" / "hal0.toml"
    return Path("/etc/hal0/hal0.toml")


@app.command("show")
def config_show() -> None:
    """Print the current hal0 configuration (hal0.toml on disk)."""
    path = _hal0_toml_path()
    if not path.exists():
        console.print(f"[dim]No config at {path}[/dim]")
        raise typer.Exit(0)
    # Translate PermissionError into a clear hint — pre-v0.1.3 installs
    # left /etc/hal0 mode 0700 in some umask-tightened environments,
    # which makes `hal0 config show` from a non-root shell explode with
    # a raw Python traceback. Re-run under sudo or chmod the config tree
    # 0755/0644 (the installer does this for fresh installs as of
    # v0.1.3).
    try:
        body = path.read_text()
    except PermissionError as exc:
        console.print(f"[red]Permission denied:[/red] {path}")
        console.print(
            "[dim]The config is owned by root. Re-run with [bold]sudo[/bold], "
            "or run [bold]sudo chmod 0755 /etc/hal0 && sudo chmod 0644 "
            f"{path}[/bold] once and the command will work without sudo.[/dim]"
        )
        raise typer.Exit(1) from exc
    console.print(
        Panel(
            Syntax(body, "toml", theme="ansi_dark", background_color="default"),
            title=str(path),
            border_style="cyan",
        )
    )


@app.command("edit")
def config_edit() -> None:
    """Open hal0.toml in $EDITOR (falls back to $VISUAL then 'vi')."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    path = _hal0_toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# hal0 configuration — see `hal0 config show` for the live shape.\n"
            "[meta]\nschema_version = 1\n\n"
            "[slots]\nport_range_start = 8081\nport_range_end = 8099\n"
        )
    try:
        subprocess.run([editor, str(path)], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        die(f"editor {editor!r} failed: {exc}")


@app.command("migrate")
def config_migrate() -> None:
    """Apply pending schema migrations to /etc/hal0/ (Tier 3, no-op today)."""
    console.print(
        "Config schema is at v1 — no migrations pending. "
        "Future versions will run idempotent transforms here."
    )


@app.command("validate")
def config_validate() -> None:
    """Validate all config files against the current schema."""
    from hal0.config.loader import (
        load_hal0_config,
        load_providers_config,
        load_upstreams_config,
    )

    problems: list[str] = []
    try:
        load_hal0_config()
    except Exception as exc:
        problems.append(f"hal0.toml: {exc}")
    try:
        load_upstreams_config()
    except Exception as exc:
        problems.append(f"upstreams.toml: {exc}")
    try:
        load_providers_config()
    except Exception as exc:
        problems.append(f"providers.toml: {exc}")
    if problems:
        for p in problems:
            console.print(f"[red]✗[/red] {p}")
        raise typer.Exit(1)
    console.print("[green]✓[/green] All configs pass schema validation.")


@app.command("reload")
def config_reload() -> None:
    """Ask the running hal0 daemon to reload configs (re-reads TOMLs)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        from hal0.cli._shared import api_post

        api_post("/api/settings/reload")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print("[green]✓[/green] Reloaded.")


@app.command("hardware")
def config_hardware() -> None:
    """Show the cached hardware probe payload."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        hw = api_get("/api/hardware")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        Panel(
            Syntax(
                jsonlib.dumps(hw, indent=2),
                "json",
                theme="ansi_dark",
                background_color="default",
            ),
            title="hardware",
            border_style="cyan",
        )
    )
