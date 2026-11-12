"""``hal0 memory mm`` — mental-model admin (list / refresh / history / repair)."""

from __future__ import annotations

import json as jsonlib
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    api_patch,
    api_post,
    die,
)

app = typer.Typer(help="Manage Hindsight mental models.")
console = Console()


def _require_api() -> str:
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    return url


@app.command("list")
def mm_list_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """List a bank's mental models."""
    _require_api()
    try:
        # hindsight-api >= 0.10.0 lists metadata only by default; ask for
        # content so ``--json`` keeps carrying each model's content and
        # source_query, as it did on older engines.
        result = api_get(f"/api/memory/banks/{bank}/mental-models", params={"detail": "content"})
    except CliApiError as exc:
        die(str(exc))
        return
    items = result.get("items") if isinstance(result, dict) and "items" in result else result
    items = items if isinstance(items, list) else []

    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    if not items:
        console.print("[dim]No mental models.[/dim]")
        return
    t = Table(title=f"memory · bank {bank} · mental models")
    t.add_column("id")
    t.add_column("name")
    t.add_column("updated_at")
    for m in items:
        t.add_row(str(m.get("id", "—")), str(m.get("name", "—")), str(m.get("updated_at", "—")))
    console.print(t)


@app.command("refresh")
def mm_refresh_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    model_id: str = typer.Argument(..., help="Mental model id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Trigger a refresh of a mental model (async)."""
    _require_api()
    try:
        result = api_post(f"/api/memory/banks/{bank}/mental-models/{model_id}/refresh")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    console.print(Panel(f"[bold green]Refresh queued[/bold green]\n{result}", border_style="green"))


@app.command("history")
def mm_history_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    model_id: str = typer.Argument(..., help="Mental model id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """Show a mental model's revision history."""
    _require_api()
    try:
        result = api_get(f"/api/memory/banks/{bank}/mental-models/{model_id}/history")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    entries: Any = result.get("items") if isinstance(result, dict) and "items" in result else result
    entries = entries if isinstance(entries, list) else []
    if not entries:
        console.print("[dim]No history.[/dim]")
        return
    t = Table(title=f"memory · mental model {model_id} · history")
    t.add_column("at")
    t.add_column("summary")
    for e in entries:
        if isinstance(e, dict):
            t.add_row(
                str(e.get("updated_at") or e.get("created_at") or "—"), str(e.get("content") or e)
            )
        else:
            t.add_row("—", str(e))
    console.print(t)


def _admin_request(method: str, path: str, *, params: Any = None, json: Any = None) -> Any:
    """The ``request`` :mod:`hal0.memory.model_owner_repair` runs on: hal0-api."""
    if method == "GET":
        return api_get(path, params=params, timeout=30.0)
    if method == "PATCH":
        return api_patch(path, json=json, timeout=30.0)
    if method == "POST":
        return api_post(path, json=json, timeout=30.0)
    raise ValueError(f"unsupported method {method}")


@app.command("repair")
def mm_repair_cmd(
    bank: list[str] = typer.Option(
        [], "--bank", help="Bank to repair (repeatable). Omit for every bank."
    ),
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="Write the flag. Default is --dry-run: list what would change, write nothing.",
    ),
    refresh: bool = typer.Option(
        True,
        "--refresh/--no-refresh",
        help="With --apply, queue a refresh of each repaired model so its content is "
        "rebuilt under the confined scope (default).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of lines."),
) -> None:
    """Give agents back their own mental models and knowledge pages (#3576).

    Sets ``trigger.exclude_mental_models`` on every model whose scope is
    otherwise confined to one agent's ``agent:<id>`` tag, so that agent can
    read it while its bank holds another agent's private memory. Shared,
    project-wide and whole-bank models are never touched. Safe to run again.
    """
    from hal0.memory.model_owner_repair import apply_repairs, survey

    _require_api()
    try:
        report = survey(_admin_request, banks=bank or None)
    except CliApiError as exc:
        die(f"could not list the memory banks: {exc}")
        return
    results = apply_repairs(_admin_request, report.findings, refresh=refresh) if apply else []
    failed = {(r.finding.bank, r.finding.model_id): r.error for r in results if r.error}
    ops = {(r.finding.bank, r.finding.model_id): r.operation_id for r in results}

    if json_out:
        rows = []
        for f in report.findings:
            row: dict[str, Any] = {
                "bank": f.bank,
                "id": f.model_id,
                "name": f.name,
                "owner": f.owner,
                "hidden": f.hidden,
            }
            if apply:
                row["error"] = failed.get((f.bank, f.model_id))
                row["operation_id"] = ops.get((f.bank, f.model_id))
            rows.append(row)
        payload = {
            "applied": apply,
            "models": rows,
            "errors": [{"bank": b, "error": e} for b, e in report.errors],
        }
        typer.echo(jsonlib.dumps(payload, indent=2, sort_keys=True))
    else:
        for b, err in report.errors:
            console.print(f"[red]could not read bank {escape(b)}:[/red] {escape(err)}")
        for f in report.findings:
            key = (f.bank, f.model_id)
            where = (
                f"{escape(f.bank)} {escape(f.model_id)} ({escape(f.name)}) owner {escape(f.owner)}"
            )
            hidden = " [yellow]hidden from its owner now[/yellow]" if f.hidden else ""
            if not apply:
                verb = "would set exclude_mental_models on"
            elif key in failed:
                verb = f"[red]failed[/red] ({escape(str(failed[key]))}):"
            else:
                verb = "set exclude_mental_models on"
                hidden += f" refresh queued {ops[key]}" if ops.get(key) else ""
            console.print(f"{verb} {where}{hidden}")
        if not report.findings:
            console.print(f"Nothing to repair in {report.banks} bank(s).")
        elif not apply:
            console.print(
                f"[dim]{len(report.findings)} model(s) to repair; run again with --apply.[/dim]"
            )
    if failed or report.errors:
        raise typer.Exit(1)


__all__ = ["app"]
