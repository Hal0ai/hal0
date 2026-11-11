"""``hal0 memory models`` and ``hal0 memory agents`` — showcase console CLI
parity for ``/api/memory/models`` and ``/api/memory/agents``.

``agents install`` runs the upstream ``hindsight-coding-agents`` installer and
then writes/merges ``~/.hindsight/coding-agent.json`` with the showcase
defaults (``pageTriggerType: after-consolidation``, ``observationScopes:
shared``, ``retainTags: [agent:<harness>]``) — matching
``work/notes/memory-showcase/spec/templates/coding-agent.json``.
"""

from __future__ import annotations

import json as jsonlib
import shlex
import subprocess
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, die

models_app = typer.Typer(help="Inspect the configured embeddings/reranker/decision models.")
agents_app = typer.Typer(help="Install and configure coding-agent memory integrations.")
console = Console()

_HINDSIGHT_COMMAND = "npx @vectorize-io/hindsight-coding-agents@0.8.5"


def _require_api() -> str:
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    return url


@models_app.command("show", help="Show the configured embeddings/reranker/decision models.")
@models_app.callback(invoke_without_command=True)
def models_cmd(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """``hal0 memory models`` — print the active embeddings/reranker/decision config."""
    if ctx.invoked_subcommand is not None:
        return
    _require_api()
    try:
        result = api_get("/api/memory/models")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    t = Table(title="memory · models")
    t.add_column("role")
    t.add_column("provider")
    t.add_column("model")
    t.add_column("status")
    emb = result.get("embeddings", {})
    rer = result.get("reranker", {})
    dec = result.get("decision", {})
    # #3518: the engine runs the embedder the store was built with
    # ("recorded"), which can differ from the configured provider and model.
    emb_provider = str(emb.get("recorded_provider") or emb.get("provider", "—"))
    emb_model = str(emb.get("recorded") or emb.get("model", "—"))
    emb_notes: list[str] = []
    recorded = (emb.get("recorded_provider"), emb.get("recorded"))
    configured = (emb.get("configured_provider"), emb.get("configured"))
    if emb.get("recorded") and recorded != configured:
        wanted = emb.get("configured")
        if emb.get("recorded_provider") != emb.get("configured_provider"):
            wanted = f"{emb.get('configured_provider')}/{wanted}"
        emb_notes.append(
            f"configured: {wanted} (not applied; `hal0 memory embedder switch` moves the store)"
        )
    if emb.get("mixed"):
        emb_notes.append(
            "store may hold vectors from two embedders; `hal0 memory embedder switch` repairs it"
        )
    if emb.get("conflict"):
        emb_notes.append("record and engine disagree, see hal0 doctor")
    emb_status = f"[yellow]{'; '.join(emb_notes)}[/yellow]" if emb_notes else ""
    t.add_row("embeddings", emb_provider, emb_model, emb_status)
    t.add_row("reranker", str(rer.get("provider", "—")), str(rer.get("model", "—")), "")
    status = "[green]reachable[/green]" if dec.get("reachable") else "[red]unreachable[/red]"
    t.add_row(
        "decision",
        str(dec.get("provider", "—")),
        str(dec.get("model", "—")),
        status if dec.get("enabled") else "[dim]disabled[/dim]",
    )
    console.print(t)


def _hindsight_config_path() -> Path:
    return Path.home() / ".hindsight" / "coding-agent.json"


def _merge_coding_agent_config(path: Path, harness: str) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = jsonlib.loads(path.read_text())
        except (jsonlib.JSONDecodeError, OSError):
            existing = {}
    retain_tags = set(existing.get("retainTags") or [])
    retain_tags.add(f"agent:{harness}")
    merged = {
        **existing,
        "pageTriggerType": "after-consolidation",
        "observationScopes": "shared",
        "retainTags": sorted(retain_tags),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(jsonlib.dumps(merged, indent=2, sort_keys=True) + "\n")
    return merged


@agents_app.command("install")
def agents_install_cmd(
    harness: str = typer.Argument(
        ..., help="Coding-agent harness name (e.g. claude-code, cursor, codex)."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the installer command and config merge without running/writing.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Install a coding-agent's Hindsight memory integration (showcase defaults)."""
    _require_api()
    try:
        agents = api_get("/api/memory/agents")
    except CliApiError as exc:
        die(str(exc))
        return
    install = agents.get("install", {}) if isinstance(agents, dict) else {}
    command = (
        install.get("command") or f"{_HINDSIGHT_COMMAND} install {harness} --server self-hosted"
    )
    # The template command carries a literal "<harness>" placeholder.
    command = command.replace("<harness>", harness)

    config_path = _hindsight_config_path()
    if dry_run:
        payload = {"command": command, "config_path": str(config_path), "dry_run": True}
        if json_out:
            typer.echo(jsonlib.dumps(payload, indent=2, sort_keys=True))
        else:
            console.print(
                Panel(
                    f"[yellow]Would run:[/yellow] {command}\n[yellow]Would write:[/yellow] {config_path}"
                )
            )
        return

    try:
        proc = subprocess.run(shlex.split(command), capture_output=True, text=True, check=False)
    except OSError as exc:
        die(f"failed to run installer: {exc}")
        return
    if proc.returncode != 0:
        die(f"installer exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}")
        return

    merged = _merge_coding_agent_config(config_path, harness)
    if json_out:
        typer.echo(
            jsonlib.dumps(
                {
                    "command": command,
                    "config_path": str(config_path),
                    "config": merged,
                    "stdout": proc.stdout,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    console.print(Panel(proc.stdout or "[dim](no output)[/dim]", title=f"installer · {harness}"))
    console.print(
        Panel(jsonlib.dumps(merged, indent=2, sort_keys=True), title=f"wrote {config_path}")
    )


__all__ = ["agents_app", "models_app"]
