"""``hal0 memory decision`` — the opt-in local decision model server (#3757).

    hal0 memory decision install   → build + start hal0-hs-decide; the converge sets provider = "local"
    hal0 memory decision status    → component row + /api/memory/models decision block
    hal0 memory decision enable    → [memory.decision].provider = "local" (re-renders the engine)
    hal0 memory decision disable   → [memory.decision].provider = "off"
    hal0 memory decision remove    → provider = "off", engine re-pointed, then unit + venv deleted

``install`` and ``remove`` run the ``memory-decision`` component's converge
arm (:mod:`hal0.memory.decision_component`), which also writes the provider,
so a CLI that stops waiting never leaves the lane half-on. Through hal0-api by
default, the ``hal0 update component memory-decision`` path. Run as root they
run in this process instead, which needs no hal0-systemctl verb and so also
works on a box whose seam wrapper predates the component. ``enable`` and
``disable`` are ``PUT /api/settings``, which re-renders the engine's showcase
drop-in; they work on a hand-installed (unmanaged) server too.
"""

from __future__ import annotations

import json as jsonlib
import os
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, api_put, die

app = typer.Typer(help="The local decision model server (opt-in, CPU, ~2.7 GB RAM).")
console = Console()

COMPONENT_ID = "memory-decision"

#: ``PUT /api/settings`` on a decision key rewrites the engine drop-in and
#: restarts hindsight-api (each seam call bounded at 60 s server-side).
_SETTINGS_PUT_TIMEOUT_S = 160.0


def _require_api() -> None:
    if _api_unreachable(_api_base()):
        raise typer.Exit(1)


def _set_provider(provider: str) -> dict[str, Any]:
    decision: dict[str, Any] = {"provider": provider}
    if provider == "local":
        decision["enabled"] = True
    try:
        return api_put(
            "/api/settings",
            json={"memory": {"decision": decision}},
            timeout=_SETTINGS_PUT_TIMEOUT_S,
        )
    except CliApiError as exc:
        die(str(exc))
        return {}


def _models_decision() -> dict[str, Any]:
    try:
        body = api_get("/api/memory/models", timeout=20.0)
    except CliApiError as exc:
        die(str(exc))
        return {}
    return (body or {}).get("decision") or {}


def _component_row() -> dict[str, Any]:
    try:
        body = api_get("/api/updates/components")
    except CliApiError as exc:
        die(str(exc))
        return {}
    for row in (body or {}).get("components") or []:
        if row.get("id") == COMPONENT_ID:
            return row
    die(f"this hal0 has no {COMPONENT_ID!r} component")
    return {}


def _converge(body: dict[str, Any]) -> dict[str, Any]:
    """Run the arm: in-process as root, else through hal0-api's converge job."""
    if os.geteuid() == 0:
        from hal0.components.registry import component_by_id
        from hal0.components.runner import converge_component

        comp = component_by_id(COMPONENT_ID)
        if comp is None:
            die(f"this hal0 has no {COMPONENT_ID!r} component")
            return {}
        kwargs: dict[str, Any] = {"apply": True, "install": True}
        if body.get("action") == "remove":
            kwargs.update(action="remove", purge_model=bool(body.get("purge_model")))
        result = converge_component(comp, **kwargs)
    else:
        from hal0.cli.update_commands import _converge_component

        result = _converge_component(COMPONENT_ID, body)
    if result.get("status") in ("build_failed", "rolled_back", "busy"):
        die(
            f"{COMPONENT_ID}: {result.get('error') or result.get('status')}"
            + (f" — {result['remedy']}" if result.get("remedy") else "")
        )
    return result


@app.command("install")
def install_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Build the decision model server (CPU torch + a 1.5 GB model), start it, turn the lane on.

    Idempotent: on an installed box it re-converges onto this release's pins.
    """
    _require_api()
    if not yes and os.isatty(0):
        typer.confirm(
            "Download CPU torch (~640 MB venv) and hs-decide-1-flash (~1.5 GB), and run "
            "hal0-hs-decide (~2.7 GB RAM while it serves)?",
            default=True,
            abort=True,
        )
    result = _converge({"action": "install"})
    if result.get("note"):
        console.print(f"[yellow]![/yellow] {escape(str(result['note']))}")
    console.print(
        Panel(
            f"[bold green]Decision model {result.get('status', 'installed')}[/bold green]\n"
            '[dim][memory.decision].provider = "local"; the memory engine sends its '
            "decision calls to hal0-hs-decide.[/dim]",
            border_style="green",
        )
    )


@app.command("status")
def status_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """Show whether the component is installed, running, and used by the engine."""
    _require_api()
    row = _component_row()
    decision = _models_decision()
    if json_out:
        typer.echo(jsonlib.dumps({"component": row, "decision": decision}, indent=2))
        return
    table = Table(title="memory · decision model", show_header=False)
    table.add_column("key", style="dim")
    table.add_column("value")
    table.add_row("provider", str(decision.get("provider")))
    table.add_row("installed", str(decision.get("install_state") or decision.get("installed")))
    active = decision.get("unit_active")
    table.add_row("service", {True: "active", False: "inactive"}.get(active, "unknown"))
    table.add_row("answering", "yes" if decision.get("reachable") else "no")
    table.add_row("component", f"{row.get('status')} (pinned {row.get('pinned')})")
    if row.get("error"):
        table.add_row("last error", escape(str(row["error"])))
    console.print(table)
    if not decision.get("installed"):
        console.print("[dim]install it with: hal0 memory decision install[/dim]")
    elif decision.get("install_state") == "unmanaged":
        console.print(
            "[dim]hand-installed (unmanaged): `hal0 memory decision install` adopts it[/dim]"
        )
    elif decision.get("provider") != "local":
        console.print("[dim]installed but off — turn it on with: hal0 memory decision enable[/dim]")


@app.command("enable")
def enable_cmd() -> None:
    """Point the memory engine at the installed server (provider = "local")."""
    _require_api()
    if not _models_decision().get("installed"):
        die("the decision model is not installed — run: hal0 memory decision install")
        return
    _set_provider("local")
    console.print('[green]decision lane on[/green] [dim](provider = "local")[/dim]')


@app.command("disable")
def disable_cmd() -> None:
    """Stop sending decision calls to the server (provider = "off"); the server keeps running."""
    _require_api()
    _set_provider("off")
    console.print(
        '[yellow]decision lane off[/yellow] [dim](provider = "off"; the engine uses its '
        "LLM lane. `hal0 memory decision remove` frees the RAM.)[/dim]"
    )


@app.command("remove")
def remove_cmd(
    purge_model: bool = typer.Option(
        False, "--purge-model", help="Also delete the downloaded model (~1.5 GB)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Stop and delete the hal0-hs-decide unit and its venv; keeps the model unless --purge-model."""
    _require_api()
    if not yes and os.isatty(0):
        typer.confirm(
            "Remove hal0-hs-decide and its venv"
            + (" and the downloaded model" if purge_model else "")
            + "?",
            default=False,
            abort=True,
        )
    _converge({"action": "remove", "purge_model": purge_model})
    console.print(
        "[green]decision model removed[/green]"
        + ("" if purge_model else " [dim](model kept in the memory HF cache)[/dim]")
    )
