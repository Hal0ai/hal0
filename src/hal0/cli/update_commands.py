"""CLI implementation for ``hal0 update``.

Thin client over the /api/updates/* surface. The CLI never invokes
``Updater`` directly - it goes through the daemon so the same code path
is exercised whether you trigger an update from the dashboard or the
shell. After a successful apply the daemon try-restarts hal0-api itself
(see ``routes/updater._run_apply_job``); the CLI does not touch systemd.

Surface:
    hal0 update                 # check + apply if newer
    hal0 update --check         # check only
    hal0 update --rollback      # roll back to previous tree
    hal0 update --channel CH    # set channel (persists), then check
    hal0 update --target VER    # pin a specific version
"""

from __future__ import annotations

import sys
import time
import tomllib
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

import hal0
from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    api_post,
    api_put,
    die,
)

console = Console()


class UpdateChannel(StrEnum):
    stable = "stable"
    nightly = "nightly"


def _editable_source_version() -> str | None:
    """Return the version in the source-tree pyproject.toml, if this is an
    editable/source checkout; otherwise None.

    In an editable install ``importlib.metadata.version("hal0")`` is frozen
    at ``pip install -e`` time and goes stale after a ``git pull``. We detect
    the source tree by walking up from ``hal0.__file__`` for a pyproject.toml
    whose project name is ``hal0`` and reading its declared version.
    """
    mod_file = getattr(hal0, "__file__", None)
    if not mod_file:
        return None
    for parent in Path(mod_file).resolve().parents:
        pp = parent / "pyproject.toml"
        if not pp.is_file():
            continue
        try:
            data = tomllib.loads(pp.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        project = data.get("project", {})
        if project.get("name") == "hal0":
            ver = project.get("version")
            return str(ver) if ver else None
        # A pyproject that isn't hal0's - stop walking (we left the tree).
        return None
    return None


def _warn_editable_version_drift() -> None:
    """Warn if the installed metadata version lags the source pyproject.

    Best-effort and silent on the common case (versions match or no source
    tree found). Surfaces the post-``git pull`` lie so an operator isn't
    misled by a stale ``hal0 --version``.
    """
    source = _editable_source_version()
    if not source:
        return
    installed = hal0.__version__
    # Compare semantically so PEP 440 normalization (0.3.2-alpha.1 vs
    # 0.3.2a1) doesn't trip a false positive. Fall back to string equality
    # if either side is unparseable.
    try:
        from packaging.version import InvalidVersion, Version

        try:
            drifted = Version(source) != Version(installed)
        except InvalidVersion:
            drifted = source != installed
    except ImportError:
        drifted = source != installed
    if drifted:
        console.print(
            f"[yellow]editable install: package metadata reports "
            f"{installed} but the source tree is {source}. "
            f"Re-run `pip install -e .` to refresh the version.[/yellow]"
        )


def _print_check(body: dict) -> None:
    """Render the /api/updates/check response as a rich panel + table."""
    current = body.get("current", "?")
    latest = body.get("latest") or "—"
    channel = body.get("channel", "stable")
    available = body.get("update_available", False)

    status = "[green]update available[/green]" if available else "[dim]up to date[/dim]"
    console.print(
        Panel(
            f"[bold]hal0[/bold] {current}  →  {latest}  ({channel})  {status}",
            border_style="cyan",
        )
    )
    manifest = body.get("manifest") or {}
    if not isinstance(manifest, dict):
        manifest = {}
    if manifest:
        table = Table(show_header=False, box=None, padding=(0, 2))
        for key in ("released_at", "notes_url", "digest_sha256", "signer_identity"):
            val = manifest.get(key)
            if val:
                table.add_row(f"[dim]{key}[/dim]", str(val))
        console.print(table)


def _poll_job(
    job_id: str,
    *,
    terminal: tuple[str, ...] = ("applied", "failed"),
    timeout_s: float = 600.0,
) -> dict:
    """Poll /api/updates/status/<id> until it reaches a terminal state."""
    deadline = time.monotonic() + timeout_s
    last_state: str | None = None
    while time.monotonic() < deadline:
        try:
            job = api_get(f"/api/updates/status/{job_id}")
        except CliApiError as exc:
            die(str(exc))
            return {}
        state = job.get("state")
        if state != last_state:
            console.print(f"[dim]· job {job_id} → {state}[/dim]")
            last_state = state
        if state in terminal:
            return job
        time.sleep(0.5)
    die(f"update job {job_id} timed out after {timeout_s:.0f}s")
    return {}


def _interactive() -> bool:
    """True on an interactive TTY — the gate for the pre-commit confirm prompt.

    Factored out so headless/piped runs (cron, scripts, ``|`` pipelines) skip the
    prompt, and so tests can force either path deterministically.
    """
    return sys.stdout.isatty()


def _render_notes(notes: dict | None) -> None:
    """Render the release notes from a prepared update job.

    ``breaking`` / ``migrations`` get a loud panel; ``highlights`` a bullet
    list; the full markdown (if any) is rendered below. All fields optional —
    a release without notes renders nothing.
    """
    if not isinstance(notes, dict):
        return
    highlights = notes.get("highlights") or []
    breaking = notes.get("breaking") or []
    migrations = notes.get("migrations") or []
    markdown = (notes.get("markdown") or "").strip()

    if breaking or migrations:
        lines = [f"[red]⚠ {b}[/red]" for b in breaking]
        lines += [f"[yellow]↻ {m}[/yellow]" for m in migrations]
        console.print(Panel("\n".join(lines), title="Breaking / migrations", border_style="yellow"))
    if highlights:
        console.print("[bold]Highlights[/bold]")
        for h in highlights:
            console.print(f"  • {h}")
    if markdown:
        console.print(Markdown(markdown))


def _fetch_slot_drift() -> dict:
    """Return the /api/updates/slot-drift payload, or an empty summary on error.

    Best-effort: the drift banner is a courtesy on top of the update flow, so
    a probe failure must never turn a successful update into a hard error.
    """
    try:
        body = api_get("/api/updates/slot-drift")
    except CliApiError:
        return {"count": 0, "slots": []}
    return body if isinstance(body, dict) else {"count": 0, "slots": []}


def _print_drift_banner(drift: dict) -> None:
    """Post-update ``N slots need restart`` banner (or a clean all-good line).

    ``rerender_slot_units`` refreshed each unit file on disk but never bounced
    the running process, so drifted slots are still serving the pre-update
    launch command. We surface that prominently and point at
    ``hal0 update --restart-slots`` — we never bounce automatically, because a
    slot may be mid-inference.
    """
    count = int(drift.get("count") or 0)
    if count == 0:
        console.print("[dim]no slots need restart.[/dim]")
        return
    slots = drift.get("slots") or []
    names = ", ".join(str(s.get("slot")) for s in slots if isinstance(s, dict) and s.get("slot"))
    plural = "s" if count != 1 else ""
    console.print(
        Panel(
            f"[bold yellow]{count} slot{plural} need restart[/bold yellow]\n"
            f"[dim]{names}[/dim]\n\n"
            "These slots are still running the pre-update launch command. Run "
            "[bold]hal0 update --restart-slots[/bold] to bounce only the drifted "
            "slots (this briefly interrupts any in-flight request on them).",
            title="Slots need restart",
            border_style="yellow",
        )
    )


def _restart_drifted_slots() -> None:
    """POST /api/updates/restart-slots and report the outcome.

    Clean-path message when nothing is drifted; otherwise restarts only the
    drifted slots and lists successes + per-slot failures.
    """
    drift = _fetch_slot_drift()
    if int(drift.get("count") or 0) == 0:
        console.print(Panel("[green]no slots need restart.[/green]", border_style="green"))
        return
    try:
        body = api_post("/api/updates/restart-slots")
    except CliApiError as exc:
        die(str(exc))
        return
    restarted = body.get("restarted") or []
    failed = body.get("failed") or []
    if restarted:
        console.print(
            Panel(
                f"[green]restarted {len(restarted)} slot(s):[/green] {', '.join(restarted)}",
                border_style="green",
            )
        )
    for f in failed:
        if isinstance(f, dict):
            console.print(f"[yellow]could not restart {f.get('slot')}:[/yellow] {f.get('error')}")


def _update_via_git() -> None:
    """Update hal0 by cloning/fetching the git repo and pip-installing.

    This is the local-only path — it goes through the CLI directly, not
    the API, because the git tree lives on the host filesystem.
    """
    from hal0.updater.updater import Updater

    console.print("[cyan]Updating via git (local)…[/cyan]")
    updater = Updater()
    try:
        import asyncio
        prepared = asyncio.run(updater.prepare_git())
        version = prepared["version"]
        console.print(f"[green]Prepared {version} from git[/green]")
        if _interactive() and not typer.confirm(f"Apply hal0 {version}?", default=True):
            console.print("[dim]Staged but not applied — re-run to apply.[/dim]")
            return
        result = asyncio.run(updater.commit_git(version))
        console.print(Panel(f"[green]Updated to {version}[/green]", border_style="green"))
        console.print(f"[dim]Restart hal0-api to apply:[/dim] systemctl restart hal0-api")
        _print_drift_banner(_fetch_slot_drift())
    except Exception as exc:
        die(f"git update failed: {exc}")


def update(
    channel: UpdateChannel | None = typer.Option(
        None,
        "--channel",
        help="Persist the update channel (stable | nightly), then check.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Only check for updates; do not apply.",
    ),
    rollback: bool = typer.Option(
        False,
        "--rollback",
        help="Roll back to the previous version recorded at /var/lib/hal0/hal0.previous.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Pin a specific version (e.g. v0.1.1). Overrides the latest manifest version.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt after reviewing release notes.",
    ),
    restart_slots: bool = typer.Option(
        False,
        "--restart-slots",
        help=(
            "Restart only the slots still running the pre-update launch command "
            "(post-update drift). Never bounces a slot unless you pass this flag."
        ),
    ),
    source: str = typer.Option(
        "release",
        "--source",
        help="Update source: 'release' (cosign-verified tarball, default) or 'git' (clone/fetch from GitHub).",
    ),
) -> None:
    """Check for, apply, or roll back a hal0 update.

    This is a thin client over /api/updates/*; the actual swap happens in
    the daemon. Real progress comes from polling /api/updates/status/<id>.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    _warn_editable_version_drift()

    if channel is not None:
        try:
            api_put("/api/updates/channel", json={"channel": channel.value})
        except CliApiError as exc:
            die(str(exc))
            return
        console.print(f"[green]channel set to {channel.value}[/green]")

    # ── git-based update: clone/fetch → pip install → symlink swap ────────────
    if source == "git":
        _update_via_git()
        return

    # Standalone action: bounce drifted slots on demand, then stop. Kept
    # separate from the check/apply flow so an operator can clear post-update
    # drift at any later time (e.g. once in-flight requests have drained).
    if restart_slots:
        _restart_drifted_slots()
        return

    if rollback:
        try:
            body = api_post("/api/updates/rollback")
        except CliApiError as exc:
            die(str(exc))
            return
        console.print(
            Panel(
                f"[green]rolled back[/green] ({body.get('channel', 'stable')})",
                border_style="green",
            )
        )
        return

    try:
        body = api_get("/api/updates/check")
    except CliApiError as exc:
        die(str(exc))
        return
    _print_check(body)

    if check:
        return

    target_version = (target or "").lstrip("v") or None
    if not body.get("update_available") and not target_version:
        console.print("[dim]nothing to apply.[/dim]")
        return

    # ── Stage → review notes → confirm → activate (prepare/commit split) ────────
    # prepare downloads + cosign-verifies + extracts WITHOUT touching the running
    # system, so we can show verified release notes and confirm before activating.
    try:
        job = api_post(
            "/api/updates/prepare", json={"version": target_version} if target_version else {}
        )
    except CliApiError as exc:
        die(str(exc))
        return
    job_id = job.get("id")
    if not job_id:
        die("server returned no job id")
        return
    console.print(f"[cyan]staging update:[/cyan] {job_id}")
    prepared = _poll_job(job_id, terminal=("prepared", "failed"))
    if prepared.get("state") != "prepared":
        die(f"prepare failed: {prepared.get('error') or 'unknown error'}")
        return

    resolved = prepared.get("resolved_version") or target_version
    _render_notes(prepared.get("notes"))

    # Confirm before activating. Prompt only on an interactive TTY without --yes;
    # headless/piped invocations (cron, scripts) proceed as before so unattended
    # updates never block. Nothing is active yet — declining just leaves the
    # staged tree, which is harmless.
    if not yes and _interactive() and not typer.confirm(f"Apply hal0 {resolved}?", default=True):
        console.print(
            "[dim]staged but not applied — re-run `hal0 update` to apply, "
            "or `hal0 update --yes`.[/dim]"
        )
        return

    try:
        cjob = api_post("/api/updates/commit", json={"version": resolved})
    except CliApiError as exc:
        die(str(exc))
        return
    cjob_id = cjob.get("id")
    if not cjob_id:
        die("server returned no commit job id")
        return
    console.print(f"[cyan]applying:[/cyan] {cjob_id}")

    final = _poll_job(cjob_id, terminal=("applied", "failed"))
    state = final.get("state")
    if state == "applied":
        console.print(Panel("[green]update applied.[/green]", border_style="green"))
        if final.get("restarted") is False and final.get("restart_error"):
            console.print(
                f"[yellow]hal0-api restart did not complete:[/yellow] {final['restart_error']}"
            )
        # The unit files were re-rendered but slots were NOT bounced (a restart
        # could kill a mid-inference request). Surface which slots are still
        # running the pre-update command so the operator can opt into a restart.
        _print_drift_banner(_fetch_slot_drift())
    else:
        err = final.get("error") or "unknown error"
        die(f"update {state}: {err}")
