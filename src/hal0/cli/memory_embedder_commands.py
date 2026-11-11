"""``hal0 memory embedder switch`` — move the memory store to another embedder (#3518).

Unlike the rest of ``hal0 memory``, this is not a thin client of hal0-api. It
runs in this process, on the box, as root or the ``hal0`` service account:
it talks to the engine directly (its own imports and deletes must not meet
the write pause it sets), rewrites the engine's managed drop-in, restarts the
engine and renames banks. The work is :mod:`hal0.memory.embedder_switch`;
this module parses the flags and prints.

A dry run by default, like ``hal0 memory migrate``: ``--apply`` does it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json as jsonlib
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

app = typer.Typer(
    help="The embedder the memory store was built with.",
    invoke_without_command=True,
)
console = Console()
# Progress goes to stderr so --json's stdout stays one JSON document.
_progress = Console(stderr=True)

_RESUME_HINT = "hal0 memory embedder switch --resume"


@app.callback()
def embedder_default(ctx: typer.Context) -> None:
    """The embedder the memory store was built with (``switch`` changes it)."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def _client() -> Any:
    """The engine's own REST client, resolved as hal0-api resolves it."""
    from hal0.memory.hindsight_client import HindsightRestClient

    return HindsightRestClient.from_env()


def _make_switch(client: Any, **kwargs: Any) -> EmbedderSwitch:
    return EmbedderSwitch(client, **kwargs)


def _drive(work: Callable[[EmbedderSwitch], Awaitable[dict[str, Any]]], **kwargs: Any) -> Any:
    """Run ``work`` on a fresh event loop, turning SIGTERM into the same clean stop as Ctrl-C.

    Either signal cancels the run where it stands: before the swap that
    undoes it and lifts the write pause; inside the swap it stops after the
    step in hand and leaves the journal for ``--resume``.
    """

    async def main() -> dict[str, Any]:
        client = _client()
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        if task is not None:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            return await work(_make_switch(client, **kwargs))
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


def _print_plan(plan: dict[str, Any], *, applied: bool) -> None:
    target = plan.get("target") or {}
    recorded = plan.get("recorded") or {}
    t = Table(title="memory · embedder switch" + ("" if applied else " (dry run)"))
    t.add_column("bank")
    for key in COUNT_KEYS:
        t.add_column(key.replace("_", " "), justify="right")
    t.add_column("queued", justify="right")
    for bank in plan.get("banks") or []:
        counts = bank.get("counts") or {}
        queued = int(bank.get("pending") or 0) + int(bank.get("processing") or 0)
        t.add_row(
            escape(str(bank.get("bank"))),
            *(str(counts.get(key, 0)) for key in COUNT_KEYS),
            f"[yellow]{queued}[/yellow]" if queued else "0",
        )
    if plan.get("banks"):
        console.print(t)

    lines: list[str] = []
    if recorded and target:
        was = describe_embedder(recorded.get("provider"), recorded.get("model"))
        if plan.get("mixed"):
            was += " [yellow](mixed: may hold vectors from two embedders)[/yellow]"
        lines.append(
            f"{was}  →  [bold]{describe_embedder(target.get('provider'), target.get('model'))}[/bold]"
        )
    engine = plan.get("engine") or {}
    if engine.get("reachable"):
        lines.append(f"engine: hindsight-api {engine.get('version') or '?'}")
    if plan.get("estimate_s") is not None:
        lines.append(
            f"rebuild: {_minutes(plan['estimate_s'])} at the measured {RESTORE_FACTS_PER_S:g} "
            "facts/s on CPU; exports and two engine restarts come on top"
        )
    disk = plan.get("disk")
    if disk:
        lines.append(
            f"disk: {_mib(disk.get('free_bytes'))} free, {_mib(disk.get('needed_bytes'))} wanted; "
            f"archives go under {escape(str(disk.get('archive_root')))}"
        )
    lines.append(
        "while it runs: memory writes are refused with 503 memory.writes_paused; "
        "recall keeps working except during the two engine restarts"
    )
    for warning in plan.get("warnings") or []:
        lines.append(f"[yellow]note: {escape(str(warning))}[/yellow]")
    refusals = plan.get("refusals") or []
    for refusal in refusals:
        lines.append(f"[red]refused: {escape(str(refusal.get('message')))}[/red]")
    if not refusals and not applied:
        lines.append("[dim]Plan only, nothing was changed. Re-run with --apply.[/dim]")
    console.print(Panel("\n".join(lines), border_style="red" if refusals else "dim"))


def _fail(exc: SwitchError, json_out: bool, *, code: int = 1) -> None:
    """Report a failed run and say which of the two end states the box is in."""
    left = interrupted_switch()
    payload = {
        "status": "failed",
        "error": exc.message,
        "bank": exc.bank,
        "details": exc.details,
        "resume_needed": left is not None,
    }
    if json_out:
        typer.echo(jsonlib.dumps(payload, indent=2, sort_keys=True))
    else:
        after = (
            f"Memory writes stay paused. Fix the cause, then run `{_RESUME_HINT}`."
            if left is not None
            else "The switch was undone: the original banks were never touched, the engine is "
            "back on its original embedder and memory writes are open."
        )
        console.print(Panel(f"[red]{escape(exc.message)}[/red]\n{after}", border_style="red"))
    raise typer.Exit(code)


@app.command("switch")
def switch_cmd(
    model: str | None = typer.Argument(
        None,
        help="Embedding model to move the store to (e.g. hindsight-ai/hs-embed-1). "
        "Omit with --resume.",
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
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of tables."),
) -> None:
    """Move the memory store to another embedding model by re-embedding every bank.

    Exports every bank, pauses memory writes, restarts the engine on the new
    model, restores each bank under a temporary id, compares fact,
    observation, mental-model, directive and document counts, and only then
    replaces the originals. Anything that fails before the originals are
    touched is undone. Run it on the box, as root or the hal0 account.
    """
    target = Embedder(provider, model) if model else None
    if resume and apply:
        # --resume always acts; --apply beside it is harmless but redundant.
        apply = False
    if not resume and target is None:
        _progress.print("[red]error:[/red] name the embedding model to switch to, or pass --resume")
        raise typer.Exit(2)

    def say(message: str) -> None:
        _progress.print(f"[dim]{escape(message)}[/dim]")

    try:
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
    body = [
        f"[bold green]The memory store is on "
        f"{escape(describe_embedder(to.get('provider'), to.get('model')))}[/bold green]",
        f"{len(result.get('banks') or [])} bank(s) rebuilt; memory writes are open again.",
        f"The export archives were kept in {escape(str(result.get('archive_dir')))}",
        f"Delete them when you no longer want them: {escape(str(result.get('delete_archives')))}",
    ]
    for warning in result.get("warnings") or []:
        body.append(f"[yellow]note: {escape(str(warning))}[/yellow]")
    console.print(Panel("\n".join(body), border_style="green"))


__all__ = ["app"]
