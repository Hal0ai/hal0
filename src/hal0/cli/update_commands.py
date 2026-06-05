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

import time
import tomllib
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
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


def _poll_job(job_id: str, *, timeout_s: float = 600.0) -> dict:
    """Poll /api/updates/status/<id> until it leaves the queued/running states."""
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
        if state in ("applied", "failed"):
            return job
        time.sleep(0.5)
    die(f"update job {job_id} timed out after {timeout_s:.0f}s")
    return {}


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

    try:
        job = api_post(
            "/api/updates/apply", json={"version": target_version} if target_version else {}
        )
    except CliApiError as exc:
        die(str(exc))
        return
    job_id = job.get("id")
    if not job_id:
        die("server returned no job id")
        return
    console.print(f"[cyan]apply job:[/cyan] {job_id}")

    final = _poll_job(job_id)
    state = final.get("state")
    if state == "applied":
        console.print(Panel("[green]update applied.[/green]", border_style="green"))
        if final.get("restarted") is False and final.get("restart_error"):
            console.print(
                f"[yellow]hal0-api restart did not complete:[/yellow] {final['restart_error']}"
            )
    else:
        err = final.get("error") or "unknown error"
        die(f"update {state}: {err}")
