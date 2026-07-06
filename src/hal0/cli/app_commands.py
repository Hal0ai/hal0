"""``hal0 app`` subcommands — deferred install verbs for optional apps.

Companion to ``hal0 agent install <name>``: apps that were skipped at
install time (``HAL0_SKIP_OPENWEBUI=1``) get a first-class "install later"
verb instead of a manual ``systemctl enable`` incantation. See issue #1102
(decision Q9): now-vs-later must be behaviourally identical, so this module
calls the exact same wiring function (:func:`hal0.install.extensions.
install_openwebui`) that ``apply_setup`` uses at install time.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

from hal0.cli._shared import die

app = typer.Typer(help="Manage optional apps (OpenWebUI, ...).")
console = Console()

# Apps with a wired "install later" verb. ComfyUI is intentionally absent —
# it's owned by the seeded img slot (hal0-slot@img.service), not a
# standalone app install path.
_SUPPORTED = frozenset({"openwebui"})


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


__all__ = ["app"]
