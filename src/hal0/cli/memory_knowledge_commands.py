"""``hal0 memory knowledge rehome`` — put a folded bank's unplaced pages in a project folder.

Like ``hal0 memory migrate fold-in``, not a thin client of hal0-api: it runs
on the box and talks to the engine directly, a dry run by default, as root
for ``--apply``. The work is :mod:`hal0.memory.knowledge_rehome`; this module
parses the flags and prints.
"""

from __future__ import annotations

import asyncio
import contextlib
import json as jsonlib
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from hal0.memory.fold_in import FoldInError, FoldInRefused
from hal0.memory.knowledge_rehome import COMMAND, KnowledgeRehome

app = typer.Typer(help="Admin work on the knowledge pages of the platform bank, on the box.")
console = Console()
# Progress goes to stderr so --json's stdout stays one JSON document.
_progress = Console(stderr=True)


def _client() -> Any:
    from hal0.memory.hindsight_client import HindsightRestClient

    return HindsightRestClient.from_env()


def _drive(apply: bool, **kwargs: Any) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        client = _client()
        try:
            run = KnowledgeRehome(
                client,
                progress=lambda m: _progress.print(f"[dim]{escape(m)}[/dim]"),
                **kwargs,
            )
            return await (run.rehome() if apply else run.plan())
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()

    return asyncio.run(main())


def _lines(report: dict[str, Any]) -> list[str]:
    out: list[str] = []
    nodes = report.get("nodes") or {}
    folder = report.get("folder") or {}
    keep = report.get("keep_content") or {}
    owner = report.get("owner") or {}
    if nodes:
        out.append(
            f"{nodes.get('total')} node(s) of {escape(str(report.get('bank')))} in "
            f"{escape(str(report.get('target')))}: {nodes.get('pages')} page(s), "
            f"{nodes.get('folders')} folder(s), {nodes.get('top_level')} at the old bank's root"
        )
        state = "exists" if folder.get("exists") else "would be created"
        if folder.get("created"):
            state = "created"
        out.append(
            f"to move into {escape(str(folder.get('name')))} ({state}): "
            f"{nodes.get('to_move')} subtree(s), {nodes.get('in_moving_subtrees')} node(s); "
            f"already there: {nodes.get('already_placed')}"
        )
    for top in report.get("tops") or []:
        mark = "move" if top.get("state") == "move" else "in place"
        out.append(f"  {mark}: {escape(str(top.get('shown_name')))} ({top.get('kind')})")
    unfolded = report.get("not_folded") or []
    if unfolded:
        out.append(
            f"[yellow]{len(unfolded)} node(s) were written to "
            f"{escape(str(report.get('bank')))} that the fold-in never sent (written after it, or "
            "dropped by it): they stay "
            "in the old bank, which nothing reads. Re-create one through compat, or ignore "
            "it.[/yellow]"
        )
        for node in unfolded:
            out.append(
                f"  not folded: {escape(str(node.get('name')))} ({node.get('kind')}, "
                f"{escape(str(node.get('old_id')))})"
            )
    for rename in report.get("renames") or []:
        out.append(
            f"  rename: {escape(str(rename.get('from')))} -> {escape(str(rename.get('to')))}"
        )
    if owner:
        owners = ", ".join(f"{escape(k)} ({v})" for k, v in (owner.get("owners") or {}).items())
        given = "--owner" if owner.get("given") else "from each page's agent: tag"
        out.append(f"owner ({given}): {owners or '-'}")
        if owner.get("pseudo_owned"):
            out.append(
                f"[yellow]{len(owner['pseudo_owned'])} page(s) go to a pseudo-owner no client "
                "holds: compat shows them, but only their owner may change them. Pass --owner "
                "with the plugin's client.[/yellow]"
            )
    if "content" in report:
        content = report["content"]
        if content.get("kept"):
            out.append(
                f"content kept; {keep.get('old_links', 0)} [[page:<old id>]] link(s) stay as "
                "they are and resolve through the page map"
            )
        else:
            out.append(f"content cleared: {content.get('cleared')} page(s)")
    if keep:
        verdict = "allowed" if keep.get("allowed") else "refused"
        out.append(f"--keep-content would be {verdict}: {escape(str(keep.get('why')))}")
    page_map = report.get("page_map") or {}
    if page_map:
        out.append(
            f"page map {escape(str(page_map.get('path')))}: {page_map.get('rows')} row(s), "
            f"{page_map.get('to_write')} to write"
        )
    return out


@app.command("rehome")
def rehome_cmd(
    bank: str = typer.Option(..., "--bank", help="The old bank the pages came from."),
    project: str = typer.Option(..., "--project", help="The project whose folder they go to."),
    owner: str | None = typer.Option(
        None,
        "--owner",
        help="CLIENT: file every page under this bridge client. Without it, each page's "
        "agent: tag, else the bank's harness, resolved as the fold-in resolved it.",
    ),
    keep_content: bool = typer.Option(
        False,
        "--keep-content",
        help="Keep what the pages hold instead of emptying them. Refused when the bank holds "
        "private facts. Kept content was written under the old, unscoped trigger.",
    ),
    apply: bool = typer.Option(
        False, "--apply/--dry-run", help="Do it (root). Default: say what it would do."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a panel."),
) -> None:
    """Move a folded bank's unplaced knowledge pages into a project folder.

    For a coding bank the fold-in left at the root of the platform bank
    because its pages name no project. Stamps each page as compat stamps
    one, empties it unless --keep-content, moves it under project:<slug>,
    and maps its old id. A re-run finishes a stopped one and does nothing
    after a complete one.
    """
    kwargs = {"bank": bank, "project": project, "owner": owner, "keep_content": keep_content}
    try:
        report = _drive(apply, **kwargs)
    except FoldInRefused as exc:
        payload = {"status": "refused", "error": exc.message, **exc.plan}
        if json_out:
            typer.echo(jsonlib.dumps(payload, indent=2, sort_keys=True, default=str))
        else:
            body = [f"[red]{escape(exc.message)}[/red]", *_lines(exc.plan)]
            console.print(Panel("\n".join(body), border_style="red"))
        raise typer.Exit(1) from exc
    except FoldInError as exc:
        payload = {"status": "failed", "error": exc.message, "details": exc.details}
        if json_out:
            typer.echo(jsonlib.dumps(payload, indent=2, sort_keys=True, default=str))
        else:
            console.print(
                Panel(
                    f"[red]{escape(exc.message)}[/red]\nRun the same command again: it "
                    "carries on from where this one stopped.",
                    border_style="red",
                )
            )
        raise typer.Exit(1) from exc
    except (KeyboardInterrupt, asyncio.CancelledError):
        _progress.print("[red]interrupted[/red]: run the same command again to finish")
        raise typer.Exit(130) from None

    refusals = report.get("refusals") or []
    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        lines = [f"[red]refused ({r['code']}):[/red] {escape(r['message'])}" for r in refusals]
        lines += _lines(report)
        if report.get("applied"):
            head = (
                "[green]Done; every page is in its folder with its scope.[/green]"
                if report.get("changed")
                else "[green]Nothing to do: every page is in place already.[/green]"
            )
        elif refusals:
            head = "[red]Plan only, and it would be refused.[/red]"
        elif not report.get("changes"):
            head = "[green]Nothing to do: every page is in place already.[/green]"
        else:
            head = (
                f"[dim]Plan only, nothing was changed. Re-run as root with --apply: sudo "
                f"{COMMAND} --bank {escape(bank)} --project {escape(project)} --apply[/dim]"
            )
        console.print(
            Panel(
                "\n".join([head, *lines]),
                title=f"memory · knowledge rehome · {escape(bank)}",
                border_style="red" if refusals else "green",
            )
        )
    if refusals:
        raise typer.Exit(1)


__all__ = ["app", "rehome_cmd"]
