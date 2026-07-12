"""hal0 memory subcommands — ADR-0014 graph-extraction gate.

Mirrors the slot / model CLI shape: a thin HTTP client to the local
hal0 API. The ``graph`` sub-sub-app maps 1:1 to the routes in
:mod:`hal0.api.routes.memory`:

    hal0 memory status                                  → GET  /api/status (memory_enabled)
    hal0 memory enable                                  → PUT  /api/settings (memory.enabled=true)
    hal0 memory disable                                 → PUT  /api/settings (memory.enabled=false)
    hal0 memory graph status                            → GET  /api/memory/graph/status
    hal0 memory graph enable [--route ...] [--provider …] [--model …]
                                                        → PUT  /api/memory/graph (enabled=true …)
    hal0 memory graph disable                           → PUT  /api/memory/graph (enabled=false)

``hal0 memory enable``/``disable`` replace the old ``HAL0_MEMORY_ENABLED``
env var (removed) — the whole subsystem (Hindsight engine, /mcp/memory,
/api/memory/*, the dashboard's Agent → Memory tab) is gated by
``[memory].enabled`` in hal0.toml, defaulting to True. The provider is
built once at ``create_app()``, so flipping it needs a ``hal0-api``
restart, same as ``hal0 memory graph`` needs one for ``memory.engine``.

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

app = typer.Typer(help="Manage hal0 memory (Hindsight engine).")
console = Console()

# ``graph`` sub-sub-app so ``hal0 memory graph --help`` renders cleanly
# alongside ``hal0 memory --help``. Same pattern as ``hal0 agent approvals``.
graph_app = typer.Typer(help="Graph-extraction settings (ADR-0023).")
app.add_typer(graph_app, name="graph")

# ``provider`` sub-sub-app — per-agent Hindsight/Honcho routing.
provider_app = typer.Typer(help="Per-agent memory provider (Hindsight | Honcho).")
app.add_typer(provider_app, name="provider")


# ── ``hal0 memory status`` ─────────────────────────────────────────────────


@app.command("status")
def status_cmd(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit raw JSON instead of the human-readable panel.",
    ),
) -> None:
    """Show whether the memory subsystem is enabled and live."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        s = api_get("/api/status")
    except CliApiError as exc:
        die(str(exc))
        return
    if not isinstance(s, dict):
        die(f"unexpected status payload: {s!r}")
        return
    if json_out:
        typer.echo(
            jsonlib.dumps(
                {
                    "memory_enabled": s.get("memory_enabled"),
                    "memory_degraded": s.get("memory_degraded"),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    enabled = bool(s.get("memory_enabled"))
    state = "[bold green]ON[/bold green]" if enabled else "[bold]OFF[/bold]"
    degraded = s.get("memory_degraded")
    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("State", state)
    if enabled and degraded is True:
        t.add_row(
            "Provider",
            "[yellow]in-memory fallback (volatile — Hindsight unreachable)[/yellow]",
        )
    elif enabled:
        t.add_row("Provider", "[green]durable[/green]")
    console.print(Panel(t, title="memory · status", border_style="dim"))


# ── ``hal0 memory enable`` / ``hal0 memory disable`` ───────────────────────


def _set_enabled(enabled: bool, json_out: bool) -> None:
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_put("/api/settings", json={"memory": {"enabled": enabled}})
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    verb = "enabled" if enabled else "disabled"
    color = "green" if enabled else "yellow"
    console.print(
        Panel(
            f"[bold {color}]Memory subsystem {verb}[/bold {color}]\n"
            "[dim]Takes effect on the next hal0-api restart: "
            "systemctl restart hal0-api[/dim]",
            border_style=color,
        )
    )


@app.command("enable")
def enable_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw JSON response instead of a panel."
    ),
) -> None:
    """Enable the memory subsystem (persists [memory].enabled=true)."""
    _set_enabled(True, json_out)


@app.command("disable")
def disable_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw JSON response instead of a panel."
    ),
) -> None:
    """Disable the memory subsystem (persists [memory].enabled=false)."""
    _set_enabled(False, json_out)


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
    slot = s.get("extraction_slot", s.get("route", "—"))
    resolves = s.get("slot_resolves")
    if resolves is True:
        slot_line = f"[bold]{slot}[/bold] [green](resolves)[/green]"
    elif resolves is False:
        slot_line = f"[bold]{slot}[/bold] [red](no matching enabled llm slot)[/red]"
    else:
        slot_line = f"[bold]{slot}[/bold]"
    available = s.get("available_slots") or []

    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("State", state)
    t.add_row("Extraction slot", slot_line)
    if available:
        t.add_row("Available slots", ", ".join(available))
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
    slot: str | None = typer.Option(
        None,
        "--slot",
        help="Local llm slot used for graph extraction (e.g. 'utility'). "
        "Must be an enabled type=llm slot; the server validates against the live "
        "slot set and restarts hindsight-api to point its extraction LLM there. "
        "Omit to keep the current slot.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw JSON response instead of a panel."
    ),
) -> None:
    """Turn graph extraction ON (optionally repointing the extraction slot)."""
    payload: dict[str, Any] = {"enabled": True}
    if slot is not None:
        payload["extraction_slot"] = slot

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
    prop = result.get("propagation") or {}
    prop_line = ""
    if prop:
        if prop.get("error"):
            prop_line = f"\n[red]hindsight-api restart: {prop['error']}[/red]"
        elif prop.get("restarted"):
            prop_line = "\n[dim]hindsight-api restarted on the new slot.[/dim]"
    console.print(
        Panel(
            f"[bold green]Graph extraction enabled[/bold green]\n"
            f"extraction slot = [bold]{result.get('extraction_slot')}[/bold]{prop_line}",
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


# ── ``hal0 memory provider list|status|set`` ──────────────────────────────────


def _render_provider_status(s: dict[str, Any]) -> None:
    engines = s.get("engines") or {}
    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    for name in ("hindsight", "honcho"):
        e = engines.get(name) or {}
        healthy = "[bold green]healthy[/bold green]" if e.get("healthy") else "[bold red]down[/bold red]"
        t.add_row(name, f"{healthy}  [dim]{e.get('url', '—')}[/dim]")
    console.print(Panel(t, title="memory · provider · engines", border_style="dim"))

    agents = s.get("agents") or {}
    if not agents:
        console.print("[dim]No agents routed yet — all agents default to Hindsight.[/dim]")
        return
    at = Table.grid(padding=(0, 2))
    at.add_column("agent", style="bold")
    at.add_column("provider")
    at.add_column("private")
    for agent_id, info in sorted(agents.items()):
        at.add_row(agent_id, str(info.get("provider")), "yes" if info.get("private") else "no")
    console.print(Panel(at, title="memory · provider · agents", border_style="dim"))


@provider_app.command("list")
@provider_app.command("status")
def provider_list_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit raw JSON instead of the human-readable panels."
    ),
) -> None:
    """Show engine health + the live per-agent provider routing."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        s = api_get("/api/memory/provider")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(s, indent=2, sort_keys=True))
        return
    _render_provider_status(s)


@provider_app.command("set")
def provider_set_cmd(
    agent: str = typer.Argument(..., help="Agent id to route (e.g. 'hermes')."),
    provider: str = typer.Argument(..., help="'hindsight' or 'honcho'."),
    private: bool = typer.Option(
        None,
        "--private/--no-private",
        help="Route this agent's writes to an isolated private Honcho workspace. "
        "Omit to leave any existing setting untouched.",
    ),
    restart: bool = typer.Option(
        True,
        "--restart/--no-restart",
        help="Restart the agent's gateway unit so the switch takes effect immediately.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit raw JSON instead of the human-readable panel."
    ),
) -> None:
    """Route ``AGENT``'s memory to ``PROVIDER`` and persist it."""
    if provider not in ("hindsight", "honcho"):
        die("provider must be 'hindsight' or 'honcho'")
        return
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    payload: dict[str, Any] = {"agent": agent, "provider": provider, "restart": restart}
    if private is not None:
        payload["private"] = private
    try:
        result = api_put("/api/memory/provider", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    restarted_line = (
        "[dim]gateway restarted.[/dim]"
        if result.get("restarted")
        else f"[yellow]{result.get('note') or 'not restarted.'}[/yellow]"
    )
    console.print(
        Panel(
            f"[bold green]{agent}[/bold green] → provider = [bold]{result.get('provider')}[/bold]"
            f" (private={result.get('private')})\n{restarted_line}",
            border_style="green",
        )
    )


# ── ``hal0 memory migrate`` ───────────────────────────────────────────────────

_DEFAULT_COGNEE_DIR = "/var/lib/hal0/memory/cognee"


_VALID_MIGRATE_ENGINES = ("hindsight", "honcho")


@app.command("migrate")
def migrate_cmd(
    from_: str = typer.Option(
        None,
        "--from",
        help="Source engine for a Hindsight<->Honcho migration: 'hindsight' or 'honcho'. "
        "Omit both --from and --to to run the legacy Cognee→Hindsight dry-run report instead.",
    ),
    to: str = typer.Option(
        None,
        "--to",
        help="Destination engine for a Hindsight<->Honcho migration: 'hindsight' or 'honcho'.",
    ),
    agent: str = typer.Option(
        "hermes",
        "--agent",
        help="Agent id whose memory is being migrated (identity + private-bucket scope).",
    ),
    dataset: list[str] = typer.Option(
        [],
        "--dataset",
        help="Hindsight dataset to migrate (repeatable). Default: shared + the agent's "
        "own private bucket. Only meaningful for --from hindsight.",
    ),
    since: str = typer.Option(
        None,
        "--since",
        help="ISO8601 watermark override for --from honcho (defaults to the saved watermark).",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume from the last saved per-dataset cursor instead of rescanning from the start "
        "(--from hindsight only). Id-level dedupe applies either way.",
    ),
    dry_run: bool = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Report the migration plan without writing. Legacy Cognee path defaults to "
        "True (apply mode is not implemented there); the Hindsight<->Honcho engine "
        "(--from/--to) defaults to False.",
    ),
    cognee_dir: str = typer.Option(
        _DEFAULT_COGNEE_DIR,
        "--cognee-dir",
        help="Path to the Cognee data directory (contains hal0_memory_index.sqlite). "
        "Legacy Cognee path only.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit raw JSON instead of the human-readable panel.",
    ),
) -> None:
    """Migrate memory between engines.

    Bare ``hal0 memory migrate`` runs the legacy Cognee→Hindsight dry-run
    report (P2-4). ``--from hindsight --to honcho`` (or the reverse) runs
    the Hindsight<->Honcho bidirectional migration engine in-process
    against the local hal0-api + Honcho REST surfaces.
    """
    if from_ is None and to is None:
        legacy_dry_run = True if dry_run is None else dry_run
        if not legacy_dry_run:
            die("--apply is not yet implemented; dry-run only.")
            return
        report = migrate_cognee_to_hindsight_dryrun(cognee_dir=cognee_dir)
        if json_out:
            typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
            return
        noop_label = (
            "[dim]yes — nothing to migrate[/dim]"
            if report["noop"]
            else "[bold yellow]no[/bold yellow]"
        )
        t = Table.grid(padding=(0, 2))
        t.add_column("k", style="dim")
        t.add_column("v")
        t.add_row("Rows total", str(report["rows_total"]))
        t.add_row("Rows mapped", str(report["rows_mapped"]))
        t.add_row("Rows unmapped", str(report["rows_unmapped"]))
        t.add_row("No-op", noop_label)
        console.print(Panel(t, title="memory · migrate (dry-run)", border_style="dim"))
        return

    if from_ not in _VALID_MIGRATE_ENGINES or to not in _VALID_MIGRATE_ENGINES or from_ == to:
        die(
            "--from/--to must be one of 'hindsight'/'honcho' and differ, "
            f"got --from={from_!r} --to={to!r}"
        )
        return

    engine_dry_run = False if dry_run is None else dry_run
    cfg = _load_honcho_cli_config()
    honcho_base = f"http://127.0.0.1:{cfg.honcho.port}"
    state = _migrate_state()

    if from_ == "hindsight":
        report = _run_migrate_hindsight_to_honcho(
            honcho_base=honcho_base,
            workspace=cfg.honcho.workspace,
            user_peer=cfg.honcho.user_peer,
            agent_id=agent,
            datasets=list(dataset) or None,
            dry_run=engine_dry_run,
            resume=resume,
            state=state,
            json_out=json_out,
        )
    else:
        report = _run_migrate_honcho_to_hindsight(
            honcho_base=honcho_base,
            workspace=cfg.honcho.workspace,
            agent_id=agent,
            since=since,
            dry_run=engine_dry_run,
            state=state,
            json_out=json_out,
        )
    if not engine_dry_run:
        state.save()
    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))


def _load_honcho_cli_config() -> Any:
    from hal0.config.loader import load_hal0_config

    return load_hal0_config()


def _migrate_state() -> Any:
    from hal0.memory.honcho_migrate import MigrateState

    return MigrateState()


def _run_migrate_hindsight_to_honcho(
    *,
    honcho_base: str,
    workspace: str,
    user_peer: str,
    agent_id: str,
    datasets: list[str] | None,
    dry_run: bool,
    resume: bool,
    state: Any,
    json_out: bool,
) -> dict[str, Any]:
    from hal0.memory.honcho_migrate import migrate_hindsight_to_honcho

    def on_progress(msg: str) -> None:
        if not json_out:
            console.print(f"[dim]{msg}[/dim]")

    report = migrate_hindsight_to_honcho(
        hal0_base=_api_base(),
        honcho_base=honcho_base,
        workspace=workspace,
        user_peer=user_peer,
        agent_id=agent_id,
        datasets=datasets,
        dry_run=dry_run,
        resume=resume,
        state=state,
        on_progress=on_progress,
    )
    if not json_out:
        t = Table.grid(padding=(0, 2))
        t.add_column("k", style="dim")
        t.add_column("v")
        for ds, counts in report.items():
            if ds == "total":
                continue
            t.add_row(ds, f"scanned={counts['scanned']} migrated={counts['migrated']} skipped={counts['skipped']}")
        total = report["total"]
        t.add_row(
            "[bold]total[/bold]",
            f"scanned={total['scanned']} migrated={total['migrated']} skipped={total['skipped']}",
        )
        title = "memory · migrate hindsight→honcho" + (" (dry-run)" if dry_run else "")
        console.print(Panel(t, title=title, border_style="dim" if dry_run else "green"))
    return report


def _run_migrate_honcho_to_hindsight(
    *,
    honcho_base: str,
    workspace: str,
    agent_id: str,
    since: str | None,
    dry_run: bool,
    state: Any,
    json_out: bool,
) -> dict[str, Any]:
    from hal0.memory.honcho_migrate import migrate_honcho_to_hindsight

    def on_progress(msg: str) -> None:
        if not json_out:
            console.print(f"[dim]{msg}[/dim]")

    report = migrate_honcho_to_hindsight(
        hal0_base=_api_base(),
        honcho_base=honcho_base,
        workspace=workspace,
        agent_id=agent_id,
        since=since,
        dry_run=dry_run,
        state=state,
        on_progress=on_progress,
    )
    if not json_out:
        t = Table.grid(padding=(0, 2))
        t.add_column("k", style="dim")
        t.add_column("v")
        t.add_row("Scanned", str(report["scanned"]))
        t.add_row("Migrated", str(report["migrated"]))
        t.add_row("Skipped", str(report["skipped"]))
        t.add_row("Watermark", str(report["watermark"]))
        title = "memory · migrate honcho→hindsight" + (" (dry-run)" if dry_run else "")
        console.print(Panel(t, title=title, border_style="dim" if dry_run else "green"))
    return report


# ── ``hal0 memory sync-graph`` ─────────────────────────────────────────────────


@app.command("sync-graph")
def sync_graph_cmd(
    agent: str = typer.Option("hermes", "--agent", help="Agent id to write synced facts as."),
    json_out: bool = typer.Option(
        False, "--json", help="Emit terse JSON (systemd-timer friendly)."
    ),
) -> None:
    """Sync Honcho conclusions → Hindsight (resumes from the saved watermark).

    Equivalent to ``migrate --from honcho --to hindsight``, wired as its own
    command for the recurring ``hal0-honcho-sync.timer`` job — no
    ``--resume``/``--dataset`` plumbing to think about, just "sync what's new".
    """
    cfg = _load_honcho_cli_config()
    honcho_base = f"http://127.0.0.1:{cfg.honcho.port}"
    state = _migrate_state()
    report = _run_migrate_honcho_to_hindsight(
        honcho_base=honcho_base,
        workspace=cfg.honcho.workspace,
        agent_id=agent,
        since=None,
        dry_run=False,
        state=state,
        json_out=json_out,
    )
    state.save()
    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))


# ── ``hal0 memory honcho render-env`` ──────────────────────────────────────────

honcho_app = typer.Typer(help="Honcho stack config rendering.")
app.add_typer(honcho_app, name="honcho")


@honcho_app.command("render-env")
def honcho_render_env_cmd(
    restart: bool = typer.Option(
        True, "--restart/--no-restart", help="Restart the Honcho compose stack if the rendered env changed."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit raw JSON instead of the human-readable panel."
    ),
) -> None:
    """Render ``/etc/hal0/honcho.env`` from ``hal0.toml [honcho]`` and (best-effort) restart the stack."""
    try:
        from hal0.memory.honcho_env import apply_honcho_env
    except ImportError as exc:
        die(f"honcho_env module not available yet: {exc}")
        return
    cfg = _load_honcho_cli_config()
    result = apply_honcho_env(cfg, restart=restart)
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    if result.get("error"):
        console.print(Panel(f"[red]{result['error']}[/red]", border_style="red"))
        raise typer.Exit(1)
    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("Written", str(result.get("written")))
    t.add_row("Changed", str(result.get("changed")))
    t.add_row("Restarted", str(result.get("restarted")))
    console.print(Panel(t, title="memory · honcho render-env", border_style="dim"))


__all__ = ["app", "graph_app", "honcho_app", "provider_app"]
