"""hal0 memory subcommands — graph-extraction gate.

Mirrors the slot / model CLI shape: a thin HTTP client to the local
hal0 API. The ``graph`` sub-sub-app maps 1:1 to the routes in
:mod:`hal0.api.routes.memory`:

    hal0 memory status                                  → GET  /api/status (memory_enabled)
    hal0 memory enable                                  → PUT  /api/settings (memory.enabled=true)
    hal0 memory disable                                 → PUT  /api/settings (memory.enabled=false)
    hal0 memory graph status                            → GET  /api/memory/graph/status
    hal0 memory graph enable [--slot …]                 → PUT  /api/memory/graph (enabled=true …)
    hal0 memory graph disable                           → PUT  /api/memory/graph (enabled=false)
    hal0 memory graph check                             → POST /api/memory/graph/throughput

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
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    api_post,
    api_put,
    die,
)
from hal0.cli.memory_bank_commands import app as bank_app
from hal0.cli.memory_migrate_commands import app as migrate_app
from hal0.cli.memory_mm_commands import app as mm_app
from hal0.cli.memory_ops_commands import app as ops_app
from hal0.cli.memory_recall_commands import recall_cmd

app = typer.Typer(help="Manage hal0 memory (Hindsight engine).")
console = Console()

# ``graph`` sub-sub-app so ``hal0 memory graph --help`` renders cleanly
# alongside ``hal0 memory --help``. Same pattern as ``hal0 agent approvals``.
graph_app = typer.Typer(help="Graph-extraction settings (ADR-0023).")
app.add_typer(graph_app, name="graph")

# Beefier Hindsight bank-admin CLI: bank/ops/mm sub-apps, a debug ``recall``,
# and ``migrate`` (``migrate unify``). Implementations live in
# hal0/cli/memory_{bank,ops,mm,recall,migrate}_commands.py so this file only
# needs this import block + the add_typer/command calls below.
app.add_typer(bank_app, name="bank")
app.add_typer(ops_app, name="ops")
app.add_typer(mm_app, name="mm")
app.command("recall")(recall_cmd)

#: ``PUT /api/memory/graph`` can restart hindsight-api (daemon-reload + restart,
#: each bounded at 60 s server-side) and then run the extraction throughput probe
#: (#2353: <= 30 s plus a 2 s metrics read). The shared 10 s default timed out
#: on any real slot change.
_GRAPH_PUT_TIMEOUT_S = 160.0
#: ``POST /api/memory/graph/throughput``: the 30 s probe cap plus the metrics read.
_THROUGHPUT_POST_TIMEOUT_S = 45.0


def _throughput_row(est: Any) -> str | None:
    """One rich-markup line for an extraction throughput estimate (#2353)."""
    if not isinstance(est, dict) or not est.get("message"):
        return None
    color = {"ok": "green", "too_slow": "yellow"}.get(str(est.get("verdict")), "dim")
    # escape(): the message names ``[memory.graph].llm_timeout_s``, which rich
    # would otherwise read as a markup tag.
    return f"[{color}]{escape(str(est['message']))}[/{color}]"


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
    throughput = _cached_throughput() if s.get("memory_enabled") else None
    if json_out:
        typer.echo(
            jsonlib.dumps(
                {
                    "memory_enabled": s.get("memory_enabled"),
                    "memory_degraded": s.get("memory_degraded"),
                    "memory_write_degraded": s.get("memory_write_degraded"),
                    "memory_write_health": s.get("memory_write_health"),
                    "extraction_throughput": throughput,
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
        # #1301: ``degraded`` now covers BOTH shapes — the boot fallback (a
        # volatile in-memory store) and a Hindsight daemon that died after
        # boot (writes error, recalls come back empty). The old wording named
        # only the first, so a post-boot outage printed a claim about the
        # provider that was simply false. One line that is true of both.
        t.add_row(
            "Provider",
            "[yellow]degraded — Hindsight unreachable (memory is volatile or failing)[/yellow]",
        )
    elif enabled:
        t.add_row("Provider", "[green]durable[/green]")
    if enabled:
        # #1420: the two rows above cannot distinguish a box where every retain
        # is being accepted and then dying in extraction from a healthy one —
        # both print ON / durable. This row is that distinction, plus the
        # engine's own operation counters so the size of the backlog is visible
        # without shelling into the daemon.
        _add_write_rows(t, s)
        row = _throughput_row(throughput)
        if row:
            t.add_row("Extraction speed", row)
    console.print(Panel(t, title="memory · status", border_style="dim"))


def _cached_throughput() -> dict[str, Any] | None:
    """The API's last extraction throughput estimate (#2353), without probing.

    Best-effort: an older API without the route, or a key without admin
    scope, just leaves the row out.
    """
    try:
        payload = api_get("/api/memory/graph/throughput")
    except CliApiError:
        return None
    est = payload.get("throughput") if isinstance(payload, dict) else None
    return est if isinstance(est, dict) else None


def _add_write_rows(t: Table, s: dict) -> None:
    """Render the retain-pipeline rows for ``hal0 memory status`` (#1420)."""
    health = s.get("memory_write_health")
    write_degraded = s.get("memory_write_degraded")
    if write_degraded is None:
        # No retain pipeline to report on (volatile fallback / other provider).
        return
    reason = (health or {}).get("reason")
    if (health or {}).get("waiting_on") == "chat_model":
        # #1792: a fresh install ships every llm slot model-less, so every
        # retain's extraction call 404s until the operator loads one — that
        # is expected, self-healing (auto-retry kicks in once a model is
        # bound), and not "memory is broken".
        t.add_row(
            "Writes",
            "[yellow]waiting[/yellow] — no chat model loaded: retains will "
            "process once one is available",
        )
    elif reason == "no_write_ever_landed":
        failed = int(((health or {}).get("operations") or {}).get("failed") or 0)
        t.add_row(
            "Writes",
            f"[red]FAILING[/red] — No write has landed: {failed} failed, 0 facts stored. "
            "Run `hal0 memory ops retry --all-failed` or check the extraction slot.",
        )
    elif write_degraded is True:
        detail = (health or {}).get("last_error") or reason or "unknown"
        t.add_row("Writes", f"[red]FAILING[/red] — {reason}: {detail}")
    elif reason == "unknown":
        t.add_row("Writes", "[yellow]unknown — the engine did not report operation counts[/yellow]")
    else:
        t.add_row("Writes", "[green]landing[/green]")
    ops = (health or {}).get("operations")
    if isinstance(ops, dict):
        t.add_row(
            "Operations",
            "  ".join(f"{k}={ops.get(k, 0)}" for k in ("failed", "pending", "processing")),
        )


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
    """Show the live graph-extraction status (enabled / extraction slot / counters)."""
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
    slot = s.get("extraction_slot", "—")
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
    # #2919: what hal0/memory resolves to, and (#2918) what the engine really runs.
    target = s.get("target") or {}
    if target.get("model"):
        kind = "upstream model" if target.get("kind") == "upstream" else "local slot"
        t.add_row(
            "Engine target",
            f"[bold]{target['model']}[/bold] [dim]({kind} via {target.get('alias', 'hal0/memory')})[/dim]",
        )
    models = s.get("available_models") or []
    if models:
        ids = [str(m.get("id")) for m in models if isinstance(m, dict)]
        shown = ", ".join(ids[:12]) + (" …" if len(ids) > 12 else "")
        t.add_row("Upstream models", shown)
    engine = s.get("engine_llm") or {}
    if engine.get("model"):
        running = f"[bold]{engine['model']}[/bold] [dim]via {engine.get('provider') or '?'}"
        if engine.get("base_url"):
            running += f" @ {engine['base_url']}"
        running += "[/dim]"
        if engine.get("overridden"):
            keys = ", ".join(engine.get("override_keys") or [])
            running += (
                f"\n[red]overridden by {engine.get('override_file')} ({keys}) — "
                "hal0's engine target is not in effect until those lines are removed "
                "and hindsight-api is restarted[/red]"
            )
        elif engine["model"] != (target.get("alias") or "hal0/memory"):
            # No drop-in written yet (or a pre-#2919 one): the unit still
            # pins a slot id, so the picker's target is not what runs.
            running += (
                f"\n[red]engine still pinned to {engine['model']} — run "
                "`hal0 memory graph enable` once to repoint it to hal0/memory "
                "(writes the drop-in and restarts hindsight-api)[/red]"
            )
        t.add_row("Engine running", running)
    t.add_row("Builds OK", str(s.get("builds_ok", 0)))
    t.add_row("Errors", str(s.get("errors", 0)))
    t.add_row("In-flight", str(s.get("in_flight", 0)))
    last = s.get("last_built_at") or "[dim]never[/dim]"
    t.add_row("Last build", str(last))
    if s.get("last_error"):
        t.add_row("Last error", f"[red]{s['last_error']}[/red]")
    speed = _throughput_row(s.get("throughput"))
    if speed:
        t.add_row("Extraction speed", speed)
    console.print(Panel(t, title="memory · graph", border_style="dim"))


# ── ``hal0 memory graph enable`` ──────────────────────────────────────────────


@graph_app.command("enable")
def graph_enable_cmd(
    slot: str | None = typer.Option(
        None,
        "--slot",
        help="Local llm slot the memory engine runs on (e.g. 'utility'). "
        "Must be an enabled type=llm slot; the server validates against the live "
        "slot set. Omit to keep the current slot.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Run the memory engine on an upstream model instead of a local slot — "
        "an id hal0's /v1 already routes (see `hal0 memory graph status` → "
        "'Upstream models', e.g. 'MiniMax-M2.5' or 'minimax/minimax-m3'). "
        "Applies at once, no engine restart; the slot is kept as the way back.",
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help="Run the memory engine on the local extraction slot again (clears --model).",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw JSON response instead of a panel."
    ),
) -> None:
    """Turn graph extraction ON (optionally repointing the engine's model)."""
    if model is not None and local:
        die("--model and --local are exclusive")
        return
    if model is not None and slot is not None:
        die("--slot and --model are exclusive: --model keeps the configured slot as the way back")
        return
    payload: dict[str, Any] = {"enabled": True}
    if slot is not None:
        # A slot choice also clears any upstream model, as the dashboard
        # picker does — otherwise the engine would stay on the upstream.
        payload["extraction_slot"] = slot
        payload["extraction_model"] = None
    if model is not None:
        payload["extraction_model"] = model
    if local:
        payload["extraction_model"] = None

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_put("/api/memory/graph", json=payload, timeout=_GRAPH_PUT_TIMEOUT_S)
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
    # #2353: a slot too slow for one extraction is a warning, never a refusal —
    # the gate is on either way, so say so in the same panel.
    speed = _throughput_row(result.get("throughput"))
    speed_line = f"\n{speed}" if speed else ""
    too_slow = bool(result.get("warnings"))
    target = result.get("target") or {}
    target_line = ""
    if target.get("model"):
        kind = "upstream model" if target.get("kind") == "upstream" else "local slot"
        target_line = f"\nengine target = [bold]{target['model']}[/bold] [dim]({kind})[/dim]"
    console.print(
        Panel(
            f"[bold green]Graph extraction enabled[/bold green]\n"
            f"extraction slot = [bold]{result.get('extraction_slot')}[/bold]"
            f"{target_line}{prop_line}{speed_line}",
            border_style="yellow" if too_slow else "green",
        )
    )


# ── ``hal0 memory graph check`` ───────────────────────────────────────────────


@graph_app.command("check")
def graph_check_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw JSON estimate instead of a panel."
    ),
) -> None:
    """Measure whether the extraction slot finishes one extraction inside llm_timeout_s.

    Reads the slot's llama-server metrics, or runs one completion of at most
    512 prompt + 32 decode tokens (capped at 30 s). A slow slot is reported,
    not refused: this always exits 0 once the API answered.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_post("/api/memory/graph/throughput", timeout=_THROUGHPUT_POST_TIMEOUT_S)
    except CliApiError as exc:
        die(str(exc))
        return
    est = result.get("throughput") if isinstance(result, dict) else None
    if json_out:
        typer.echo(jsonlib.dumps(est, indent=2, sort_keys=True))
        return
    row = _throughput_row(est) or "[dim]no estimate returned[/dim]"
    verdict = est.get("verdict") if isinstance(est, dict) else None
    console.print(
        Panel(
            row,
            title="memory · extraction speed",
            border_style={"ok": "green", "too_slow": "yellow"}.get(str(verdict), "dim"),
        )
    )


# ── ``hal0 memory graph disable`` ─────────────────────────────────────────────


@graph_app.command("disable")
def graph_disable_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw JSON response instead of a panel."
    ),
) -> None:
    """Turn graph extraction OFF; cancels any in-flight build."""
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
    # #1471: this used to claim "In-flight builds cancelled." — it never
    # cancelled anything. On Hindsight (the only shipping engine) extraction
    # runs inside the daemon and this flag is a hal0-side label; the daemon
    # keeps extracting either way, which is why /graph/status keeps reporting
    # real, rising operation counters after a disable. Say that, rather than
    # letting an operator believe they just stopped GPU work.
    console.print(
        Panel(
            "[bold]Graph extraction marked disabled[/bold]\n"
            "[dim]Labeling only on the Hindsight engine — it builds its graph "
            "natively and keeps extracting. This flag controls how hal0 reports "
            "and routes, not whether the daemon runs.[/dim]",
            border_style="yellow",
        )
    )


# ── ``hal0 memory migrate`` / ``migrate unify`` ────────────────────────────────
app.add_typer(migrate_app, name="migrate")


__all__ = ["app", "graph_app"]
