"""``hal0 memory ops`` — cross-bank async-operation admin.

Hindsight's operations API (``/v1/default/banks/{bank}/operations...``) is
per-bank only — there is no cross-bank listing endpoint upstream. When the
caller omits ``--bank`` these commands fan out across every bank returned by
``GET /api/memory/banks`` and merge the results, tagging each row with its
``bank_id`` so the output still reads as one list.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, api_post, die

app = typer.Typer(help="Manage Hindsight async operations (retain, consolidation, refresh, ...).")
console = Console()


def _require_api() -> str:
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    return url


def _all_bank_ids() -> list[str]:
    result = api_get("/api/memory/banks")
    banks = (result or {}).get("banks", []) if isinstance(result, dict) else []
    return [str(b["bank_id"]) for b in banks if "bank_id" in b]


def _list_bank_ops(bank: str, *, failed_only: bool) -> list[dict[str, Any]]:
    params = {"limit": "100"}
    if failed_only:
        params["status"] = "failed"
    result = api_get(f"/api/memory/banks/{bank}/operations", params=params)
    ops = (result or {}).get("operations", []) if isinstance(result, dict) else []
    for op in ops:
        op["bank_id"] = bank
    return ops


# ── ``hal0 memory ops list`` ────────────────────────────────────────────────


@app.command("list")
def ops_list_cmd(
    bank: str | None = typer.Option(
        None, "--bank", help="Restrict to a single bank. Omit to fan out across all banks."
    ),
    failed: bool = typer.Option(False, "--failed", help="Only show operations with status=failed."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """List async operations (retain, consolidation, refresh_mental_model, ...)."""
    _require_api()
    try:
        if bank:
            ops = _list_bank_ops(bank, failed_only=failed)
        else:
            ops = []
            for b in _all_bank_ids():
                ops.extend(_list_bank_ops(b, failed_only=failed))
    except CliApiError as exc:
        die(str(exc))
        return

    if json_out:
        typer.echo(jsonlib.dumps({"operations": ops}, indent=2, sort_keys=True))
        return
    if not ops:
        console.print("[dim]No operations.[/dim]")
        return
    t = Table(title="memory · operations" + (" (failed)" if failed else ""))
    t.add_column("bank_id")
    t.add_column("id")
    t.add_column("type")
    t.add_column("status")
    t.add_column("items")
    t.add_column("created_at")
    t.add_column("error")
    for op in ops:
        t.add_row(
            str(op.get("bank_id", "—")),
            str(op.get("id", "—")),
            str(op.get("task_type", "—")),
            str(op.get("status", "—")),
            str(op.get("items_count", "—")),
            str(op.get("created_at", "—")),
            str(op.get("error_message") or ""),
        )
    console.print(t)


# ── ``hal0 memory ops retry`` ───────────────────────────────────────────────


@app.command("retry")
def ops_retry_cmd(
    bank: str | None = typer.Option(None, "--bank", help="Bank to retry operations in."),
    all_failed: bool = typer.Option(
        False,
        "--all-failed",
        help="Retry every failed operation (in --bank, or across all banks if omitted).",
    ),
    op_id: str | None = typer.Option(
        None,
        "--id",
        help="Retry a single operation id. Requires --bank (operation ids are bank-scoped).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Retry failed operations — either one by id, or every failed op in scope."""
    if bool(all_failed) == bool(op_id):
        die("pass exactly one of --all-failed or --id")
        return
    if op_id and not bank:
        die("--id requires --bank (operation ids are scoped to a bank)")
        return

    _require_api()
    results: list[dict[str, Any]] = []
    try:
        if op_id:
            r = api_post(f"/api/memory/banks/{bank}/operations/{op_id}/retry")
            r["bank_id"] = bank
            results.append(r)
        else:
            targets: list[tuple[str, str]] = []
            banks = [bank] if bank else _all_bank_ids()
            for b in banks:
                for op in _list_bank_ops(b, failed_only=True):
                    # The live Hindsight operations schema (0.8.x) isn't
                    # runtime-verified here — accept either ``id`` or
                    # ``operation_id`` and skip-with-warning rather than
                    # KeyError the whole retry loop on an unexpected row.
                    oid = op.get("id") or op.get("operation_id")
                    if not oid:
                        console.print(
                            f"[yellow]skipping failed op in {b!r} with no "
                            f"id/operation_id: {op!r}[/yellow]"
                        )
                        continue
                    targets.append((b, str(oid)))
            for b, oid in targets:
                r = api_post(f"/api/memory/banks/{b}/operations/{oid}/retry")
                r["bank_id"] = b
                results.append(r)
    except CliApiError as exc:
        die(str(exc))
        return

    if json_out:
        typer.echo(jsonlib.dumps({"retried": results}, indent=2, sort_keys=True))
        return
    if not results:
        console.print("[dim]Nothing to retry.[/dim]")
        return
    t = Table.grid(padding=(0, 2))
    t.add_column("bank")
    t.add_column("operation_id")
    t.add_column("result")
    for r in results:
        ok = "[green]queued[/green]" if r.get("success") else "[red]failed[/red]"
        t.add_row(
            str(r.get("bank_id")), str(r.get("operation_id")), f"{ok} — {r.get('message', '')}"
        )
    console.print(Panel(t, title="memory · ops retry", border_style="dim"))


__all__ = ["app"]
