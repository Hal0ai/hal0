"""``hal0 memory recall`` — debug recall through the ACL front door.

Hits ``POST /api/memory/recall`` (:mod:`hal0.api.routes.memory`), the
*namespace* recall used by agents at runtime — ACL-scoped, cross-bank
fan-out, envelope ``{items: [...]}``. This is deliberately NOT the admin
bank console recall (``POST /api/memory/banks/{bank}/recall``, envelope
``{results: [...]}``) — see issue #1026 for why the two must not be
conflated. Use this command to see exactly what an agent would recall.

``--bank`` is passed through verbatim as the ``dataset`` filter, so it
means a *namespace* (``shared``, ``private:<agent>``, ...), not a raw
Hindsight bank id — the two overlap for private banks (``private__X`` ↔
``private:X``) but are not spelled the same.
"""

from __future__ import annotations

import json as jsonlib

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_post, die

console = Console()


def recall_cmd(
    query: str = typer.Argument(..., help="Recall query text."),
    bank: str | None = typer.Option(
        None,
        "--bank",
        help="Namespace/dataset to recall from (e.g. 'shared', 'private:<agent>'). "
        "Omit to use the caller's default (anonymous → shared).",
    ),
    tags: list[str] = typer.Option([], "--tags", help="Filter by tag (repeatable)."),
    types: list[str] = typer.Option([], "--types", help="Filter by memory type (repeatable)."),
    max_tokens: int = typer.Option(4096, "--max-tokens", help="Token budget for the recalled set."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """Debug recall — see what an agent would get back for a query."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    body: dict[str, object] = {"query": query, "max_tokens": max_tokens}
    if bank:
        body["dataset"] = bank
    if tags:
        body["tags"] = list(tags)
    if types:
        body["types"] = list(types)

    try:
        result = api_post("/api/memory/recall", json=body)
    except CliApiError as exc:
        die(str(exc))
        return

    items = (result or {}).get("items", []) if isinstance(result, dict) else []
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    if not items:
        console.print("[dim]No results.[/dim]")
        return
    t = Table(title=f"memory · recall {query!r}")
    t.add_column("type")
    t.add_column("content")
    t.add_column("tags")
    for item in items:
        if isinstance(item, dict):
            t.add_row(
                str(item.get("type", "—")),
                str(item.get("content") or item.get("text") or item),
                ", ".join(item.get("tags") or []),
            )
        else:
            t.add_row("—", str(item), "")
    console.print(t)


__all__ = ["recall_cmd"]
