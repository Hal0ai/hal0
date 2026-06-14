"""hal0 memory subcommands — ADR-0014 graph-extraction gate.

Mirrors the slot / model CLI shape: a thin HTTP client to the local
hal0 API. The ``graph`` sub-sub-app maps 1:1 to the routes in
:mod:`hal0.api.routes.memory`:

    hal0 memory graph status                            → GET  /api/memory/graph/status
    hal0 memory graph enable [--route ...] [--provider …] [--model …]
                                                        → PUT  /api/memory/graph (enabled=true …)
    hal0 memory graph disable                           → PUT  /api/memory/graph (enabled=false)

PLAN.md §13 ("CLI is a thin client") — every command hits 127.0.0.1:8080.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    api_put,
    die,
)
from hal0.memory.migrate import migrate_cognee_to_hindsight_dryrun

app = typer.Typer(help="Manage hal0 memory (Cognee — ADR-0005 + ADR-0014).")
console = Console()

# ``graph`` sub-sub-app so ``hal0 memory graph --help`` renders cleanly
# alongside ``hal0 memory --help``. Same pattern as ``hal0 agent approvals``.
graph_app = typer.Typer(help="Graph-extraction gate (ADR-0014).")
app.add_typer(graph_app, name="graph")


# ── ``hal0 memory graph status`` ──────────────────────────────────────────────


@graph_app.command("status")
def graph_status_cmd(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit raw JSON instead of the human-readable panel.",
    ),
) -> None:
    """Show the live graph-extraction status (enabled / route / counters)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        s = api_get("/api/memory/graph/status")
    except CliApiError as exc:
        die(str(exc))
        return
    if not isinstance(s, dict):
        die(f"unexpected status payload: {s!r}")
        return
    if json_out:
        typer.echo(jsonlib.dumps(s, indent=2, sort_keys=True))
        return

    enabled = bool(s.get("enabled"))
    state = "[bold green]ON[/bold green]" if enabled else "[bold]OFF[/bold]"
    route = s.get("route", "—")
    upstream = s.get("upstream") or {}
    upstream_line = (
        f"{upstream.get('provider', '?')} · {upstream.get('model', '?')}"
        if upstream
        else "[dim]not configured[/dim]"
    )

    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("State", state)
    t.add_row("Route", str(route))
    t.add_row("Upstream", upstream_line)
    t.add_row("Builds OK", str(s.get("builds_ok", 0)))
    t.add_row("Errors", str(s.get("errors", 0)))
    t.add_row("In-flight", str(s.get("in_flight", 0)))
    last = s.get("last_built_at") or "[dim]never[/dim]"
    t.add_row("Last build", str(last))
    if s.get("last_error"):
        t.add_row("Last error", f"[red]{s['last_error']}[/red]")
    console.print(Panel(t, title="memory · graph", border_style="dim"))


# ── ``hal0 memory graph enable`` ──────────────────────────────────────────────


@graph_app.command("enable")
def graph_enable_cmd(
    route: str = typer.Option(
        "upstream",
        "--route",
        help="Where to dispatch graph extraction: upstream | primary | agent.",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="Upstream provider id (required when --route=upstream).",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Upstream model id (required when --route=upstream).",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw JSON response instead of a panel."
    ),
) -> None:
    """Turn graph extraction ON.

    ``--route=upstream`` requires ``--provider`` + ``--model``. The
    server-side validator ALSO enforces this, so the CLI can be skipped
    in scripts that hit the API directly.
    """
    if route not in {"upstream", "primary", "agent"}:
        die(f"--route must be one of upstream | primary | agent (got {route!r})")
        return
    if route == "upstream" and (not provider or not model):
        die("--route=upstream requires --provider and --model")
        return

    payload: dict[str, Any] = {"enabled": True, "route": route}
    if route == "upstream":
        payload["upstream"] = {"provider": provider, "model": model}

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_put("/api/memory/graph", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    console.print(
        Panel(
            f"[bold green]Graph extraction enabled[/bold green]\n"
            f"route = [bold]{result.get('route')}[/bold]",
            border_style="green",
        )
    )


# ── ``hal0 memory graph disable`` ─────────────────────────────────────────────


@graph_app.command("disable")
def graph_disable_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw JSON response instead of a panel."
    ),
) -> None:
    """Turn graph extraction OFF; cancels any in-flight build (ADR §6)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_put("/api/memory/graph", json={"enabled": False})
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    console.print(
        Panel(
            "[bold]Graph extraction disabled[/bold]\n[dim]In-flight builds cancelled.[/dim]",
            border_style="yellow",
        )
    )


# ── ``hal0 memory migrate`` ───────────────────────────────────────────────────

_DEFAULT_COGNEE_DIR = "/var/lib/hal0/memory/cognee"


@app.command("migrate")
def migrate_cmd(
    dry_run: bool = typer.Option(
        True,
        "--dry-run",
        help="Report the migration plan without writing. Dry-run only — apply/write mode is not yet implemented.",
    ),
    cognee_dir: str = typer.Option(
        _DEFAULT_COGNEE_DIR,
        "--cognee-dir",
        help="Path to the Cognee data directory (contains hal0_memory_index.sqlite).",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit raw JSON instead of the human-readable panel.",
    ),
) -> None:
    """Migrate Cognee memory store → Hindsight banks (dry-run only, P2-4)."""
    if not dry_run:
        die("--apply is not yet implemented; dry-run only.")
        return
    report = migrate_cognee_to_hindsight_dryrun(cognee_dir=cognee_dir)
    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
        return
    noop_label = (
        "[dim]yes — nothing to migrate[/dim]" if report["noop"] else "[bold yellow]no[/bold yellow]"
    )
    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("Rows total", str(report["rows_total"]))
    t.add_row("Rows mapped", str(report["rows_mapped"]))
    t.add_row("Rows unmapped", str(report["rows_unmapped"]))
    t.add_row("No-op", noop_label)
    console.print(Panel(t, title="memory · migrate (dry-run)", border_style="dim"))


__all__ = ["app", "graph_app"]
