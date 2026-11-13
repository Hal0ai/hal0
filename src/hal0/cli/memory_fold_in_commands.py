"""``hal0 memory migrate fold-in`` — fold the old banks into the two banks (ADR-0058 7a).

Like ``hal0 memory embedder switch``, not a thin client of hal0-api: it runs
in this process, on the box, as root for ``--apply`` and ``--resume``, and
talks to the engine directly. The work is :mod:`hal0.memory.fold_in`; this
module parses the flags and prints.

A dry run by default: it reads every bank and says what folding it would do,
and writes nothing. ``--status`` reads only the files on disk. Nothing here
asks a question, so every path can be driven from a script.
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

from hal0.memory.engine_maintenance import COUNT_KEYS
from hal0.memory.fold_in import (
    FoldIn,
    FoldInError,
    FoldInRefused,
    interrupted_fold_in,
)
from hal0.memory.write_gate import read_gate

console = Console()
# Progress goes to stderr so --json's stdout stays one JSON document.
_progress = Console(stderr=True)

_COMMAND = "hal0 memory migrate fold-in"

#: Live-verification aid, not an operator feature: the process kills itself
#: when the run reaches the named point (``hal0.memory.fold_in.HOOK_POINTS``),
#: and only while ``HAL0_TEST_HOOKS=1`` is set as well.
KILL_AT_ENV = "HAL0_FOLD_IN_KILL_AT"
TEST_HOOKS_ENV = "HAL0_TEST_HOOKS"


def _client() -> Any:
    from hal0.memory.hindsight_client import HindsightRestClient

    return HindsightRestClient.from_env()


def _kill_hook() -> Callable[[str], None] | None:
    point = os.environ.get(KILL_AT_ENV)
    if not point or os.environ.get(TEST_HOOKS_ENV) != "1":
        return None

    def hook(reached: str) -> None:
        if reached == point:
            _progress.print(f"[red]{KILL_AT_ENV}={point}: killing this process[/red]")
            os.kill(os.getpid(), signal.SIGKILL)

    return hook


def _pairs(values: list[str], flag: str) -> dict[str, str]:
    """``KEY=VALUE`` options as a dict; a malformed one is a usage error."""
    out: dict[str, str] = {}
    for raw in values:
        key, sep, value = raw.partition("=")
        if not sep or not key.strip() or not value.strip():
            _progress.print(f"[red]error:[/red] {flag} takes KEY=VALUE, not {raw!r}")
            raise typer.Exit(2)
        out[key.strip()] = value.strip()
    return out


def _drive(work: Callable[[FoldIn], Awaitable[Any] | Any], **kwargs: Any) -> Any:
    """Run ``work`` on a fresh event loop; SIGTERM stops it like Ctrl-C."""

    async def main() -> Any:
        client = _client()
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        if task is not None:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            result = work(FoldIn(client, hook=_kill_hook(), **kwargs))
            return await result if asyncio.iscoroutine(result) else result
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()

    return asyncio.run(main())


def _bank_table(title: str, rows: list[dict[str, Any]]) -> Table:
    t = Table(title=title)
    t.add_column("bank")
    t.add_column("class")
    for key in COUNT_KEYS:
        t.add_column(key.replace("_", " "), justify="right")
    t.add_column("private facts", justify="right")
    t.add_column("owner-less", justify="right")
    t.add_column("action")
    for row in rows:
        counts = row.get("counts") or {}
        fold = row.get("fold") or {}
        t.add_row(
            escape(str(row.get("bank"))),
            escape(str(row.get("class"))),
            *(str(counts.get(k, "")) for k in COUNT_KEYS),
            str(fold.get("private_facts", "")),
            str(fold.get("ownerless_facts", "")),
            escape(str(row.get("action")) + (f": {row['reason']}" if row.get("reason") else "")),
        )
    return t


def _owner_table(rows: list[dict[str, Any]]) -> Table | None:
    t = Table(title="who the folded facts are filed under")
    for col in ("bank", "wrote it", "owner", "how", "side", "facts"):
        t.add_column(col, justify="right" if col == "facts" else "left")
    any_row = False
    for row in rows:
        for candidate, info in sorted(((row.get("fold") or {}).get("owners") or {}).items()):
            any_row = True
            t.add_row(
                escape(str(row.get("bank"))),
                escape(candidate),
                escape(str(info.get("owner"))),
                escape(str(info.get("source"))),
                escape(str(info.get("part"))),
                str(info.get("facts")),
            )
    return t if any_row else None


def _project_table(rows: list[dict[str, Any]]) -> Table | None:
    t = Table(title="projects the folded facts are tagged with")
    t.add_column("bank")
    t.add_column("project")
    t.add_column("facts", justify="right")
    any_row = False
    for row in rows:
        for slug, n in sorted(((row.get("fold") or {}).get("projects") or {}).items()):
            any_row = True
            t.add_row(escape(str(row.get("bank"))), escape(slug), str(n))
    return t if any_row else None


def _print_plan(plan: dict[str, Any], *, applied: bool) -> None:
    rows = plan.get("banks") or []
    if rows:
        console.print(_bank_table("memory · fold-in" + ("" if applied else " (dry run)"), rows))
        for table in (_owner_table(rows), _project_table(rows)):
            if table is not None:
                console.print(table)
    lines: list[str] = []
    layout = plan.get("layout") or {}
    targets = plan.get("targets") or {}
    if layout:
        lines.append(
            f"layout: {escape(str(layout.get('layout')))}"
            + (
                f" (platform bank {escape(str(layout.get('platform_bank')))})"
                if layout.get("platform_bank")
                else ""
            )
        )
    if targets:
        lines.append(
            f"into: {escape(str(targets.get('shared')))} and {escape(str(targets.get('private')))}"
        )
    owners = plan.get("owners") or {}
    if owners:
        lines.append(
            "owners: " + ", ".join(f"{escape(k)} → {escape(v)}" for k, v in sorted(owners.items()))
        )
    if plan.get("estimate_s") is not None:
        lines.append(f"duration: about {max(1, round(plan['estimate_s'] / 60))} min")
    lines.append(
        "[bold]while it runs, memory is out of service:[/bold] the engine is moved to a "
        "private port, writes are refused with 503 memory.writes_paused and recall fails "
        "until the run ends. Nothing is deleted: the old banks stay, read-only once the run "
        "has cut over. --apply is your consent to that"
    )
    for warning in plan.get("warnings") or []:
        lines.append(f"[yellow]note: {escape(str(warning))}[/yellow]")
    refusals = plan.get("refusals") or []
    for refusal in refusals:
        lines.append(f"[red]refused: {escape(str(refusal.get('message')))}[/red]")
    if not refusals and not applied:
        lines.append("[dim]Plan only, nothing was changed. Re-run as root with --apply.[/dim]")
    console.print(Panel("\n".join(lines), border_style="red" if refusals else "dim"))


def _print_status(status: dict[str, Any]) -> None:
    lines: list[str] = []
    journal = status.get("journal")
    if journal:
        lines.append(
            f"journal: run {journal.get('run_id')}, phase [bold]{journal.get('phase')}[/bold], "
            f"started {journal.get('started_at')}"
        )
        lines.append(f"read from: {escape(str(status.get('journal_source')))}")
        lines.append(f"archives: {escape(str(journal.get('archive_dir')))}")
        for bank in journal.get("banks") or []:
            note = f" — {escape(str(bank['note']))}" if bank.get("note") else ""
            lines.append(f"  {escape(str(bank.get('bank')))}: {bank.get('step')}{note}")
        if journal.get("error"):
            lines.append(f"[red]stopped on: {escape(str(journal['error']))}[/red]")
    else:
        lines.append("journal: none")
    if status.get("journal_error"):
        lines.append(f"[red]journal problem: {escape(str(status['journal_error']))}[/red]")
    gate = status.get("gate")
    lines.append(
        f"[yellow]memory writes are paused[/yellow] since {gate.get('since')} "
        f"(reason {gate.get('reason')}, run {gate.get('run_id')})"
        if gate
        else "memory writes: open"
    )
    lines.append(
        "a fold-in is running now" if status.get("lock_held") else "no fold-in process is running"
    )
    if status.get("private_port") is not None:
        lines.append(
            f"[yellow]the engine is isolated on 127.0.0.1:{status['private_port']}[/yellow]"
        )
    record = status.get("record") or {}
    for row in record.get("left") or []:
        lines.append(
            f"[yellow]left: {escape(str(row.get('bank')))}[/yellow] — "
            f"{escape(str(row.get('note')))}"
        )
    lines.append(f"[bold]{escape(str(status.get('summary')))}[/bold]")
    console.print(Panel("\n".join(lines), title="memory · fold-in · status"))


def _fail(exc: FoldInError, json_out: bool, *, code: int = 1) -> None:
    left = interrupted_fold_in()
    gate = read_gate()
    payload = {
        "status": "failed",
        "error": exc.message,
        "bank": exc.bank,
        "details": exc.details,
        "resume_needed": left is not None,
        "writes_paused": gate is not None,
        "archive_dir": (left or {}).get("archive_dir") or (gate or {}).get("archive_dir"),
    }
    if json_out:
        typer.echo(jsonlib.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        if left is not None or gate is not None:
            after = (
                f"Memory writes stay paused. `{_COMMAND} --status` shows where the run stands; "
                f"once the cause is fixed, run `{_COMMAND} --resume`."
            )
        else:
            after = (
                "No bank was changed: the engine is back at its normal address and memory "
                "writes are open."
            )
        console.print(Panel(f"[red]{escape(exc.message)}[/red]\n{after}", border_style="red"))
    raise typer.Exit(code)


def fold_in_cmd(
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="Do the fold-in. Default is --dry-run: read every bank and print the plan.",
    ),
    resume: bool = typer.Option(
        False, "--resume", help="Carry an interrupted fold-in on from its journal."
    ),
    status: bool = typer.Option(
        False, "--status", help="Show where a fold-in stands, from the files on disk."
    ),
    bank: list[str] = typer.Option(
        [],
        "--bank",
        help="Fold only this bank (repeatable): retries a bank an earlier run left over.",
    ),
    owner: list[str] = typer.Option(
        [],
        "--owner",
        help="HARNESS=CLIENT (repeatable): the bridge client an old coding bank's facts from "
        "HARNESS are filed under.",
    ),
    mapping: list[str] = typer.Option(
        [],
        "--map",
        help="BANK=HARNESS (repeatable): fold a bank the fold-in does not know as a coding "
        "bank written by HARNESS, its project taken from each fact's tags.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of tables."),
) -> None:
    """Fold the old memory banks into hal0:shared and hal0:private.

    Moves coding-agent::<repo>, pi-coder, Hal0::Agents, the old platform bank
    and any private__<agent> bank into the two banks, one bank at a time,
    tagging by project and owner and splitting private facts out. A bank
    whose counts do not match afterwards is left as it was and the run goes
    on. Nothing is deleted. Memory is out of service while it runs. Run it on
    the box, as root, for --apply and --resume.
    """
    chosen = [n for n, on in (("--resume", resume), ("--status", status)) if on]
    if len(chosen) > 1 or (chosen and apply):
        _progress.print("[red]error:[/red] --apply, --resume and --status are one at a time")
        raise typer.Exit(2)
    owners = {k.lower(): v for k, v in _pairs(owner, "--owner").items()}
    maps = _pairs(mapping, "--map")
    kwargs = {"owners": owners, "maps": maps, "only": bank}

    def say(message: str) -> None:
        _progress.print(f"[dim]{escape(message)}[/dim]")

    try:
        if status:
            report = _drive(lambda f: f.status(), **kwargs)
            if json_out:
                typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True, default=str))
            else:
                _print_status(report)
            return
        if resume:
            result = _drive(lambda f: f.resume(), progress=say, **kwargs)
        elif apply:
            result = _drive(lambda f: f.run(), progress=say, **kwargs)
        else:
            plan = _drive(lambda f: f.preflight(), **kwargs)
            if json_out:
                typer.echo(
                    jsonlib.dumps({"applied": False, **plan}, indent=2, sort_keys=True, default=str)
                )
            else:
                _print_plan(plan, applied=False)
            if plan.get("refusals"):
                raise typer.Exit(1)
            return
    except FoldInRefused as exc:
        if json_out:
            typer.echo(
                jsonlib.dumps(
                    {"status": "refused", "error": exc.message, **exc.plan},
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )
        elif exc.plan:
            _print_plan(exc.plan, applied=True)
        else:
            console.print(Panel(f"[red]{escape(exc.message)}[/red]", border_style="red"))
        raise typer.Exit(1) from exc
    except FoldInError as exc:
        _fail(exc, json_out)
        return
    except (KeyboardInterrupt, asyncio.CancelledError):
        _fail(FoldInError("interrupted"), json_out, code=130)
        return
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(FoldInError(f"{type(exc).__name__}: {exc}"), json_out)
        return

    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True, default=str))
        return
    folded, left = result.get("folded") or [], result.get("left") or []
    body = [
        f"[bold {'yellow' if left else 'green'}]{len(folded)} bank(s) folded into "
        f"{escape(str((result.get('targets') or {}).get('shared')))} and "
        f"{escape(str((result.get('targets') or {}).get('private')))}[/]",
        "The store is on the two banks; memory writes are open again. The old banks are kept.",
    ]
    for entry in result.get("banks") or []:
        if entry.get("step") == "left":
            body.append(
                f"[yellow]left: {escape(str(entry.get('bank')))}[/yellow] — "
                f"{escape(str(entry.get('note')))}"
            )
    if left:
        body.append(
            "Retry a left bank alone with "
            + " ".join(f"--bank {escape(b)}" for b in left)
            + f" (`sudo {_COMMAND} --apply --bank <id>`)."
        )
    body.append(f"Archives: {escape(str(result.get('archive_dir')))}")
    console.print(Panel("\n".join(body), border_style="yellow" if left else "green"))


__all__ = ["fold_in_cmd"]
