"""``hal0 app`` subcommands — deferred install/uninstall verbs for optional apps.

Companion to ``hal0 agent install <name>``: apps that were skipped at
install time (``HAL0_SKIP_OPENWEBUI=1``) get a first-class "install later"
verb instead of a manual ``systemctl enable`` incantation. See issue #1102
(decision Q9): now-vs-later must be behaviourally identical, so this module
calls the exact same wiring function (:func:`hal0.install.extensions.
install_openwebui`) that ``apply_setup`` uses at install time.

CLI consolidation (2026-07): added ``list``/``uninstall`` alongside
``install`` — every sibling cluster (``agent``, ``mcp``, ``upstream``) has
list/status/remove for its resources; this one only had ``install``.
"""

from __future__ import annotations

import shutil
import subprocess

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import die

app = typer.Typer(help="Manage optional apps (OpenWebUI, ...).")
console = Console()

# Apps with a wired "install later" verb. ComfyUI is intentionally absent —
# it's owned by the seeded img slot (hal0-slot@img.service), not a
# standalone app install path.
_SUPPORTED = frozenset({"openwebui"})

# App id -> the systemd unit `install`/`list`/`uninstall` operate on.
_APP_UNITS: dict[str, str] = {"openwebui": "hal0-openwebui.service"}


@app.command("install")
def app_install(
    name: str = typer.Argument(..., help="App id (currently: openwebui)."),
) -> None:
    """Install + enable an app that was skipped at install time.

    Runs the identical enable+runtime-guard logic as the install-time path
    (installer/install.sh's inline OpenWebUI block / ``apply_setup``), so
    skipping now and installing later leave the box in the same state.
    """
    if name not in _SUPPORTED:
        die(f"'hal0 app install {name}' isn't supported yet — known apps: {sorted(_SUPPORTED)}")
        return

    from hal0.install.extensions import install_openwebui

    outcome = install_openwebui()

    if outcome.skipped:
        console.print(
            Panel(
                f"[yellow]OpenWebUI not started[/yellow] — {outcome.skipped.replace('_', ' ')}.\n"
                "Install/start podman, then re-run [bold]hal0 app install openwebui[/bold].",
                border_style="yellow",
            )
        )
        raise typer.Exit(1)

    if outcome.error and not outcome.installed:
        die(f"openwebui install failed: {outcome.error}")
        return

    if outcome.error:
        # installed=True but the active-confirmation lagged (slow first
        # boot pulling the image / initialising sqlite) — not fatal.
        console.print(
            Panel(
                "[bold green]Enabled[/bold green] openwebui  "
                "[dim](not active yet — check 'journalctl -u hal0-openwebui -n 40')[/dim]",
                border_style="yellow",
            )
        )
        return

    console.print(
        Panel(
            "[bold green]Installed[/bold green] openwebui  [dim](chat at :3001)[/dim]",
            border_style="green",
        )
    )


def _systemctl_query(unit: str, prop: str) -> str:
    """Best-effort ``systemctl is-<prop> <unit>`` — returns the raw stdout
    (e.g. 'active'/'inactive'/'failed', 'enabled'/'disabled') or '?' when
    systemctl is unavailable."""
    if shutil.which("systemctl") is None:
        return "?"
    try:
        result = subprocess.run(
            ["systemctl", f"is-{prop}", unit],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "?"
    return (result.stdout or "").strip() or "?"


@app.command("list")
def app_list() -> None:
    """List known apps and their systemd enabled/active state."""
    table = Table(title="Apps")
    table.add_column("Name", style="bold")
    table.add_column("Unit")
    table.add_column("Enabled")
    table.add_column("Active")
    for name in sorted(_SUPPORTED):
        unit = _APP_UNITS.get(name, "—")
        enabled = _systemctl_query(unit, "enabled") if unit != "—" else "—"
        active = _systemctl_query(unit, "active") if unit != "—" else "—"
        table.add_row(name, unit, enabled, active)
    console.print(table)


@app.command("uninstall")
def app_uninstall(
    name: str = typer.Argument(..., help="App id (currently: openwebui)."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip the confirmation prompt."),
) -> None:
    """Stop + disable an app installed via `hal0 app install`.

    Only tears down the systemd unit — it does not remove the container
    image or any data volumes, mirroring the conservative default of
    `hal0 uninstall` (no `--purge` equivalent here yet).
    """
    if name not in _SUPPORTED:
        die(f"'hal0 app uninstall {name}' isn't supported yet — known apps: {sorted(_SUPPORTED)}")
        return
    unit = _APP_UNITS.get(name)
    if unit is None:
        die(f"no service unit known for app {name!r}")
        return
    if not force:
        typer.confirm(
            f"Uninstall app {name!r}? This stops and disables {unit}.",
            abort=True,
        )
    if shutil.which("systemctl") is None:
        die("systemctl not available on this host — cannot uninstall.")
        return
    ok = subprocess.run(["systemctl", "disable", "--now", unit], check=False).returncode == 0
    if not ok:
        die(f"systemctl disable --now {unit} failed")
        return
    console.print(f"[green]Uninstalled[/green] {name}  [dim]({unit} stopped + disabled)[/dim]")


__all__ = ["app"]
