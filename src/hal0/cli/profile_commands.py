"""``hal0 profile`` subcommands — thin CLI over ``/api/profiles``.

``list`` / ``show`` cover the read surface an operator needs to answer "what
profiles exist" and "what does this one resolve to". ``create`` / ``edit``
write a custom profile through the same endpoints (and so the same
``ProfileCatalog`` + flag/window screens) the dashboard uses. A profile owns
every tune a slot launches with (ADR-0029): ``--flags``, ``--ctx-size``,
``--thinking``, ``--vision``, ``--speculation``, ``--runner``. Seed profiles are
read-only: ``create <new> --from <seed>`` duplicates one, then ``edit`` the
copy and point a slot at it (``hal0 slot edit <slot> --profile <new>``).
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import typer
from rich.console import Console
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
from hal0.config.schema import CTX_SIZE_MAX, CTX_SIZE_MIN

app = typer.Typer(
    name="profile",
    help="Inspect and edit profiles — the tune a slot launches a model with.",
    no_args_is_help=True,
)

console = Console()


def _tri(value: Any) -> str:
    """Render a tri-state bool: unset is "—", not "no"."""
    return "—" if value is None else ("on" if value else "off")


@app.command("list")
def profile_list(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/profiles JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """List every profile in the catalog (GET /api/profiles)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        profiles = api_get("/api/profiles")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(profiles, indent=2))
        return
    if not isinstance(profiles, list) or not profiles:
        console.print("[dim]No profiles available.[/dim]")
        return
    table = Table(title=f"Profiles ({len(profiles)})")
    table.add_column("Name", style="bold")
    table.add_column("Ctx", justify="right")
    table.add_column("Thinking")
    table.add_column("Vision")
    table.add_column("Speculation")
    table.add_column("Runner")
    table.add_column("Intent")
    table.add_column("tok/s", justify="right")
    table.add_column("Used by")
    for p in profiles:
        if not isinstance(p, dict):
            continue
        tps = p.get("tps")
        used_by = p.get("used_by") or []
        if p.get("quarantined"):
            table.add_row(
                f"[dim]{p.get('name', '—')}[/dim]",
                "[red]quarantined[/red]",
                f"[dim]{p.get('error') or 'invalid as stored'}[/dim]",
                "",
                "",
                "",
                "",
                "",
                ", ".join(used_by) if used_by else "—",
            )
            continue
        table.add_row(
            p.get("name", "—"),
            f"{p['ctx_size']:,}" if isinstance(p.get("ctx_size"), int) else "native",
            _tri(p.get("enable_thinking")),
            str(p.get("vision") or "auto"),
            str(p.get("speculation") or "auto"),
            p.get("runner") or "—",
            p.get("intent") or "—",
            f"{tps:.1f}" if isinstance(tps, (int, float)) else "—",
            ", ".join(used_by) if used_by else "—",
        )
    console.print(table)


@app.command("show")
def profile_show(
    name: str = typer.Argument(..., help="Profile name to inspect"),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/profiles/{name} JSON for CI/pipe use (no Rich panel).",
    ),
) -> None:
    """Resolve a single profile by name (GET /api/profiles/{name})."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        profile = api_get(f"/api/profiles/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(profile, indent=2))
        return
    if not isinstance(profile, dict):
        die(f"unexpected response for profile {name!r}")
        return
    for key in (
        "name",
        "seed",
        "ctx_size",
        "enable_thinking",
        "vision",
        "speculation",
        "runner",
        "cloned_from",
        "intent",
        "tps",
        "rtf",
    ):
        if key in profile:
            console.print(f"[bold]{key}[/bold]: {profile[key]}")
    used_by = profile.get("used_by") or []
    console.print(f"[bold]used_by[/bold]: {', '.join(used_by) if used_by else '—'}")
    flags = profile.get("resolved_flags") or profile.get("flags")
    if flags:
        console.print(f"[bold]flags[/bold]: {flags}")


_ONOFF = {"auto", "off"}


_SPECULATION_MODES = ("auto", "off", "ngram")
_MTP_RENAMED = "profile 'mtp' is now 'speculation' (auto | off | ngram)"


def _check_speculation(value: str | None) -> str | None:
    if value is not None and value not in _SPECULATION_MODES:
        raise typer.BadParameter(f"--speculation must be 'auto', 'off' or 'ngram' (got {value!r})")
    return value


def _refuse_mtp(value: str | None) -> None:
    """``--mtp`` is gone (ADR-0031 §7): refuse it with the rename, exit 2."""
    if value is not None:
        typer.echo(f"Error: {_MTP_RENAMED}", err=True)
        raise typer.Exit(code=2)


def _check_auto_off(option: str, value: str | None) -> str | None:
    if value is not None and value not in _ONOFF:
        raise typer.BadParameter(f"{option} must be 'auto' or 'off' (got {value!r})")
    return value


@app.command("create")
def profile_create(
    name: str = typer.Argument(..., help="New profile name (kebab-case, <=32 chars)."),
    from_profile: str | None = typer.Option(
        None,
        "--from",
        help="Duplicate this profile (a seed or a custom one) and apply the other options on top.",
    ),
    flags: str | None = typer.Option(
        None,
        "--flags",
        help="Free llama-server flags (KV cache types, batch sizes, sampling, ...).",
    ),
    ctx_size: int | None = typer.Option(
        None,
        "--ctx-size",
        min=CTX_SIZE_MIN,
        max=CTX_SIZE_MAX,
        help=(
            f"Context window ({CTX_SIZE_MIN}-{CTX_SIZE_MAX}). Unset = the model's native "
            "window, capped at 32768 (8192 when unknown)."
        ),
    ),
    thinking: bool | None = typer.Option(
        None,
        "--thinking/--no-thinking",
        help="Request-time reasoning default for slots running this profile.",
    ),
    vision: str | None = typer.Option(
        None, "--vision", help="'auto' loads the model's mmproj when it has one; 'off' never does."
    ),
    speculation: str | None = typer.Option(
        None,
        "--speculation",
        help=(
            "'auto' speculates with the model's drafter or MTP heads when it has "
            "them; 'ngram' uses n-gram lookup; 'off' never."
        ),
    ),
    mtp: str | None = typer.Option(None, "--mtp", hidden=True, help="Removed: use --speculation."),
    runner: str | None = typer.Option(None, "--runner", help="Runner key this profile applies."),
    intent: str | None = typer.Option(None, "--intent", help="Human label for the profile."),
    json_out: bool = typer.Option(False, "--json", help="Emit the created profile as JSON."),
) -> None:
    """Create a custom profile (POST /api/profiles); ``--from`` duplicates one first."""
    _check_auto_off("--vision", vision)
    _refuse_mtp(mtp)
    _check_speculation(speculation)
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    body: dict[str, Any] = {"name": name}
    if from_profile:
        try:
            source = api_get(f"/api/profiles/{from_profile}")
        except CliApiError as exc:
            die(str(exc))
            return
        for key in (
            "flags",
            "ctx_size",
            "enable_thinking",
            "vision",
            "speculation",
            "runner",
            "intent",
        ):
            if isinstance(source, dict) and source.get(key) is not None:
                body[key] = source[key]
        body["cloned_from"] = from_profile
    for key, value in (
        ("flags", flags),
        ("ctx_size", ctx_size),
        ("enable_thinking", thinking),
        ("vision", vision),
        ("speculation", speculation),
        ("runner", runner),
        ("intent", intent),
    ):
        if value is not None:
            body[key] = value

    try:
        created = api_post("/api/profiles", json=body)
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(created, indent=2))
        return
    console.print(f"Created profile [bold]{name}[/bold]")


@app.command("edit")
def profile_edit(
    name: str = typer.Argument(..., help="Custom profile to edit."),
    flags: str | None = typer.Option(None, "--flags", help="Replace the llama-server flags."),
    ctx_size: int | None = typer.Option(
        None,
        "--ctx-size",
        min=CTX_SIZE_MIN,
        max=CTX_SIZE_MAX,
        help=f"Context window ({CTX_SIZE_MIN}-{CTX_SIZE_MAX}).",
    ),
    clear_ctx_size: bool = typer.Option(
        False, "--clear-ctx-size", help="Unset the window: follow the model's native one."
    ),
    thinking: bool | None = typer.Option(
        None, "--thinking/--no-thinking", help="Request-time reasoning default."
    ),
    clear_thinking: bool = typer.Option(
        False, "--clear-thinking", help="Unset the reasoning default."
    ),
    vision: str | None = typer.Option(None, "--vision", help="'auto' | 'off'."),
    speculation: str | None = typer.Option(None, "--speculation", help="'auto' | 'off' | 'ngram'."),
    mtp: str | None = typer.Option(None, "--mtp", hidden=True, help="Removed: use --speculation."),
    runner: str | None = typer.Option(
        None, "--runner", help="Runner key; pass '' to clear (back to the slot's own runner)."
    ),
    intent: str | None = typer.Option(None, "--intent", help="Human label for the profile."),
    json_out: bool = typer.Option(False, "--json", help="Emit the updated profile as JSON."),
) -> None:
    """Edit a custom profile (PUT /api/profiles/{name}); omitted options keep their value."""
    _check_auto_off("--vision", vision)
    _refuse_mtp(mtp)
    _check_speculation(speculation)
    if ctx_size is not None and clear_ctx_size:
        raise typer.BadParameter("--ctx-size and --clear-ctx-size are mutually exclusive")
    if thinking is not None and clear_thinking:
        raise typer.BadParameter("--thinking/--no-thinking and --clear-thinking are exclusive")

    payload: dict[str, Any] = {}
    if flags is not None:
        payload["flags"] = flags
    if ctx_size is not None:
        payload["ctx_size"] = ctx_size
    if clear_ctx_size:
        payload["ctx_size"] = None
    if thinking is not None:
        payload["enable_thinking"] = thinking
    if clear_thinking:
        payload["enable_thinking"] = None
    if vision is not None:
        payload["vision"] = vision
    if speculation is not None:
        payload["speculation"] = speculation
    if runner is not None:
        payload["runner"] = runner
    if intent is not None:
        payload["intent"] = intent
    if not payload:
        console.print(
            "[bold yellow]No fields provided.[/bold yellow]  Pass at least one of --flags, "
            "--ctx-size/--clear-ctx-size, --thinking/--no-thinking/--clear-thinking, "
            "--vision, --speculation, --runner, --intent."
        )
        raise typer.Exit(code=2)

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        current = api_get(f"/api/profiles/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    if isinstance(current, dict) and current.get("seed"):
        die(
            f"profile {name!r} is a seed profile and is read-only. Duplicate it first: "
            f"`hal0 profile create <new-name> --from {name}`, edit the copy, then "
            f"`hal0 slot edit <slot> --profile <new-name>`."
        )
        return
    try:
        updated = api_put(f"/api/profiles/{name}", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(updated, indent=2))
        return
    console.print(
        f"Updated profile [bold]{name}[/bold]; slots running it pick the change up at their "
        "next restart."
    )


def _running_as_root() -> bool:
    import os

    return hasattr(os, "geteuid") and os.geteuid() == 0


@app.command("migrate")
def profile_migrate(
    apply: bool = typer.Option(False, "--apply", help="Convert this install (backs up first)."),
    restore: str | None = typer.Option(
        None,
        "--restore",
        metavar="ARCHIVE",
        help="Put a profiles-<ts>.tar.gz backup back (for a downgrade; stop hal0-api first).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
) -> None:
    """Move this install to profile-centric config (ADR-0029).

    Without flags it previews: which profiles would be generated, which slots
    re-point, which keys leave the slots, and every value that is inert today
    (those are dropped, never applied: the conversion keeps what launches).
    ``--apply`` converts (``hal0 update`` and ``hal0 serve`` do this
    automatically). Exits 2 when anything cannot be converted (a slot, a
    stored profile or a stack).
    """
    from hal0.config.migrations.profile_centric import ProfileMigrationError
    from hal0.config.migrations.profile_centric_apply import (
        ProfileConversionError,
        migrate_profile_centric,
        restore_backup,
    )

    if (apply or restore) and _running_as_root():
        # Run as root, the backup would be a root-owned 0600 file that hal0-api
        # (and so `hal0 update --rollback`) cannot read.
        flag = "--apply" if apply else f"--restore {restore}"
        die(
            f"run this as the hal0 service user, not root: `sudo -u hal0 hal0 profile migrate "
            f"{flag}`"
        )
        return

    if restore:
        try:
            restored = restore_backup(restore)
        except ProfileConversionError as exc:
            die(str(exc))
            return
        for line in restored:
            typer.echo(f"restored {line}")
        return
    try:
        report = migrate_profile_centric(dry_run=not apply)
    except (ProfileMigrationError, ProfileConversionError, OSError) as exc:
        die(str(exc))
        return
    plan = report.plan
    if json_out:
        payload = plan.to_dict() if plan is not None else {}
        payload["backup"] = report.backup
        typer.echo(jsonlib.dumps(payload, indent=2, default=str))
    else:
        for line in report.lines():
            typer.echo(line)
        if plan is not None and plan.blockers:
            typer.echo(
                f"{len(plan.blockers)} item(s) cannot be converted "
                f"({', '.join(plan.blockers)}); see BLOCKED above"
            )
    if plan is not None and plan.blockers:
        raise typer.Exit(2)


__all__ = ["app"]
