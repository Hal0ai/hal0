"""hal0 CLI entry point.

Entry point declared in pyproject.toml:
    [project.scripts]
    hal0 = "hal0.cli.main:app"
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import structlog
import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import hal0
from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    die,
)
from hal0.cli.agent_commands import app as agent_app
from hal0.cli.app_commands import app as app_ext_app
from hal0.cli.auth_commands import app as auth_app
from hal0.cli.bench_commands import BENCH_CONTEXT_SETTINGS, BENCH_HELP
from hal0.cli.bench_commands import bench as bench_command
from hal0.cli.board_commands import app as board_app
from hal0.cli.capabilities_commands import app as capabilities_app
from hal0.cli.chat_commands import chat_command
from hal0.cli.comfyui_commands import app as comfyui_app
from hal0.cli.config_commands import app as config_app
from hal0.cli.doctor_commands import app as doctor_app
from hal0.cli.mcp_commands import app as mcp_app
from hal0.cli.memory_commands import app as memory_app
from hal0.cli.migrate_commands import app as migrate_app
from hal0.cli.model_commands import app as model_app
from hal0.cli.ports_command import ports_cmd
from hal0.cli.profile_commands import app as profile_app
from hal0.cli.registry_commands import app as registry_app
from hal0.cli.setup_command import app as setup_app
from hal0.cli.slot_commands import app as slot_app
from hal0.cli.update_commands import update_app
from hal0.cli.upstream_commands import app as upstream_app
from hal0.observability import sentry

console = Console()

# ---------------------------------------------------------------------------
# Root app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="hal0",
    help="hal0 — open-source home AI inference platform.",
    no_args_is_help=True,
    add_completion=True,
)

# Mount sub-apps
app.add_typer(slot_app, name="slot")
app.add_typer(model_app, name="model")
# Issue #258 — ``hal0 memory graph {status,enable,disable}`` surface.
# Mounted between ``model`` and ``config`` so it sits alongside the other
# user-facing data subcommands rather than buried under operator surfaces.
app.add_typer(memory_app, name="memory")
app.add_typer(config_app, name="config")
app.add_typer(doctor_app, name="doctor")
app.add_typer(upstream_app, name="upstream")
app.add_typer(capabilities_app, name="capabilities")
# Issue #1199 — ``hal0 comfyui orchestrate-models`` pulls the curated ComfyUI
# model set in one logged sequence.
app.add_typer(comfyui_app, name="comfyui")
app.add_typer(agent_app, name="agent")
# Issue #1102 — ``hal0 app install <name>`` (deferred install verb for apps
# skipped via HAL0_SKIP_OPENWEBUI=1 at install time; Q9 skip/defer parity).
app.add_typer(app_ext_app, name="app")
app.add_typer(migrate_app, name="migrate")
app.add_typer(registry_app, name="registry")
# Issue #1796 — ``hal0 profile {list,show}``, the read half of /api/profiles
# every slot TOML references but which had no CLI surface at all.
app.add_typer(profile_app, name="profile")
# Issue #504 — ``hal0 mcp {list,status,install,uninstall,restart,catalog}``
# CLI over /api/mcp/*. Mounted after registry before setup.
app.add_typer(mcp_app, name="mcp")
# §5.2 (R5 sync assessment) — the R3/R4 auth + operator-board surfaces the
# CLI had zero verbs for. `auth` mirrors /api/auth/{status,rotate,require};
# `board` is a thin list|show|add|move slice of /api/board.
app.add_typer(auth_app, name="auth")
app.add_typer(board_app, name="board")
# `setup` is an INTERNAL entry point, hidden from `hal0 --help` (v1.0): the
# installer is the single user-facing way to provision a box, and install.sh
# drives `hal0 setup --auto` itself (see the "First-run seeding" step there).
# Registered — not deleted — because that install-time invocation, the
# installer test harness, and support/debug runs all still need it; `hidden=True`
# is the same convention the other internal verbs use (`hal0 model register`,
# `hal0 slot add`, …). Do NOT re-advertise it in docs or the install banner.
app.add_typer(setup_app, name="setup", help="First-run setup (internal)", hidden=True)
# `hal0 bench <verb>` — a single passthrough command (not a typer group): the
# bench CLI is argparse-based (design §5), so the raw argv forwards to
# hal0.bench.cli.main. See bench_commands.py for why the context settings.
app.command("bench", help=BENCH_HELP, context_settings=BENCH_CONTEXT_SETTINGS)(bench_command)
# §21.14 — terminal chat REPL over the local /v1/chat/completions. Plain
# function registration (no sub-app: `hal0 chat` has no verbs of its own,
# just in-REPL slash commands), mirroring the `bench` passthrough above.
app.command("chat")(chat_command)
# §21.3 — `hal0 system-info`: host/GPU/NPU/runtime evidence in one read-only
# pass. Plain function registration (no verbs of its own), like `chat`/`bench`.
from hal0.cli.system_info_command import system_info_cmd  # noqa: E402

app.command("system-info")(system_info_cmd)
# §5.2 — `hal0 ports`: PortAuthority claim-map view. Plain function
# registration (no verbs of its own), like `chat`/`bench`/`system-info`.
app.command("ports")(ports_cmd)


# ---------------------------------------------------------------------------
# --version callback
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"hal0 {hal0.__version__}")
        raise typer.Exit()


def _configure_cli_logging() -> None:
    """Filter structlog output for one-shot CLI commands.

    Nothing in the CLI process ever calls ``structlog.configure``, so it runs
    with structlog's own default — a bare print logger with NO level filter.
    Any ``log.debug(...)`` on a code path a command happens to exercise (e.g.
    the hardware probe's nvidia-smi fallback) prints straight to stdout,
    above the command's own table (#1796). Default to WARNING so debug/info
    telemetry stays silent for humans; ``HAL0_LOG_LEVEL`` still overrides for
    anyone who wants it back (mirrors the server-side log level env var).
    """
    level_name = os.environ.get("HAL0_LOG_LEVEL", "WARNING").strip().upper()
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        level = logging.WARNING
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


@app.callback()
def main_callback(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        help="Print version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """hal0 — open-source home AI inference platform."""
    # Runs before any subcommand, so `hal0 bench worker` (the long-lived
    # hal0-bench-worker.service process) and every one-shot CLI call are
    # both covered by one hook. Unhandled CLI exceptions are then picked up
    # by the SDK's own excepthook integration. Inert without a DSN.
    _configure_cli_logging()
    sentry.init_sentry("cli")


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Show system and slot summary."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        st = api_get("/api/status")
        slots = api_get("/api/slots")
        ups = api_get("/api/upstreams")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        Panel(
            f"[bold]{st.get('name', 'hal0')}[/bold] v{st.get('version', '?')}  "
            f"· slots={len(slots)} · upstreams={len(ups)}",
            border_style="cyan",
        )
    )
    table = Table(title="Slots")
    table.add_column("Name", style="bold")
    table.add_column("State")
    table.add_column("Model")
    table.add_column("Port", justify="right")
    for s in slots:
        table.add_row(
            s.get("name", "—"),
            s.get("status", "—"),
            s.get("model") or s.get("model_id") or "—",
            str(s.get("port") or "—"),
        )
    console.print(table)


# HAL0-SUNSET: v1.0.0 — alias for `hal0 config hardware --refresh`; drop the alias.
@app.command(hidden=True)
def probe() -> None:
    """[DEPRECATED] alias for `hal0 config hardware --refresh`; use that instead."""
    typer.echo(
        "[deprecated] `hal0 probe` is replaced by `hal0 config hardware --refresh`; "
        "use `hal0 config hardware --refresh`.",
        err=True,
    )
    from hal0.cli.config_commands import config_hardware

    config_hardware(refresh=True)


# ---------------------------------------------------------------------------
# hal0 update — real implementation lives in hal0.cli.update_commands.
# A Typer group: the bare `hal0 update` runs the self-update (group callback,
# invoke_without_command), and `hal0 update owui` repins the OpenWebUI image.
# ---------------------------------------------------------------------------

app.add_typer(update_app, name="update")


# ---------------------------------------------------------------------------
# hal0 serve  (Phase 0 — the only command that actually does something)
# ---------------------------------------------------------------------------


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        envvar="HAL0_BIND_HOST",
        help=(
            "Bind host for the hal0 API. Defaults to $HAL0_BIND_HOST — the "
            "SAME var the hal0-api systemd unit reads from /etc/hal0/api.env "
            "(WS-C network coherence) — then 127.0.0.1 when neither is set."
        ),
    ),
    port: int = typer.Option(
        8080, "--port", envvar="HAL0_PORT", help="Bind port for the hal0 API."
    ),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev mode)."),
) -> None:
    """Start the hal0 API server (used by hal0-api.service)."""
    console.print(f"Starting hal0 API on [bold]{host}:{port}[/bold]")
    # Bounded graceful shutdown (issue #1225): uvicorn's default
    # timeout_graceful_shutdown is None (wait forever for open connections —
    # e.g. an in-flight model pull request or a live SSE progress stream —
    # to close on their own) before it ever sends the ASGI lifespan.shutdown
    # event. Since hal0-api's lifespan is where in-flight pulls get cancelled
    # (see hal0.api.lifespan), that cancellation would never run in time and
    # `systemctl restart hal0-api` would hang until systemd's own
    # TimeoutStopSec (default ~90s) SIGKILLs the process mid-download. This
    # bound guarantees lifespan.shutdown fires promptly either way.
    uvicorn.run(
        "hal0.api:app",
        host=host,
        port=port,
        reload=reload,
        timeout_graceful_shutdown=20,
    )


# ---------------------------------------------------------------------------
# hal0 uninstall
# ---------------------------------------------------------------------------


@app.command()
def uninstall(
    purge: bool = typer.Option(
        False,
        "--purge",
        "--clean-slate",
        help="Clean slate: ALSO delete /etc/hal0, /var/lib/hal0 (models, "
        "registry, memory banks), the hal0 system user, and all hal0 podman "
        "images. Prompts for DELETE unless --force.",
    ),
    keep_data: bool = typer.Option(
        False,
        "--keep-data",
        help="Conservative mode (the default): keep /etc/hal0 + /var/lib/hal0. "
        "Accepted for back-compat / explicitness.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip the --purge DELETE confirmation prompt (also honours HAL0_FORCE=1).",
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Tear down a dev-mode install rooted at $PWD/.hal0ai (or $HAL0_PREFIX).",
    ),
) -> None:
    """Uninstall hal0 from this system.

    Thin wrapper around ``installer/uninstall.sh`` — the shell script is the
    source of truth and mirrors install.sh's path layout. We exec it so the
    script inherits the live TTY for its DELETE confirmation prompt.

    By default this is conservative: it stops services and removes code, units,
    venvs, binaries, and containers but KEEPS /etc/hal0 and /var/lib/hal0 so a
    re-install reuses them. Pass ``--purge`` for a full clean slate (wipes
    config, data, the system user, and pulled container images).
    """
    import shutil

    from hal0.config import paths

    # The uninstaller ships in the source tree, which lives in different places
    # depending on install layout (#495):
    #   - editable/dev:  src/hal0/__init__.py -> repo root is parents[2]
    #   - FHS prod:      installed non-editable, so __file__ is in the venv
    #     site-packages; the source tree is under the `current` symlink.
    candidates = [
        Path(hal0.__file__).resolve().parents[2] / "installer" / "uninstall.sh",
        paths.usr_lib() / "installer" / "uninstall.sh",
    ]
    script = next((c for c in candidates if c.is_file()), None)
    if script is None:
        die(
            "uninstall.sh not found (looked in "
            + ", ".join(str(c) for c in candidates)
            + "). This hal0 install looks packaged differently — run the script directly."
        )

    if not shutil.which("bash"):
        die("bash is required to run the uninstaller.")

    # The script's DELETE prompt only fires under --purge. A conservative run
    # (the default, or --keep-data) never prompts, so it's safe non-interactive.
    # For --purge we still require a TTY unless the caller opted out of the
    # prompt (--force / HAL0_FORCE=1), else the prompt would hang silently.
    force_env = os.environ.get("HAL0_FORCE") == "1"
    if purge and not (force or force_env) and not sys.stdin.isatty():
        die(
            "Refusing to --purge non-interactively without --force — "
            "the shell script's DELETE prompt would hang."
        )

    argv = ["bash", str(script)]
    if purge:
        argv.append("--purge")
    if keep_data:
        argv.append("--keep-data")
    if force:
        argv.append("--force")
    if dev:
        argv.append("--dev")

    os.execvp("bash", argv)
