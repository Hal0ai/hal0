"""``hal0 memory embedder switch`` — move the memory store to another embedder (#3518).

Unlike the rest of ``hal0 memory``, this is not a thin client of hal0-api. It
runs in this process, on the box, as root or the ``hal0`` service account:
it talks to the engine directly (its own imports and deletes must not meet
the write pause it sets), rewrites the engine's managed drop-in, restarts the
engine and renames banks. The work is :mod:`hal0.memory.embedder_switch`;
this module parses the flags and prints.

A dry run by default, like ``hal0 memory migrate``: ``--apply`` does it.
``--status`` and the dry run read only. Nothing here asks a question, so
every path can be driven from a script.
"""

from __future__ import annotations

import asyncio
import contextlib
import json as jsonlib
import os
import signal
from collections.abc import Awaitable, Callable
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from hal0.memory.embedder_record import Embedder, describe_embedder
from hal0.memory.embedder_switch import (
    COUNT_KEYS,
    RESTORE_FACTS_PER_S,
    EmbedderSwitch,
    SwitchError,
    SwitchRefused,
    interrupted_switch,
)
from hal0.memory.write_gate import read_gate

app = typer.Typer(
    help="The embedder the memory store was built with.",
    invoke_without_command=True,
)
console = Console()
# Progress goes to stderr so --json's stdout stays one JSON document.
_progress = Console(stderr=True)

_RESUME_HINT = "hal0 memory embedder switch --resume"
_STATUS_HINT = "hal0 memory embedder switch --status"

#: Live-verification aid, not an operator feature: the process kills itself
#: (SIGKILL, so nothing is cleaned up) when the run reaches the named point
#: (``hal0.memory.embedder_switch.HOOK_POINTS``, e.g. ``deleted:shared``).
KILL_AT_ENV = "HAL0_EMBEDDER_SWITCH_KILL_AT"


@app.callback()
def embedder_default(ctx: typer.Context) -> None:
    """The embedder the memory store was built with (``switch`` changes it)."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def _client() -> Any:
    """The engine's own REST client, resolved as hal0-api resolves it."""
    from hal0.memory.hindsight_client import HindsightRestClient

    return HindsightRestClient.from_env()


def _kill_hook() -> Callable[[str], None] | None:
    point = os.environ.get(KILL_AT_ENV)
    if not point:
        return None

    def hook(reached: str) -> None:
        if reached == point:
            _progress.print(f"[red]{KILL_AT_ENV}={point}: killing this process[/red]")
            os.kill(os.getpid(), signal.SIGKILL)

    return hook


def _make_switch(client: Any, **kwargs: Any) -> EmbedderSwitch:
    return EmbedderSwitch(client, hook=_kill_hook(), **kwargs)


def _drive(work: Callable[[EmbedderSwitch], Awaitable[Any] | Any], **kwargs: Any) -> Any:
    """Run ``work`` on a fresh event loop, turning SIGTERM into the same clean stop as Ctrl-C.

    Either signal cancels the run where it stands: before the swap that
    undoes it and lifts the write pause; inside the swap it stops after the
    step in hand and leaves the journal for ``--resume``.
    """

    async def main() -> Any:
        client = _client()
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        if task is not None:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            result = work(_make_switch(client, **kwargs))
            return await result if asyncio.iscoroutine(result) else result
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()

    return asyncio.run(main())


def _minutes(seconds: float | int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 90:
        return f"about {max(1, round(seconds))} s"
    return f"about {round(seconds / 60)} min"


def _mib(n: int | None) -> str:
    return "?" if n is None else f"{n // 2**20} MiB"


def _counts_table(title: str, rows: list[dict[str, Any]], extra: str | None = None) -> Table:
    t = Table(title=title)
    t.add_column("bank")
    for key in COUNT_KEYS:
        t.add_column(key.replace("_", " "), justify="right")
    if extra:
        t.add_column(extra)
    for row in rows:
        counts = row.get("counts") or {}
        cells = [escape(str(row.get("bank"))), *(str(counts.get(key, 0)) for key in COUNT_KEYS)]
        if extra:
            cells.append(escape(str(row.get("_extra", ""))))
        t.add_row(*cells)
    return t


def _print_plan(plan: dict[str, Any], *, applied: bool) -> None:
    target = plan.get("target") or {}
    recorded = plan.get("recorded") or {}
    rows = []
    for bank in plan.get("banks") or []:
        queued = int(bank.get("pending") or 0) + int(bank.get("processing") or 0)
        rows.append({**bank, "_extra": queued})
    if rows:
        title = "memory · embedder switch" + ("" if applied else " (dry run)")
        console.print(_counts_table(title, rows, "queued"))

    lines: list[str] = []
    if recorded and target:
        was = describe_embedder(recorded.get("provider"), recorded.get("model"))
        if plan.get("mixed"):
            was += " [yellow](mixed: may hold vectors from two embedders)[/yellow]"
        now = describe_embedder(target.get("provider"), target.get("model"))
        lines.append(f"{was}  →  [bold]{now}[/bold]")
    engine = plan.get("engine") or {}
    if engine.get("reachable"):
        lines.append(f"engine: hindsight-api {engine.get('version') or '?'}")
    if plan.get("estimate_s") is not None:
        lines.append(
            f"rebuild: {_minutes(plan['estimate_s'])} at the measured {RESTORE_FACTS_PER_S:g} "
            "facts/s on CPU; the exports and an engine restart come on top"
        )
    disk = plan.get("disk")
    if disk:
        lines.append(
            f"disk: {_mib(disk.get('store_free_bytes'))} free where the engine keeps its data, "
            f"{_mib(disk.get('store_needed_bytes'))} wanted; archives go under "
            f"{escape(str(disk.get('archive_root')))}"
        )
    lines.append(
        "while it runs: memory writes are refused with 503 memory.writes_paused, from before "
        "the first export; recall keeps working except while the engine restarts"
    )
    for warning in plan.get("warnings") or []:
        lines.append(f"[yellow]note: {escape(str(warning))}[/yellow]")
    refusals = plan.get("refusals") or []
    for refusal in refusals:
        lines.append(f"[red]refused: {escape(str(refusal.get('message')))}[/red]")
    if not refusals and not applied:
        lines.append("[dim]Plan only, nothing was changed. Re-run with --apply.[/dim]")
    console.print(Panel("\n".join(lines), border_style="red" if refusals else "dim"))


def _print_status(status: dict[str, Any]) -> None:
    rows = [{**b, "_extra": b.get("role", "")} for b in status.get("banks") or []]
    if rows:
        console.print(_counts_table("memory · embedder switch · banks on the engine", rows, "role"))
    lines: list[str] = []
    journal = status.get("journal")
    if journal:
        target = journal.get("target") or {}
        lines.append(
            f"journal: run {journal.get('run_id')}, phase [bold]{journal.get('phase')}[/bold], "
            f"to {escape(describe_embedder(target.get('provider'), target.get('model')))}, "
            f"started {journal.get('started_at')}"
        )
        lines.append(f"read from: {escape(str(status.get('journal_source')))}")
        lines.append(f"archives: {escape(str(journal.get('archive_dir')))}")
        if journal.get("error"):
            lines.append(f"[red]stopped on: {escape(str(journal['error']))}[/red]")
    else:
        lines.append("journal: none")
    if status.get("journal_error"):
        lines.append(f"[red]journal problem: {escape(str(status['journal_error']))}[/red]")
    gate = status.get("gate")
    if gate:
        lines.append(
            f"[yellow]memory writes are paused[/yellow] since {gate.get('since')} "
            f"(run {gate.get('run_id')}, archives {escape(str(gate.get('archive_dir')))})"
        )
    else:
        lines.append("memory writes: open")
    lines.append(
        "a switch is running now" if status.get("lock_held") else "no switch process is running"
    )
    engine = status.get("engine") or {}
    live = engine.get("live") or {}
    if not engine.get("reachable"):
        lines.append(f"[red]engine: not answering ({escape(str(engine.get('error')))})[/red]")
    elif live:
        lines.append(
            f"engine: up, running {escape(describe_embedder(live.get('provider'), live.get('model')))}"
        )
    for step in status.get("resume") or []:
        lines.append(
            f"  {escape(str(step['bank']))} [dim](step {step['step']})[/dim]: "
            f"{escape(str(step['action']))}"
        )
    lines.append(f"[bold]{escape(str(status.get('summary')))}[/bold]")
    console.print(Panel("\n".join(lines), title="memory · embedder switch · status"))


def _fail(exc: SwitchError, json_out: bool, *, code: int = 1) -> None:
    """Report a failed run and say which of the two end states the box is in."""
    left = interrupted_switch()
    gate = read_gate()
    archive_dir = (left or {}).get("archive_dir") or (gate or {}).get("archive_dir")
    payload = {
        "status": "failed",
        "error": exc.message,
        "bank": exc.bank,
        "details": exc.details,
        "resume_needed": left is not None,
        "writes_paused": gate is not None,
        "archive_dir": archive_dir,
    }
    if json_out:
        typer.echo(jsonlib.dumps(payload, indent=2, sort_keys=True))
    else:
        if left is not None or gate is not None:
            after = (
                "Memory writes stay paused. See where the run stands with "
                f"`{_STATUS_HINT}`; once the cause is fixed, run `{_RESUME_HINT}`."
            )
        else:
            after = (
                "Nothing is left half-done: the original banks are in place, the engine is on "
                "its original embedder and memory writes are open."
            )
        if archive_dir:
            after += f"\nArchives: {escape(str(archive_dir))}"
        console.print(Panel(f"[red]{escape(exc.message)}[/red]\n{after}", border_style="red"))
    raise typer.Exit(code)


@app.command("switch")
def switch_cmd(
    model: str | None = typer.Argument(
        None,
        help="Embedding model to move the store to (e.g. hindsight-ai/hs-embed-1). "
        "Omit with --resume, --status and --clear-gate.",
    ),
    provider: str = typer.Option(
        "local", "--provider", help="Embeddings provider the model belongs to."
    ),
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="Do the switch. Default is --dry-run: run the preflight and print the plan.",
    ),
    allow_pending: bool = typer.Option(
        False,
        "--allow-pending",
        help="Go ahead while the engine still has queued or running operations. What they "
        "have not written when a bank is exported is not carried over.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Finish an interrupted switch from its journal: carry a started swap forward, "
        "or undo a run that stopped before the swap.",
    ),
    status: bool = typer.Option(
        False,
        "--status",
        help="Show where a switch stands: its journal, the write pause, the banks and "
        "temporary banks on the engine, and what --resume would do. Changes nothing.",
    ),
    clear_gate: bool = typer.Option(
        False,
        "--clear-gate",
        help="DANGEROUS: lift the memory write pause by hand. Only for a pause that no "
        "journal explains; refused while any switch is unfinished. Checks no bank.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of tables."),
) -> None:
    """Move the memory store to another embedding model by re-embedding every bank.

    Pauses memory writes, exports every bank, restarts the engine on the new
    model, restores each bank under a temporary id, compares fact,
    observation, mental-model, directive and document counts, and only then
    replaces the originals. Anything that fails before the originals are
    touched is undone. Run it on the box, as root or the hal0 account.
    """
    target = Embedder(provider, model) if model else None
    chosen = [name for name, on in (("--resume", resume), ("--status", status)) if on]
    if clear_gate:
        chosen.append("--clear-gate")
    if len(chosen) > 1 or (chosen and apply):
        _progress.print(
            "[red]error:[/red] --apply, --resume, --status and --clear-gate are one at a time"
        )
        raise typer.Exit(2)
    if not chosen and target is None:
        _progress.print(
            "[red]error:[/red] name the embedding model to switch to, or pass --resume or --status"
        )
        raise typer.Exit(2)

    def say(message: str) -> None:
        _progress.print(f"[dim]{escape(message)}[/dim]")

    try:
        if status:
            report = _drive(lambda s: s.status(), target=target)
            if json_out:
                typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
            else:
                _print_status(report)
            return
        if clear_gate:
            cleared = _drive(lambda s: s.clear_gate(), target=target)
            if json_out:
                typer.echo(jsonlib.dumps(cleared, indent=2, sort_keys=True))
            else:
                gate = cleared.get("gate") or {}
                console.print(
                    Panel(
                        "[bold red]The memory write pause was lifted by hand.[/bold red]\n"
                        f"It was set by run {gate.get('run_id')} at {gate.get('since')}; its "
                        f"archives are in {escape(str(gate.get('archive_dir')))}.\n"
                        "Nothing was checked: if a bank is missing or still under a temporary "
                        f"id, writes now go to the wrong place. `{_STATUS_HINT}` lists the banks.",
                        border_style="red",
                    )
                )
            return
        if resume:
            result = _drive(lambda s: s.resume(), target=target, progress=say)
        elif apply:
            result = _drive(
                lambda s: s.run(), target=target, allow_pending=allow_pending, progress=say
            )
        else:
            plan = _drive(lambda s: s.preflight(), target=target, allow_pending=allow_pending)
            if json_out:
                typer.echo(jsonlib.dumps({"applied": False, **plan}, indent=2, sort_keys=True))
            else:
                _print_plan(plan, applied=False)
            if plan.get("refusals"):
                raise typer.Exit(1)
            return
    except SwitchRefused as exc:
        if json_out:
            typer.echo(
                jsonlib.dumps(
                    {"status": "refused", "error": exc.message, **exc.plan},
                    indent=2,
                    sort_keys=True,
                )
            )
        elif exc.plan:
            _print_plan(exc.plan, applied=True)
        else:
            console.print(Panel(f"[red]{escape(exc.message)}[/red]", border_style="red"))
        raise typer.Exit(1) from exc
    except SwitchError as exc:
        _fail(exc, json_out)
        return
    except (KeyboardInterrupt, asyncio.CancelledError):
        _fail(SwitchError("interrupted"), json_out, code=130)
        return
    except typer.Exit:
        raise
    except Exception as exc:
        # Anything unforeseen (an unreadable hal0.toml, a full disk) is
        # reported the same way, with the same word on where the box stands.
        _fail(SwitchError(f"{type(exc).__name__}: {exc}"), json_out)
        return

    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    if result.get("status") == "rolled_back":
        to = result.get("to") or {}
        console.print(
            Panel(
                "The interrupted switch had not replaced any bank, so it was undone: the store "
                f"is on {escape(describe_embedder(to.get('provider'), to.get('model')))} and "
                "memory writes are open.\n"
                f"Its archives are in {escape(str(result.get('archive_dir')))}.",
                border_style="yellow",
            )
        )
        return
    to = result.get("to") or {}
    mixed = bool(result.get("mixed"))
    body = [
        f"[bold {'yellow' if mixed else 'green'}]The memory store is on "
        f"{escape(describe_embedder(to.get('provider'), to.get('model')))}"
        + (", but is recorded as mixed" if mixed else "")
        + "[/]",
        f"{len(result.get('banks') or [])} bank(s) rebuilt; memory writes are open again.",
        f"The export archives were kept in {escape(str(result.get('archive_dir')))}",
        f"Delete them when you no longer want them: {escape(str(result.get('delete_archives')))}",
    ]
    for warning in result.get("warnings") or []:
        body.append(f"[yellow]note: {escape(str(warning))}[/yellow]")
    console.print(Panel("\n".join(body), border_style="yellow" if mixed else "green"))


__all__ = ["app"]
