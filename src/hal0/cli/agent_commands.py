"""hal0 agent subcommands — bundled-agent lifecycle + approval queue.

Mirrors :mod:`hal0.cli.slot_commands` shape (Typer sub-app + thin HTTP
client). The lifecycle subcommands hit the routes in
:mod:`hal0.api.routes.agents`; the ``approvals`` sub-sub-app hits the
MCP-backend's approval queue at ``/api/agent/approvals`` (shape per
ADR-0004 §5 "Pending items").
"""

from __future__ import annotations

import json as jsonlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_delete,
    api_get,
    api_post,
    die,
)
from hal0.cli._shared import _console as _stderr_console
from hal0.mcp.approval_queue import _PRIMARY_TARGET_ARG

app = typer.Typer(help="Manage bundled agents (Phase 8 — pi-coder / Hermes-Agent).")
console = Console()

# Approvals lives as a sub-sub-app so ``hal0 agent approvals list``
# renders correctly in --help. Same pattern as the slot sub-app.
approvals_app = typer.Typer(help="Manage agent approval requests (gated destructives).")
app.add_typer(approvals_app, name="approvals")

# Bootstrap sub-sub-app — ``hal0 agent bootstrap hermes`` runs the
# Hermes provisioning state machine (v0.3 Phase 10 stream).
bootstrap_app = typer.Typer(help="Run bundled-agent bootstrap pipelines (Phase 10).")
app.add_typer(bootstrap_app, name="bootstrap")

# Personas sub-sub-app (PR-3, v0.3) — manages the persona TOML store at
# /var/lib/hal0/.hermes/personas/. Activate writes active.txt and
# triggers a best-effort hot-reload nudge; the API endpoint added in PR-4
# wraps the same persona.activate() helper.
personas_app = typer.Typer(help="Manage Hermes personas (system prompt + tool gating).")
app.add_typer(personas_app, name="personas")


# ── Lifecycle ────────────────────────────────────────────────────────────────


@app.command("install")
def agent_install(
    name: str = typer.Argument(..., help="Bundled agent name (pi-coder | hermes)."),
    switch: bool = typer.Option(
        False,
        "--switch",
        help=(
            "If another agent is already installed, atomically uninstall it before "
            "installing this one (single-pick enforced; ADR-0004 §2)."
        ),
    ),
) -> None:
    """Install a bundled agent.

    Hermes provisions into a hal0-managed venv (toolchain + venv create +
    pip-install hermes-agent + wrapper shim). That multi-minute work can't
    run inside a single HTTP request, so for hermes ``install`` runs the
    bootstrap pipeline locally in the foreground (streaming progress) and
    only consults the daemon at the end to honour ``--switch``. Other
    agents keep the thin API-driven path.
    """
    if name == "hermes":
        _install_hermes(switch=switch)
        return

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    payload: dict[str, object] = {"name": name, "switch": switch}
    try:
        rec = api_post("/api/agents/install", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        Panel(
            f"[bold green]Installed[/bold green] {rec.get('name', name)}  "
            f"[dim](data: {rec.get('data_dir', '?')})[/dim]",
            border_style="green",
        )
    )


def _install_hermes(*, switch: bool) -> None:
    """Foreground provision of Hermes into the hal0-managed venv.

    Three steps, all local + foreground:

    1. **Toolchain** — ``installer/agents/hermes-prereqs.sh`` ensures
       python3 (>=3.11), python3-venv (the clean-Ubuntu trap), python3-pip
       and pipx via the distro helper. Idempotent.
    2. **Provision** — the bootstrap pipeline creates the venv,
       pip-installs hermes-agent into it, installs the
       ``/usr/local/bin/hermes`` shim, and writes the manager seed
       (registers the agent).
    3. **Switch** — best-effort daemon call so ``--switch`` still does the
       atomic single-pick swap; provisioning already registered hermes, so
       a daemon hiccup here doesn't un-provision it.
    """
    import subprocess as _subprocess

    from hal0.agents.hermes_provision import REPO_ROOT_FOR_INSTALLER, bootstrap_cli

    # Bail out cleanly (before the toolchain shell-out) when we can't write the
    # provisioning trees — otherwise the bootstrap crashes several phases deep
    # with a raw PermissionError and leaves half-owned dirs behind.
    _ensure_hermes_writable_or_die()

    prereqs = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes-prereqs.sh"
    if prereqs.is_file():
        console.print("[bold]Ensuring toolchain[/bold] (python · venv · pip · pipx)…")
        rc = _subprocess.run(  # nosec B603 B607 — fixed argv, repo-anchored script
            ["bash", str(prereqs)], check=False
        ).returncode
        if rc != 0:
            die("toolchain prerequisites failed — see the output above.")
            return
    else:
        console.print(
            f"[yellow]prereq script missing at {prereqs}; assuming toolchain present.[/yellow]"
        )

    console.print("[bold]Provisioning Hermes[/bold] → /var/lib/hal0/venvs/hermes …")
    rc = bootstrap_cli(repair=False, dry_run=False, skip_phases=(), verbose=False)
    if rc != 0:
        die(
            "Hermes provisioning failed — inspect `hal0 agent status hermes` / `hal0 agent log hermes`."
        )
        return

    # Bootstrap ran as the installing user (root on a system install) but the
    # agent unit runs as `hal0` and must WRITE $HERMES_HOME at runtime — hand
    # the provisioned trees to the agent user. No-op off-root / no such user.
    _chown_hermes_trees_to_agent_user()

    # Register + honour --switch via the daemon (venv now present → gate passes).
    url = _api_base()
    if not _api_unreachable(url):
        try:
            api_post("/api/agents/install", json={"name": "hermes", "switch": switch})
        except CliApiError as exc:
            console.print(f"[yellow]Provisioned, but daemon register/switch hint:[/yellow] {exc}")

    # Bring the unit up so the agent actually runs (and survives reboot).
    _enable_and_start_hermes_unit()

    console.print(
        Panel(
            "[bold green]Installed[/bold green] hermes  "
            "[dim](managed venv: /var/lib/hal0/venvs/hermes)[/dim]",
            border_style="green",
        )
    )


# The agent unit runs as this user (User= in installer/systemd/hal0-agent@.service).
_AGENT_RUNTIME_USER = "hal0"
# Trees the bootstrap creates as the installing user that the agent must own to
# run: the venv (exec), HERMES_HOME (read+write at runtime), bootstrap state.
_HERMES_AGENT_TREES = (
    "/var/lib/hal0/venvs/hermes",
    "/var/lib/hal0/.hermes",
    "/var/lib/hal0/state/agents/hermes",
)


def _ensure_hermes_writable_or_die() -> None:
    """Abort with a sudo hint when provisioning can't write its trees.

    ``hal0 agent install hermes`` provisions into root-owned ``/var/lib/hal0``
    and is built to run as root on a system install — it chowns the result to
    the ``hal0`` agent user afterwards (:func:`_chown_hermes_trees_to_agent_user`).
    Run as a normal login user it used to crash several phases into the
    bootstrap with a raw ``PermissionError`` and leave half-owned trees behind
    (observed on a Fedora install). Catch the privilege mismatch up front, before
    the toolchain shell-out, so we abort cleanly with no side effects.

    No-op when we're root (root writes anywhere; the post-provision chown hands
    the trees to ``hal0``) or on a dev / rootless install that already owns the
    trees (the probe passes).
    """
    import os as _os

    from hal0.agents.hermes_provision import path_is_writable

    if _os.geteuid() == 0:
        return
    blocked = [t for t in _HERMES_AGENT_TREES if not path_is_writable(t)]
    if not blocked:
        return
    die(
        "Hermes provisioning needs write access to "
        + ", ".join(blocked)
        + f", but you're running as uid={_os.getuid()} and those live under "
        "root-owned /var/lib/hal0.\n\n"
        "Re-run as root:\n    sudo hal0 agent install hermes\n\n"
        f"(Provisioning runs as root, then hands the trees to the "
        f"'{_AGENT_RUNTIME_USER}' agent user automatically.)"
    )


def _chown_hermes_trees_to_agent_user() -> None:
    """Recursively chown the provisioned trees to the agent runtime user.

    Only acts when we're root AND the agent user exists — off-root installs
    (dev / `--dev`) already own everything, and a missing user means a
    non-standard layout we shouldn't second-guess. Skipping is silent and safe.
    """
    import os as _os
    import pwd as _pwd
    import subprocess as _subprocess
    from pathlib import Path as _Path

    if _os.geteuid() != 0:
        return
    try:
        _pwd.getpwnam(_AGENT_RUNTIME_USER)
    except KeyError:
        console.print(
            f"[yellow]agent user '{_AGENT_RUNTIME_USER}' not found — skipping chown; "
            "the agent unit may not be able to write $HERMES_HOME.[/yellow]"
        )
        return
    for tree in _HERMES_AGENT_TREES:
        if _Path(tree).exists():
            _subprocess.run(  # nosec B603 B607 — fixed argv, known paths
                ["chown", "-R", f"{_AGENT_RUNTIME_USER}:{_AGENT_RUNTIME_USER}", tree],
                check=False,
            )


def _enable_and_start_hermes_unit() -> None:
    """`systemctl enable --now hal0-agent@hermes` so the agent runs + persists.

    No-op when systemd isn't present (containers / dev). A non-zero rc is
    surfaced as a hint rather than failing the install — the agent is
    provisioned either way and can be started manually.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    if _shutil.which("systemctl") is None:
        return
    rc = _subprocess.run(  # nosec B603 B607 — fixed argv
        ["systemctl", "enable", "--now", "hal0-agent@hermes"], check=False
    ).returncode
    if rc != 0:
        console.print(
            "[yellow]Hermes provisioned, but the agent unit didn't start cleanly — "
            "check `systemctl status hal0-agent@hermes` / `hal0 agent log hermes`.[/yellow]"
        )


@app.command("uninstall")
def agent_uninstall(
    name: str = typer.Argument(..., help="Bundled agent name."),
    keep_memory: bool = typer.Option(
        False,
        "--keep-memory",
        help=(
            "Preserve the agent's private:<agent_id> Cognee namespace + "
            "its identity card. Default: full teardown including memory."
        ),
    ),
) -> None:
    """Uninstall a bundled agent."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    # Memory cleanup BEFORE we tear down the agent surface so a failed
    # memory call doesn't leave half-state. Skipped on --keep-memory
    # (per #246 + ADR-0011 §6 — re-install reuses the existing card).
    if name == "hermes" and not keep_memory:
        outcome = _uninstall_hermes_memory()
        _warn_memory_outcome(outcome)

    try:
        result = api_delete(f"/api/agents/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    status = (result or {}).get("status", "uninstalled")
    if status == "not_installed":
        console.print(f"[dim]{name} was not installed.[/dim]")
    else:
        console.print(f"[bold]Uninstalled[/bold] {name}.")
        if keep_memory:
            console.print("[dim](memory preserved — re-install will reuse it)[/dim]")


# Outcomes of ``_uninstall_hermes_memory``. See #350 — the bare-swallowed
# exception used to mask cases where the delete reported OK but the dataset
# still held leftover rows (observed 2026-05-26: 9 hermes-agent identity
# cards survived a default uninstall, operator got no signal). The four
# cases below let the CLI distinguish "all clear" from "memory was down"
# from "delete lied" without changing the idempotent exit-0 contract.
MemoryOutcomeStatus = Literal["deleted", "not_found", "unreachable", "leftover"]


@dataclass(frozen=True)
class MemoryUninstallOutcome:
    """Structured result from ``_uninstall_hermes_memory``.

    Attributes
    ----------
    outcome
        One of ``deleted`` / ``not_found`` / ``unreachable`` / ``leftover``.
        ``deleted`` = delete request succeeded AND the post-verify search
        found zero surviving rows. ``not_found`` = the pre-delete search
        already saw zero matching rows (true no-op). ``unreachable`` =
        the memory API couldn't be reached on either the search or the
        delete call. ``leftover`` = delete returned OK but a post-delete
        verify search still saw matching rows (the bug observed in #350).
    deleted_count
        Number of rows the delete call was issued against. Zero for
        ``not_found`` / ``unreachable`` (we never got to the delete).
    leftover_count
        Rows the post-delete verify search saw matching
        ``agent_id=hermes-agent``. ``None`` when the verify call itself
        couldn't run (transport error, unparseable response, etc.) —
        distinct from a confirmed zero.
    url
        The hal0 API base we tried (handy in stderr warnings so the
        operator knows what endpoint to check).
    """

    outcome: MemoryOutcomeStatus
    deleted_count: int
    leftover_count: int | None
    url: str


def _uninstall_hermes_memory() -> MemoryUninstallOutcome:
    """Best-effort: delete the hermes identity card from the `agents` dataset.

    Failure is tolerated (memory unreachable shouldn't strand the operator
    with a half-uninstalled agent — same contract as before #350), but
    surfaces the outcome to the caller so the CLI can warn on unreachable
    or leftover-row cases. The CLI always exits 0 regardless of outcome.
    """
    import json as _json
    import urllib.error
    import urllib.request

    # #302: REST shims at /api/memory/{search,delete} instead of the
    # broken /mcp/memory JSON-RPC POST. Same idempotent uninstall
    # semantics: failure is tolerated (memory unreachable shouldn't
    # strand the operator with a half-uninstalled agent).
    url = _api_base()
    headers = {"Content-Type": "application/json", "X-hal0-Agent": "hermes-agent"}

    def _search_ids() -> list[str] | None:
        """Return matching ids, or ``None`` if the search couldn't run."""
        search_body = _json.dumps(
            {
                "query": "hermes-agent",
                "tags": ["agent-identity"],
                "dataset": "agents",
                "limit": 50,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/api/memory/search", data=search_body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, _json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        items = data.get("items") or []
        ids: list[str] = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            md = it.get("metadata") or {}
            if md.get("agent_id") == "hermes-agent" and it.get("id"):
                ids.append(it["id"])
        return ids

    ids = _search_ids()
    if ids is None:
        return MemoryUninstallOutcome(
            outcome="unreachable", deleted_count=0, leftover_count=None, url=url
        )
    if not ids:
        # Pre-delete search saw zero — true no-op. Skip the verify
        # round-trip since there's nothing to verify.
        return MemoryUninstallOutcome(
            outcome="not_found", deleted_count=0, leftover_count=0, url=url
        )

    del_body = _json.dumps({"ids": ids}).encode("utf-8")
    req2 = urllib.request.Request(
        f"{url}/api/memory/delete", data=del_body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req2, timeout=5.0):
            pass
    except (urllib.error.URLError, OSError):
        return MemoryUninstallOutcome(
            outcome="unreachable", deleted_count=len(ids), leftover_count=None, url=url
        )

    # Post-delete verify: the 2026-05-26 incident logged in #350 had the
    # delete return 200 but leave 9 rows in place. Re-run the search and
    # surface the gap if rows survive.
    verify_ids = _search_ids()
    if verify_ids is None:
        # Delete returned OK but verify couldn't run. Don't escalate to
        # ``leftover`` — we have no evidence either way; treat as silent
        # success (best-effort contract preserved).
        return MemoryUninstallOutcome(
            outcome="deleted", deleted_count=len(ids), leftover_count=None, url=url
        )
    if verify_ids:
        return MemoryUninstallOutcome(
            outcome="leftover",
            deleted_count=len(ids),
            leftover_count=len(verify_ids),
            url=url,
        )
    return MemoryUninstallOutcome(
        outcome="deleted", deleted_count=len(ids), leftover_count=0, url=url
    )


def _warn_memory_outcome(outcome: MemoryUninstallOutcome) -> None:
    """Surface the two failure-ish outcomes as yellow stderr warnings.

    ``deleted`` / ``not_found`` stay silent (the happy path); only the
    two cases that mean "memory teardown didn't complete cleanly" warn.
    The CLI's exit code is unaffected — operator-facing signal only.
    """
    if outcome.outcome == "unreachable":
        _stderr_console.print(
            f"[yellow]warning[/yellow]: memory teardown skipped — "
            f"hal0 memory API unreachable at {outcome.url}. "
            "Re-run uninstall once the daemon is back, or use "
            "[bold]hal0 agent uninstall hermes --keep-memory[/bold] "
            "if this is intentional."
        )
    elif outcome.outcome == "leftover":
        leftover = outcome.leftover_count if outcome.leftover_count is not None else "?"
        _stderr_console.print(
            f"[yellow]warning[/yellow]: memory teardown incomplete — "
            f"{leftover} hermes-agent row(s) still in the [bold]agents[/bold] "
            "dataset after delete. Inspect with "
            "[bold]hal0 agent peers[/bold] and clean up by id via "
            "[bold]/api/memory/delete[/bold]."
        )


@app.command("list")
def agent_list(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/agents JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """List installed bundled agents."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/agents")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(data, indent=2))
        return
    agents = data.get("agents", []) if isinstance(data, dict) else data
    if not agents:
        console.print("[dim]No bundled agents installed.[/dim]")
        return
    table = Table(title=f"Bundled agents ({len(agents)})")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Installed at")
    table.add_column("Data dir")
    for a in agents:
        table.add_row(
            a.get("name", "—"),
            a.get("status", "—"),
            a.get("installed_at", "—"),
            a.get("data_dir", "—"),
        )
    console.print(table)


# ── Approvals (MCP-backend owns the route shape; CLI assumes ADR-0004 §5) ────


def _fmt_enqueued_at(value: Any) -> str:
    """Project ``enqueued_at`` (epoch seconds float) to a short ISO string.

    Mirrors the dashboard's ``AgentApprovalRow.vue`` tooltip projection
    (``new Date(epoch * 1000).toISOString()``) — keep CLI + UI agreeing
    on a single representation so screenshots and CLI output read the
    same to operators.
    """
    if value in (None, "", "—"):
        return "—"
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        # Already a string-ish timestamp — pass through untouched.
        return str(value)
    return (
        datetime.fromtimestamp(epoch, tz=UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _approval_summary(entry: dict[str, Any]) -> str:
    """Build a one-line Summary from ``tool`` + primary target arg.

    Mirrors ``AgentApprovalRow.vue``'s ``primaryArg`` projection: the
    arg name comes from :data:`hal0.mcp.approval_queue._PRIMARY_TARGET_ARG`
    so the CLI and the dashboard agree on which field is the
    "distinguishing" one. When the tool has no registered primary arg
    we fall back to the first scalar in ``args`` so the operator still
    sees something more useful than the bare tool name. Truncated to
    60 chars to fit a reasonable terminal width without wrapping.
    """
    tool = str(entry.get("tool") or "—")
    args = entry.get("args")
    if not isinstance(args, dict) or not args:
        return tool[:60]

    primary_key = _PRIMARY_TARGET_ARG.get(tool)
    primary_val: Any = None
    if primary_key is not None:
        primary_val = args.get(primary_key)
    if primary_val is None:
        # No registered primary arg (or it's missing) — fall back to
        # the first scalar value, matching the Vue row's behaviour.
        for v in args.values():
            if isinstance(v, str | int | float | bool) and v != "":
                primary_val = v
                break

    if isinstance(primary_val, list | tuple):
        primary_val = ",".join(str(v) for v in primary_val)
    summary = tool if primary_val is None or primary_val == "" else f"{tool} {primary_val}"
    return summary[:60]


@approvals_app.command("list")
def approvals_list() -> None:
    """List pending agent approval requests."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/agent/approvals")
    except CliApiError as exc:
        die(str(exc))
        return
    items = data.get("approvals", []) if isinstance(data, dict) else data
    if not items:
        console.print("[dim]No pending approvals.[/dim]")
        return
    table = Table(title=f"Pending approvals ({len(items)})")
    table.add_column("ID", style="bold")
    table.add_column("Tool")
    table.add_column("Agent")
    table.add_column("Requested at")
    table.add_column("Summary")
    for it in items:
        # ApprovalEntry.as_dict() emits ``client_id`` + ``enqueued_at``
        # + ``args`` — NOT ``agent`` / ``requested_at`` / ``summary``.
        # Mirror ui/src/components/agent/AgentApprovalRow.vue's
        # projection so CLI + dashboard show the same row content.
        table.add_row(
            str(it.get("id", "—")),
            str(it.get("tool", "—")),
            str(it.get("client_id") or "—"),
            _fmt_enqueued_at(it.get("enqueued_at")),
            _approval_summary(it),
        )
    console.print(table)


@approvals_app.command("approve")
def approvals_approve(
    approval_id: str = typer.Argument(..., help="Approval request ID."),
) -> None:
    """Approve a pending agent action."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_post(f"/api/agent/approvals/{approval_id}/approve")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"[bold green]Approved[/bold green] {approval_id}.")


@approvals_app.command("deny")
def approvals_deny(
    approval_id: str = typer.Argument(..., help="Approval request ID."),
) -> None:
    """Deny a pending agent action."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_post(f"/api/agent/approvals/{approval_id}/deny")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"[bold]Denied[/bold] {approval_id}.")


# ── Bootstrap (Hermes provisioning, Phase 10) ────────────────────────────────


@app.command("peers")
def agent_peers() -> None:
    """List discoverable agent identity cards (ADR-0011 §6).

    Thin wrapper over ``memory_search`` against the dedicated
    ``agents`` Cognee dataset. Sibling of ``hal0 agent list`` (which
    shows installed bundled agents on this host); ``peers`` shows
    every card published into the federated registry.
    """
    import json as _json
    import urllib.error
    import urllib.request

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    # #302: switched from the broken /mcp/memory JSON-RPC POST to the
    # REST shim at /api/memory/search. The MCP server at /mcp/memory
    # requires the FastMCP initialize handshake + session-tagged calls;
    # one-shot POST returns 405. The shim is plain HTTP.
    body = _json.dumps(
        {
            "query": "agent identity",
            "tags": ["agent-identity"],
            "dataset": "agents",
            "limit": 50,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/api/memory/search",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-hal0-Agent": "hal0-cli",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, _json.JSONDecodeError) as exc:
        die(f"memory API unreachable: {exc}")
        return

    items = data.get("items") or [] if isinstance(data, dict) else []
    if not items:
        console.print("[dim]No agent identity cards published yet.[/dim]")
        return

    table = Table(title=f"Agent peers ({len(items)})")
    table.add_column("Agent ID", style="bold")
    table.add_column("Display name")
    table.add_column("Roles")
    table.add_column("Endpoint")
    table.add_column("Registered")
    for item in items:
        md = item.get("metadata") or {} if isinstance(item, dict) else {}
        endpoint = md.get("endpoint") or {}
        hal0_state = md.get("hal0_state") or {}
        table.add_row(
            str(md.get("agent_id") or "—"),
            str(md.get("display_name") or "—"),
            ", ".join(md.get("roles") or []) or "—",
            str(endpoint.get("url") or "—"),
            str(hal0_state.get("registered_at") or "—"),
        )
    console.print(table)


@bootstrap_app.command("hermes")
def bootstrap_hermes(
    repair: bool = typer.Option(
        False,
        "--repair",
        help="Re-run every phase regardless of checkpoint state (forces full rerun).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run phases but don't persist provision.json.",
    ),
    skip_phase: list[str] = typer.Option(
        [],
        "--skip-phase",
        help="Skip the named phase (may be repeated).",
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Assume hermes-agent wheel is pre-staged; preflight skips PyPI check.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose phase log."),
) -> None:
    """Run the Hermes-Agent bootstrap state machine."""
    # Late import keeps the CLI startup snappy on hosts where the
    # hermes_provision module's downstream slices grow heavier deps.
    import os as _os

    from hal0.agents.hermes_provision import bootstrap_cli

    if offline:
        _os.environ["HAL0_HERMES_OFFLINE"] = "1"
    rc = bootstrap_cli(
        repair=repair,
        dry_run=dry_run,
        skip_phases=tuple(skip_phase),
        verbose=verbose,
    )
    raise typer.Exit(rc)


# ── Bootstrap status / log / upgrade / uninstall (Phase 10, #246) ───────────


@app.command("status")
def agent_status(
    name: str = typer.Argument("hermes", help="Bundled agent name (default: hermes)."),
) -> None:
    """Pretty-print the agent's provision.json checkpoint."""
    import json as _json
    from pathlib import Path

    state_file = Path(f"/var/lib/hal0/state/agents/{name}/provision.json")
    if not state_file.exists():
        console.print(f"[dim]{name}: no provision.json yet (run bootstrap first).[/dim]")
        raise typer.Exit(0)
    data = _json.loads(state_file.read_text())
    table = Table(title=f"{name} bootstrap status")
    table.add_column("Phase", style="bold")
    table.add_column("Status")
    table.add_column("At")
    table.add_column("Detail")
    for phase, entry in (data.get("phases") or {}).items():
        detail = entry.get("reason") or _json.dumps(entry.get("details") or {})[:60]
        table.add_row(phase, entry.get("status", "—"), entry.get("at", "—"), detail)
    console.print(table)
    console.print(
        f"[dim]hal0={data.get('hal0_version', '?')} "
        f"hermes={data.get('hermes_version', '?')} "
        f"completed_at={data.get('completed_at', '—')}[/dim]"
    )


@app.command("log")
def agent_log(
    name: str = typer.Argument("hermes", help="Bundled agent name."),
    phase: str | None = typer.Option(None, "--phase", help="Dump the named phase's log only."),
) -> None:
    """Show per-phase logs from /var/lib/hal0/state/agents/<name>/provision-logs/."""
    from pathlib import Path

    log_dir = Path(f"/var/lib/hal0/state/agents/{name}/provision-logs")
    if not log_dir.exists():
        console.print(f"[dim]{name}: no logs dir at {log_dir}[/dim]")
        raise typer.Exit(0)
    pattern = f"{phase}.log" if phase else "*.log"
    for log_file in sorted(log_dir.glob(pattern)):
        console.print(f"[bold]== {log_file.name} ==[/bold]")
        console.print(log_file.read_text())


@app.command("upgrade")
def agent_upgrade(
    name: str = typer.Argument("hermes", help="Bundled agent name."),
    to: str | None = typer.Option(
        None, "--to", help="Pin to a specific version (power-user / compat-testing flag)."
    ),
) -> None:
    """Bump the agent's version pin and re-run bootstrap with --repair."""
    if name != "hermes":
        die(f"upgrade currently only supports `hermes`; got {name!r}.")
        return
    import os as _os
    import subprocess as _subprocess  # nosec B404 — known argv

    if to:
        _os.environ["HAL0_HERMES_VERSION_PIN"] = to
    rc = _subprocess.run(  # nosec B603 — known argv
        ["hal0", "agent", "bootstrap", "hermes", "--repair"],
        check=False,
    ).returncode
    raise typer.Exit(rc)


# Note: post-ADR-0012 there is no `rotate-token` subcommand. The hal0
# daemon has no auth; agent identity flows via the X-hal0-Agent header
# the wrapper exports from $HAL0_AGENT_ID. See #246 sharpening's second
# correction comment for the supersede.


# ── Reprovision (PR-3, v0.3) ────────────────────────────────────────────────


@app.command("reprovision")
def agent_reprovision(
    name: str = typer.Argument("hermes", help="Bundled agent name."),
    repair: bool = typer.Option(
        False,
        "--repair",
        help="Force re-run of every phase (re-writes persona seeds, re-renders config).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Re-run the bootstrap state machine idempotently.

    Wrapper over ``hal0 agent bootstrap hermes`` that's name-stable
    across v0.4 agents (only flag the difference: this is the
    "re-converge" verb, not the "first install" verb). Phases that
    already produced their on-disk artefacts are skipped; phases whose
    inputs drifted re-run.
    """
    if name != "hermes":
        die(f"reprovision currently only supports `hermes`; got {name!r}.")
        return
    from hal0.agents.hermes_provision import bootstrap_cli

    rc = bootstrap_cli(
        repair=repair,
        dry_run=False,
        skip_phases=(),
        verbose=verbose,
    )
    raise typer.Exit(rc)


# ── Personas (PR-3, v0.3) ───────────────────────────────────────────────────


@personas_app.command("list")
def personas_list() -> None:
    """List personas + mark the active one.

    Reads ``/var/lib/hal0/.hermes/personas/*.toml`` directly so the
    CLI works without a running hal0-api (mirrors ``hal0 agent status``
    which also reads disk state).
    """
    from hal0.agents import personas as _personas

    items = _personas.list_personas()
    if not items:
        console.print(
            "[dim]No personas seeded yet. Run "
            "[bold]hal0 agent reprovision hermes[/bold] to seed the defaults.[/dim]"
        )
        return
    active = _personas.get_active()
    table = Table(title=f"Hermes personas ({len(items)})")
    table.add_column("ID", style="bold")
    table.add_column("Display name")
    table.add_column("Summary")
    table.add_column("Active")
    for persona in items:
        is_active = persona.id == active
        table.add_row(
            persona.id,
            persona.display_name,
            (persona.summary or "—")[:60],
            "[bold green]yes[/bold green]" if is_active else "—",
        )
    console.print(table)


@personas_app.command("show")
def personas_show(
    persona_id: str = typer.Argument(..., help="Persona id (filename stem)."),
) -> None:
    """Print a persona's TOML body.

    Useful for grabbing a starting template before hand-editing a new
    persona — copy the output, change ``[persona].id``, save under
    ``/var/lib/hal0/.hermes/personas/<new-id>.toml``.
    """
    import tomli_w

    from hal0.agents import personas as _personas

    try:
        persona = _personas.load_persona(persona_id)
    except FileNotFoundError as exc:
        die(str(exc))
        return
    except _personas.PersonaError as exc:
        die(f"persona {persona_id!r} is malformed: {exc}")
        return
    body = tomli_w.dumps(persona.to_dict())
    console.print(Panel(body, title=f"persona: {persona.id}", border_style="cyan"))


@personas_app.command("activate")
def personas_activate(
    persona_id: str = typer.Argument(..., help="Persona id to make active."),
    reload_url: str | None = typer.Option(
        None,
        "--reload-url",
        help="Override Hermes JSON-RPC URL for the hot-reload nudge.",
    ),
) -> None:
    """Switch the active persona + nudge running Hermes to hot-reload.

    Writes ``active.txt`` atomically; the nudge is best-effort. If
    Hermes isn't running, the next service start (or next reprovision)
    picks up the new active persona via the system_prompt prelude
    render. PR-4 wraps this in
    ``POST /api/agents/{id}/personas/{pid}/activate``.
    """
    from hal0.agents import personas as _personas

    try:
        result = _personas.activate(persona_id, reload_url=reload_url)
    except FileNotFoundError as exc:
        die(str(exc))
        return
    except _personas.PersonaError as exc:
        die(f"persona {persona_id!r} is malformed: {exc}")
        return
    panel_body = (
        f"[bold green]Activated[/bold green] {result['display_name']} "
        f"([dim]{result['persona_id']}[/dim])\n"
        f"active pointer: {result['active_path']}"
    )
    hot = result["hot_reload"]
    if hot["ok"]:
        panel_body += "\n[dim]Hot-reload nudge: ok[/dim]"
    else:
        panel_body += (
            "\n[yellow]Hot-reload nudge skipped[/yellow] — "
            f"{hot['error']}. The next Hermes restart will pick up the new persona."
        )
    console.print(Panel(panel_body, border_style="green"))
