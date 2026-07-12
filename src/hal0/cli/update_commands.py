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

import os
import subprocess
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

#: The `hal0 update` surface. The group callback runs the hal0 self-update
#: (the historical behaviour of the bare `hal0 update`); `owui` is a sibling
#: verb that updates the OpenWebUI companion image. Registered in main.py via
#: ``app.add_typer(update_app, name="update")``.
update_app = typer.Typer(
    help="Update hal0, or its OpenWebUI companion (`hal0 update owui`).",
    no_args_is_help=False,
)


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


def _refuse_if_editable() -> None:
    """Die early on an editable/dev install — `hal0 update` can't swap the FHS tree.

    Mirrors the daemon-side refusal (audit 4.1) so an operator on a
    `pip install -e` checkout gets an immediate, actionable message instead of
    a background prepare job that silently fails. Detection is metadata-driven
    so an editable install cloned from git is caught too.
    """
    from hal0.updater.updater import _editable_install_path, _is_editable_install

    if not _is_editable_install():
        return
    path = _editable_install_path() or "the current source tree"
    die(
        f"hal0 is installed in editable mode from {path}. "
        "Install from release wheel with `pip install hal0`."
    )


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


def _update_via_git(check_only: bool = False) -> None:
    """Update hal0 by cloning/fetching the git repo and pip-installing.

    When *check_only* is True, only report whether an update is available
    without applying it.
    """
    from hal0.updater.updater import Updater

    console.print("[cyan]Checking for updates via git (local)…[/cyan]")
    updater = Updater()
    import asyncio

    prepared = asyncio.run(updater.prepare_git())
    version = prepared["version"]

    current = hal0.__version__
    try:
        from packaging.version import Version

        newer = Version(version) > Version(current)
    except Exception:
        newer = version != current

    if not newer:
        console.print(f"[dim]hal0 {current} is up to date (latest tag: {version})[/dim]")
        return

    console.print(f"[green]hal0 {current} → {version}  update available[/green]")
    if check_only:
        return

    if _interactive() and not typer.confirm(f"Apply hal0 {version}?", default=True):
        console.print("[dim]Staged but not applied — re-run to apply.[/dim]")
        return
    asyncio.run(updater.commit_git(version))
    console.print(Panel(f"[green]Updated to {version}[/green]", border_style="green"))
    console.print("[dim]Restart hal0-api to apply:[/dim] systemctl restart hal0-api")
    _print_drift_banner(_fetch_slot_drift())


@update_app.callback(invoke_without_command=True)
def update(
    ctx: typer.Context,
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
    # `hal0 update owui` (and any future sibling verb) still runs this
    # callback first — bail so the bare `hal0 update` self-update only fires
    # when no sub-command was given, mirroring the `hal0 doctor` pattern.
    if ctx.invoked_subcommand is not None:
        return

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
        _update_via_git(check_only=check)
        return

    # Standalone action: bounce drifted slots on demand, then stop. Kept
    # separate from the check/apply flow so an operator can clear post-update
    # drift at any later time (e.g. once in-flight requests have drained).
    if restart_slots:
        _restart_drifted_slots()
        return

    if rollback:
        # Same confirm gate as the apply path below: interactive TTY without
        # --yes prompts, headless/piped invocations proceed unattended. A
        # rollback reverts the whole install tree — at least as consequential
        # as an apply, so it shouldn't fire on zero confirmation.
        if (
            not yes
            and _interactive()
            and not typer.confirm(
                "Roll back to the previous hal0 install? This reverts the entire install tree.",
                default=False,
            )
        ):
            console.print("[dim]rollback cancelled.[/dim]")
            return
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

    # Refuse before staging: an editable/dev install has no FHS tree to swap,
    # so a prepare/commit would either 409 at the daemon or phantom-succeed
    # (audit 4.1). Fail fast with an actionable message.
    _refuse_if_editable()

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


# ── hal0 update owui — repin the OpenWebUI companion image ─────────────────────
#
# OpenWebUI is a podman container pinned by sha256 manifest-list digest in the
# installed unit (packaging/systemd/hal0-openwebui.service, copied to
# /etc/systemd/system by install.sh). It is NOT in manifest.json, and the hal0
# self-updater does not touch companion units — so a runtime repin of the
# installed unit is durable across `hal0 update` (only a fresh install.sh run
# rewrites it). This command resolves a digest (upstream tag or explicit
# --target), repins the unit, pulls, and restarts. The pure text seam lives in
# hal0.openwebui.image_pin; the release-source pin sites (install.sh + the
# packaging unit) are a separate maintainer concern — see
# scripts/update-owui-digest.sh + the pin-consistency test.


def _dev_mode() -> bool:
    """True under an ``install.sh --dev`` layout (HAL0_HOME set).

    In dev we rewrite the unit file but skip the privileged podman/systemctl
    side effects — there is no real service to bounce.
    """
    return bool(os.environ.get("HAL0_HOME", "").strip())


def _run_cmd(argv: list[str], *, timeout: float) -> tuple[int | None, str, str]:
    """Run ``argv``; return ``(rc, stdout, stderr)``. rc=None on spawn/timeout."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "", f"{argv[0]} timed out after {timeout:.0f}s"
    except (FileNotFoundError, OSError) as exc:
        return None, "", f"{argv[0]} not runnable: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def _resolve_owui_upstream_digest(tag: str) -> str:
    """Resolve the published ghcr.io manifest-list digest for OWUI ``tag``.

    Reuses the anonymous OCI probe the toolbox surface already ships. Raises
    ``httpx.HTTPError`` / ``RuntimeError`` / ``ValueError`` on failure for the
    caller to turn into a clean ``die``.
    """
    import httpx

    from hal0.cli.doctor_commands import _ghcr_anon_token, _ghcr_manifest_digest
    from hal0.openwebui.image_pin import OPENWEBUI_GHCR_REPO

    with httpx.Client(follow_redirects=True) as client:
        token = _ghcr_anon_token(OPENWEBUI_GHCR_REPO, client=client)
        return _ghcr_manifest_digest(OPENWEBUI_GHCR_REPO, tag, token=token, client=client)


def _write_unit_atomic(unit: Path, text: str) -> None:
    """Rewrite ``unit`` in place, preserving mode, via a same-dir temp + replace."""
    try:
        mode = unit.stat().st_mode & 0o777
    except OSError:
        mode = 0o644
    tmp = unit.with_name(f".{unit.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, unit)


@update_app.command("owui")
def update_owui(
    check: bool = typer.Option(
        False,
        "--check",
        help="Only report the pinned vs upstream digest; don't repin or restart.",
    ),
    tag: str | None = typer.Option(
        None,
        "--tag",
        help="Upstream tag to resolve (default: OpenWebUI's :main). Ignored with --target.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Pin an explicit digest (sha256:… or bare 64-hex) instead of resolving a tag.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt before repinning."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Repin + pull + restart even when the digest already matches.",
    ),
) -> None:
    """Update the OpenWebUI companion container to a newer pinned image.

    OpenWebUI runs as a podman container pinned by sha256 digest in its systemd
    unit. This resolves a target digest (upstream ``--tag`` or explicit
    ``--target``), repins the installed unit, pulls the image, and restarts
    ``hal0-openwebui``. The repin is durable — ``hal0 update`` (the hal0
    self-update) never rewrites companion units.
    """
    from hal0.openwebui import image_pin

    unit = image_pin.installed_unit_path()
    if not unit.is_file():
        die(f"OpenWebUI unit not found at {unit} — is the OpenWebUI companion installed?")
        return
    text = unit.read_text(encoding="utf-8")
    current = image_pin.parse_pinned_digest(text)
    if current is None:
        die(
            f"could not read a consistent pinned digest from {unit} "
            "(no pin, or the two pins disagree — inspect the unit)."
        )
        return

    # ── Resolve the target digest ────────────────────────────────────────────
    resolve_tag = tag or image_pin.OPENWEBUI_DEFAULT_TAG
    if target is not None:
        target_digest = image_pin.normalize_digest(target)
        if target_digest is None:
            die(f"--target {target!r} is not a sha256 digest (expected sha256:<64-hex> or 64-hex).")
            return
        source = "target"
    else:
        try:
            target_digest = _resolve_owui_upstream_digest(resolve_tag)
        except Exception as exc:
            die(f"could not resolve ghcr.io {image_pin.OPENWEBUI_IMAGE_REPO}:{resolve_tag}: {exc}")
            return
        if not image_pin.is_sha256_digest(target_digest):
            die(f"ghcr.io returned an unexpected digest for :{resolve_tag}: {target_digest!r}")
            return
        source = f"ghcr :{resolve_tag}"

    drifted = target_digest != current
    table = Table(title="OpenWebUI image pin", show_header=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("pinned (now)", current)
    table.add_row(f"target ({source})", target_digest)
    table.add_row(
        "status", "[green]newer available[/green]" if drifted else "[dim]up to date[/dim]"
    )
    console.print(table)

    if check:
        return

    if not drifted and not force:
        console.print(
            "[dim]already pinned to that digest — nothing to do (use --force to re-pull).[/dim]"
        )
        return

    dev = _dev_mode()
    if not dev and hasattr(os, "geteuid") and os.geteuid() != 0:
        die("repinning the unit and restarting hal0-openwebui need root — re-run with sudo.")
        return

    short = target_digest[:19] + "…"
    if (
        not yes
        and _interactive()
        and not typer.confirm(f"Repin OpenWebUI → {short}?", default=True)
    ):
        console.print("[dim]aborted — unit unchanged.[/dim]")
        return

    ref = image_pin.pinned_ref(target_digest)

    # Pull FIRST (unless dev): if the new image isn't pullable, abort before
    # touching the unit so we never leave it pointing at an unusable digest.
    if not dev:
        console.print(f"[cyan]pulling[/cyan] {ref}")
        rc, _out, err = _run_cmd(["podman", "pull", ref], timeout=600.0)
        if rc != 0:
            die(f"podman pull failed — unit unchanged: {err.strip() or f'exit {rc}'}")
            return

    new_text, count = image_pin.repin_unit_text(text, target_digest)
    if count == 0:  # pragma: no cover — parse_pinned_digest already proved a match
        die("internal error: no digest occurrences rewritten in the unit.")
        return
    try:
        _write_unit_atomic(unit, new_text)
    except OSError as exc:
        die(f"could not write {unit}: {exc}")
        return
    console.print(f"[green]repinned[/green] {unit} ({count} occurrence(s))")

    if dev:
        console.print("[dim]dev mode (HAL0_HOME set): skipping daemon-reload / restart.[/dim]")
        return

    rc, _out, err = _run_cmd(["systemctl", "daemon-reload"], timeout=30.0)
    if rc != 0:
        console.print(
            f"[yellow]systemctl daemon-reload failed:[/yellow] {err.strip() or f'exit {rc}'}"
        )

    console.print(f"[cyan]restarting[/cyan] {image_pin.OPENWEBUI_UNIT_NAME}")
    rc, _out, err = _run_cmd(["systemctl", "restart", image_pin.OPENWEBUI_UNIT_NAME], timeout=120.0)
    if rc != 0:
        die(
            f"restart failed: {err.strip() or f'exit {rc}'}. "
            f"The unit is repinned; check `journalctl -u {image_pin.OPENWEBUI_UNIT_NAME} -n 40`."
        )
        return
    console.print(Panel("[green]OpenWebUI updated.[/green]", border_style="green"))
