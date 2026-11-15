"""``hal0 memory refresh-policy apply`` — write ``[memory.refresh]`` onto the engine (ADR-0060).

hal0-api does this by itself at boot and on every ``memory.refresh.*`` save
(:mod:`hal0.memory.refresh_runner`). This command is the same reconcile run
by hand, on the box, straight to the engine (like ``hal0 memory knowledge
rehome``): after editing ``hal0.toml`` by hand, on a box whose hal0-api is
down, or to see what would change (``--dry-run``). The work is
:func:`hal0.memory.refresh_policy.apply_refresh_policy`.
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
from rich.table import Table

from hal0.cli._shared import die

app = typer.Typer(
    help="When knowledge pages refresh and the engine consolidates ([memory.refresh])."
)
console = Console()


def _banks(memory_cfg: Any) -> list[str]:
    """This box's platform bank, and its private bank on a two-bank store."""
    from hal0.memory.bank_seed import engine_bank_for

    out: list[str] = []
    for namespace in ("shared", "private:hermes"):
        bank = engine_bank_for(namespace, memory_cfg)
        if bank and bank not in out:
            out.append(bank)
    return out


def _run(banks: list[str], policy: Any, *, dry_run: bool, api: str | None) -> dict[str, Any]:
    from hal0.memory.hindsight_client import HindsightRestClient
    from hal0.memory.refresh_policy import apply_refresh_policy

    async def main() -> dict[str, Any]:
        if api:
            env = HindsightRestClient.from_env()
            client = HindsightRestClient(base_url=api, api_key=env.api_key)
        else:
            client = HindsightRestClient.from_env()
        try:

            async def call(
                method: str,
                path: str,
                *,
                params: dict[str, Any] | None = None,
                json_body: Any | None = None,
            ) -> Any:
                return await client.request_json(
                    method, path, params=params, json_body=json_body, timeout_s=60.0
                )

            report = await apply_refresh_policy(call, banks, policy, dry_run=dry_run)
            return report.to_dict()
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()

    return asyncio.run(main())


def _result(bank: dict[str, Any], dry_run: bool) -> str:
    if bank.get("skipped"):
        return f"[yellow]skipped: {escape(str(bank['skipped']))}[/yellow]"
    verb = "would patch" if dry_run else "patched"
    bits = [
        f"{bank.get('models_seen', 0)} page(s)",
        f"{verb} {len(bank.get('models_patched') or [])}",
    ]
    updates = bank.get("config_updates") or {}
    if updates:
        bits.append(
            ("config would set " if dry_run else "config set ") + ", ".join(sorted(updates))
        )
    else:
        bits.append("config already set")
    out = " · ".join(bits)
    for note in bank.get("notes") or []:
        out += f" [yellow]· {escape(str(note))}[/yellow]"
    errors = bank.get("model_errors") or []
    if errors:
        out += f" [red]· {len(errors)} page error(s)[/red]"
    if bank.get("config_error"):
        out += f" [red]· config: {escape(str(bank['config_error']))}[/red]"
    if bank.get("list_error"):
        out += f" [red]· list: {escape(str(bank['list_error']))}[/red]"
    return out


@app.command("apply")
def refresh_policy_apply_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Read the pages and the bank config and say what would change."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the JSON report instead of a table."),
    api: str | None = typer.Option(
        None,
        "--api",
        help="Memory engine base URL (default: HAL0_HINDSIGHT_URL, else the local engine).",
    ),
) -> None:
    """Write the page refresh trigger and the banks' refresh and consolidation config."""
    try:
        from hal0.config.loader import load_hal0_config

        memory_cfg = load_hal0_config().memory
        banks = _banks(memory_cfg)
    except Exception as exc:
        die(f"cannot read [memory] from hal0.toml to name this box's banks: {exc}")
        return
    if not banks:
        die("this box's memory layout names no bank")
        return
    report = _run(banks, memory_cfg.refresh, dry_run=dry_run, api=api)
    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
    else:
        policy = report["policy"]
        trigger = report["trigger"]
        when = {
            "scheduled": f"on {policy['schedule']} (UTC)",
            "after_consolidation": "after every consolidation",
            "manual": "only by hand",
        }[policy["pages"]]
        console.print(
            f"pages refresh {when}, at most every {policy['min_interval_s']} s; "
            f"consolidation {policy['consolidation']}"
            + (
                f" on {policy['consolidation_schedule']} (UTC)"
                if policy["consolidation"] == "scheduled"
                else ""
            )
        )
        console.print(f"[dim]page trigger: {escape(jsonlib.dumps(trigger, sort_keys=True))}[/dim]")
        if report.get("deferred"):
            console.print(
                Panel(f"[yellow]{escape(report['deferred'])}[/yellow]", border_style="yellow")
            )
            raise typer.Exit(1)
        t = Table(title=f"refresh policy {'plan' if dry_run else 'apply'}")
        t.add_column("bank")
        t.add_column("result")
        for bank in report["banks"]:
            t.add_row(escape(str(bank["bank"])), _result(bank, dry_run))
        console.print(t)
    if not report.get("ok"):
        raise typer.Exit(1)


__all__ = ["app"]
