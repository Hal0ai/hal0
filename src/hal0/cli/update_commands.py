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
from enum import StrEnum
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
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
    preview = "preview"
    nightly = "nightly"


#: A manifest whose version is exactly "0.0.0" (optionally paired with an
#: all-zero digest) is the placeholder the release service serves for a
#: channel with nothing published yet — not a real, installable target.
#: Rendering it as "→ 0.0.0 up to date" tells the operator there is nothing
#: to install when the truth is nobody has published anything here.
_PLACEHOLDER_VERSION = "0.0.0"
_PLACEHOLDER_DIGEST = "0" * 64


def _is_placeholder_manifest(latest: str, manifest: dict) -> bool:
    if latest.strip() == _PLACEHOLDER_VERSION:
        return True
    digest = manifest.get("digest_sha256")
    return isinstance(digest, str) and digest == _PLACEHOLDER_DIGEST


def _print_check(body: dict) -> None:
    """Render the /api/updates/check response as a rich panel + table."""
    current = body.get("current", "?")
    latest_raw = str(body.get("latest") or "")
    channel = body.get("channel", "stable")
    available = body.get("update_available", False)
    manifest = body.get("manifest") or {}
    if not isinstance(manifest, dict):
        manifest = {}

    if _is_placeholder_manifest(latest_raw, manifest):
        console.print(
            Panel(
                f"[bold]hal0[/bold] {current}  ({channel})  "
                "[yellow]no release published on this channel[/yellow]",
                border_style="cyan",
            )
        )
        return

    latest = latest_raw or "—"
    status = "[green]update available[/green]" if available else "[dim]up to date[/dim]"
    console.print(
        Panel(
            f"[bold]hal0[/bold] {current}  →  {latest}  ({channel})  {status}",
            border_style="cyan",
        )
    )
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


#: How long the poll tolerates a completely unreachable API before giving up.
#: A commit restarts hal0-api underneath us, so connection-refused is the
#: EXPECTED response for a few seconds mid-update, not a failure (#1540).
#: Bounded separately from ``timeout_s`` so a genuinely dead API still fails in
#: seconds rather than sitting out the full 600s job budget.
_POLL_UNREACHABLE_BUDGET_S = 180.0


def _poll_job(
    job_id: str,
    *,
    terminal: tuple[str, ...] = ("applied", "failed"),
    timeout_s: float = 600.0,
) -> dict:
    """Poll /api/updates/status/<id> until it reaches a terminal state.

    Survives the restart window. ``commit`` bounces hal0-api as part of
    applying, so this poll is talking to a service that is deliberately
    going away and coming back. Treating the resulting connection error as
    fatal made every successful update exit 1 (#1540).

    Only *transport* failures are retried. A ``CliApiError`` raised from an
    HTTP status (a live API answering 4xx/5xx) still dies immediately —
    blanket-retrying would turn a real commit failure into a long hang.
    """
    deadline = time.monotonic() + timeout_s
    last_state: str | None = None
    unreachable_since: float | None = None
    warned_unreachable = False
    while time.monotonic() < deadline:
        try:
            job = api_get(f"/api/updates/status/{job_id}")
        except CliApiError as exc:
            if not isinstance(exc.__cause__, httpx.TransportError):
                # A live API returned an error status - that is a real
                # failure, not the restart window.
                die(str(exc))
                return {}
            now = time.monotonic()
            if unreachable_since is None:
                unreachable_since = now
            elif now - unreachable_since > _POLL_UNREACHABLE_BUDGET_S:
                die(
                    f"hal0-api stayed unreachable for "
                    f"{_POLL_UNREACHABLE_BUDGET_S:.0f}s while applying job {job_id}. "
                    "The update may have completed - check `systemctl status hal0-api` "
                    "and `hal0 --version`."
                )
                return {}
            if not warned_unreachable:
                console.print("[dim]· hal0-api restarting, waiting for it to come back…[/dim]")
                warned_unreachable = True
            time.sleep(1.0)
            continue
        unreachable_since = None
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


def _decide_profile_reset(status: dict | None, *, yes: bool) -> bool | None:
    """Decide whether to consent to the one-shot v1.0 profile-catalog reset.

    v1.0 made profiles tuning-only, so a pre-v1.0 ``/etc/hal0/profiles.toml`` is
    reset exactly once (backed up first; the built-in catalog is virtual, so the
    reseed is free). ``commit()`` runs inside hal0-api and has no TTY, so the
    decision is made here and posted with the commit.

    Policy — deliberately three-way, and biased against destroying operator work:

    * Nothing to lose (fresh box, seeds only, or an unparseable file that is
      already breaking this install) → converge silently, ``None`` is enough;
      ``reset_profile_catalog`` proceeds on its own when ``needs_consent`` is
      false. Nothing is prompted for a no-op.
    * ``--yes`` → explicit operator consent, wipe.
    * Interactive TTY → prompt, naming the exact profiles at risk. Defaults to
      yes: convergence is the point of the upgrade and a timestamped backup is
      written first, but the operator can decline and keep them.
    * Headless without ``--yes`` → **skip**. An unattended cron update must
      never delete operator-authored profiles. The gate stays unstamped and the
      post-update convergence banner says it is still outstanding.

    Returns True (consent), or None (no consent — never a hard "no", because the
    reset itself distinguishes "declined" from "nothing to consent to").
    """
    if not isinstance(status, dict) or not status.get("due"):
        return None
    if not status.get("needs_consent"):
        return None
    names = [str(n) for n in (status.get("custom_profiles") or [])]
    if yes:
        return True
    if not _interactive():
        console.print(
            "[yellow]![/yellow] the one-shot profile-catalog reset is due but needs consent "
            f"({len(names)} custom profile(s)); skipping in a non-interactive run.\n"
            "[dim]  re-run on a terminal, or pass --yes, to converge.[/dim]"
        )
        return None
    console.print(
        Panel(
            "[bold]hal0 v1.0 makes profiles tuning-only.[/bold]\n"
            "Converging this box resets /etc/hal0/profiles.toml. The 16 built-in "
            "profiles are re-seeded from code automatically, but these "
            "operator-authored profiles would be deleted:\n\n"
            + "\n".join(f"  • {n}" for n in names)
            + "\n\nThis includes any profile the UI currently refuses to save — a stored "
            "hardware flag (-dev, --threads, -ngl) makes v1.0 reject it on edit (#1411). "
            "Those are deleted here, not repaired.\n\n"
            "[dim]A timestamped copy is written to /var/lib/hal0/backups/ first, and it is "
            "the only way to get any of them back. Slots pointing at a deleted profile fall "
            "back to their base config.[/dim]",
            title="Profile catalog reset",
            border_style="yellow",
        )
    )
    if typer.confirm("Reset the profile catalog now?", default=True):
        return True
    console.print("[dim]profile catalog kept — the reset stays due on the next update.[/dim]")
    return None


def _maybe_converge_profiles(status: dict | None, *, yes: bool) -> bool:
    """Converge an outstanding v1.0 profile-catalog reset when no update is due.

    #1585: the reset rides ``commit()``, but a box updated 0.9.8→1.0 ran commit
    under the old daemon (no reset), so it lands converged-except-for-this and
    the up-to-date path used to print "nothing to apply" and hide it. When the
    ``/check`` snapshot says a reset is still due, converge it here — a local
    ``/converge-profiles`` call, no download or swap.

    Returns True when it handled the situation (converged, or raised ``Exit(2)``
    because a consent-needing reset is outstanding). Returns False when there is
    nothing due — the caller then prints its usual "nothing to apply".
    """
    if not isinstance(status, dict) or not status.get("due"):
        return False

    reset_profiles = _decide_profile_reset(status, yes=yes)

    # Consent needed but not given (headless, or interactively declined): honor
    # the half-converged contract — name it as outstanding and exit 2, distinct
    # from a clean "up to date" (0).
    if status.get("needs_consent") and reset_profiles is not True:
        console.print(
            Panel(
                "[yellow]up to date, but this install is NOT fully converged.[/yellow]\n"
                "The one-shot v1.0 profile-catalog reset is still outstanding.\n"
                "[dim]Re-run `hal0 update --yes` on a terminal to converge. "
                "(exit 2 = up to date, convergence outstanding)[/dim]",
                border_style="yellow",
            )
        )
        raise typer.Exit(2)

    convergence_body: dict[str, object] = {}
    if reset_profiles is True:
        convergence_body["reset_profiles"] = True
    try:
        result = api_post("/api/updates/converge-profiles", json=convergence_body)
    except CliApiError as exc:
        die(str(exc))
        return True

    if result.get("performed"):
        backup = result.get("backup")
        console.print(
            Panel(
                "[green]profile catalog converged.[/green]"
                + (f"\n[dim]backup: {backup}[/dim]" if backup else ""),
                border_style="green",
            )
        )
    else:
        # already_reset / no_config — nothing was outstanding after all.
        console.print("[dim]nothing to apply.[/dim]")
    return True


def _print_convergence(convergence: dict | None) -> bool:
    """Report how far the updated box is from the v1.0 on-disk shape.

    Returns True when fully converged. A False return is what makes
    ``hal0 update`` refuse to call the run a clean success — silently handing
    back a half-migrated box is the failure mode this exists to prevent.
    """
    if not isinstance(convergence, dict):
        return True

    swept = convergence.get("slot_enabled_swept") or []
    if swept:
        console.print(
            f"[green]✓[/green] swept the removed [b]enabled[/b] key off {len(swept)} slot(s): "
            f"[dim]{', '.join(str(s) for s in swept)}[/dim]"
        )

    reset = convergence.get("profile_reset") or {}
    outcome = reset.get("outcome")
    if outcome == "reset":
        backup = reset.get("backup")
        console.print(
            "[green]✓[/green] profile catalog reset to the v1.0 tuning-only shape"
            + (f" [dim](backup: {backup})[/dim]" if backup else "")
        )
    elif outcome == "declined":
        console.print(
            "[yellow]![/yellow] profile catalog NOT reset — "
            f"{len(reset.get('custom_profiles') or [])} operator profile(s) kept."
        )
    elif outcome == "error":
        console.print(f"[yellow]![/yellow] profile catalog reset failed: {reset.get('error')}")

    ownership = convergence.get("ownership_migrations") or {}
    pending = list(ownership.get("pending") or [])
    if not pending:
        if convergence.get("converged"):
            console.print("[dim]on-disk config is fully converged to the v1.0 shape.[/dim]")
        return bool(convergence.get("converged"))

    detail = ownership.get("detail") or {}
    lines: list[str] = []
    for key in pending:
        entry = detail.get(key) or {}
        lines.append(f"[bold]{key}[/bold] — [cyan]{entry.get('command')}[/cyan]")
        if entry.get("error"):
            lines.append(f"    [red]needs manual resolution:[/red] {entry['error']}")
        for line in list(entry.get("lines") or [])[:8]:
            lines.append(f"    [dim]{line}[/dim]")
        extra = len(entry.get("lines") or []) - 8
        if extra > 0:
            lines.append(f"    [dim]… and {extra} more[/dim]")
    console.print(
        Panel(
            "[bold yellow]This box is still on the pre-v1.0 slot/model shape.[/bold yellow]\n"
            "The new code is installed and running, but the migrations below have NOT been\n"
            "applied — until they are, per-slot launch tune, NGL/runner pins and\n"
            "mtp/vision/reasoning capabilities stay where the old schema put them and are\n"
            "no longer read at launch.\n\n"
            "They are not auto-run because each one refuses to rewrite slot TOMLs while\n"
            "hal0 is live — and the updater runs inside hal0-api. Stop hal0, then run:\n\n"
            + "\n".join(lines)
            + "\n\n[dim]Each is dry-run by default: drop --apply to preview. A timestamped\n"
            "backup of the slot config + registry DB is taken before any write.[/dim]",
            title="Convergence incomplete",
            border_style="yellow",
        )
    )
    return False


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


@update_app.callback(invoke_without_command=True)
def update(
    ctx: typer.Context,
    channel: UpdateChannel | None = typer.Option(
        None,
        "--channel",
        help="Persist the update channel (stable, preview, or nightly), then check.",
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
        help="Require the authenticated channel manifest to exactly match this version.",
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

    if channel is not None:
        try:
            api_put("/api/updates/channel", json={"channel": channel.value})
        except CliApiError as exc:
            die(str(exc))
            return
        console.print(f"[green]channel set to {channel.value}[/green]")

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
        # No new version — but a v1.0 profile-catalog reset may still be
        # outstanding (#1585): it runs in commit(), which on the 0.9.8→1.0
        # update ran under the old daemon that had no reset. Converge it here
        # rather than going silent.
        if _maybe_converge_profiles(body.get("profile_reset"), yes=yes):
            return
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

    # The one-shot v1.0 profile-catalog reset needs a decision from a human, and
    # this is the only process in the chain that has one. Asked AFTER the apply
    # confirm so a declined update never prompts about a wipe that will not run.
    reset_profiles = _decide_profile_reset(prepared.get("profile_reset"), yes=yes)

    commit_body: dict[str, object] = {"version": resolved}
    if reset_profiles is True:
        commit_body["reset_profiles"] = True
    try:
        cjob = api_post("/api/updates/commit", json=commit_body)
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
        if final.get("restarted") is False and final.get("restart_error"):
            console.print(
                f"[yellow]hal0-api restart did not complete:[/yellow] {final['restart_error']}"
            )
        # The unit files were re-rendered but slots were NOT bounced (a restart
        # could kill a mid-inference request). Surface which slots are still
        # running the pre-update command so the operator can opt into a restart.
        _print_drift_banner(_fetch_slot_drift())
        converged = _print_convergence(final.get("convergence"))
        if converged:
            console.print(Panel("[green]update applied.[/green]", border_style="green"))
            return
        # Refuse to report a clean success while the old on-disk shape survives:
        # the tree swapped, but the box is not the thing v1.0 promises. Exit 2 is
        # distinct from a failed update (die() → 1) so a wrapper can tell
        # "applied but not converged" from "did not apply".
        console.print(
            Panel(
                "[yellow]update applied, but this install is NOT fully converged.[/yellow]\n"
                "[dim]Run the commands above, then re-run `hal0 update` to re-check. "
                "(exit 2 = applied, convergence outstanding)[/dim]",
                border_style="yellow",
            )
        )
        raise typer.Exit(2)
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
