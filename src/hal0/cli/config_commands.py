"""hal0 config subcommands — thin HTTP client to the hal0 API."""

from __future__ import annotations

import contextlib
import json as jsonlib
import os
import stat
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, die
from hal0.config.paths import UPSTREAMS_TOML_MODE

#: Canonical mode for a freshly-seeded hal0.toml — matches perms.py's
#: PermRow for the file. No shared constant exists yet (perms.py inlines
#: the literal too); this comment is the cross-reference.
HAL0_TOML_SEED_MODE = 0o600

#: Canonical mode for a freshly-seeded providers.toml. save_providers_config's
#: atomic rewrite passes no explicit mode to write_toml_atomic, so
#: tempfile.mkstemp's own default (0600) is what "canonical" already means
#: for this file — matched here so the seed doesn't drift from the first
#: real write.
PROVIDERS_TOML_SEED_MODE = 0o600

app = typer.Typer(help="Inspect and manage hal0 configuration.")
console = Console()


class ConfigFile(StrEnum):
    """Which on-disk config file a `config show`/`config edit` targets.

    Mirrors the three files ``hal0 config validate`` already checks
    (hal0.toml, upstreams.toml, providers.toml) — before this, ``show``/
    ``edit`` were hardcoded to hal0.toml only, so a validate failure in
    upstreams.toml or providers.toml had no matching ``edit`` target.
    """

    hal0 = "hal0"
    upstreams = "upstreams"
    providers = "providers"


_CONFIG_FILENAMES: dict[ConfigFile, str] = {
    ConfigFile.hal0: "hal0.toml",
    ConfigFile.upstreams: "upstreams.toml",
    ConfigFile.providers: "providers.toml",
}


def _config_path(which: ConfigFile) -> Path:
    """Return the on-disk path for one of hal0's config files, honouring HAL0_HOME."""
    filename = _CONFIG_FILENAMES[which]
    base = os.environ.get("HAL0_HOME")
    if base:
        return Path(base) / "etc" / "hal0" / filename
    return Path("/etc/hal0") / filename


def _hal0_toml_path() -> Path:
    """Return the on-disk hal0.toml path, honouring HAL0_HOME."""
    return _config_path(ConfigFile.hal0)


def _seed_config_file(path: Path, content: str, mode: int) -> None:
    """Atomically create *path* with *content*, at its canonical *mode*.

    Mirrors ``hal0.config.loader.write_toml_atomic``'s fchmod-before-rename
    pattern (tempfile.mkstemp + os.fchmod on the owned descriptor +
    os.replace), but for the raw commented-TOML text `config edit` seeds a
    missing file with — that text isn't a TOML-encodable dict, so
    ``write_toml_atomic`` itself can't be reused directly. A bare
    ``Path.write_text`` would instead go through ``open()``'s 0644 default
    (mediated by the process umask), silently widening a file whose
    canonical mode is 0600/0640 the moment it's created via ``edit`` rather
    than via the API's own writer.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.fchmod(f.fileno(), mode)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, path)
        tmp_path = None  # rename succeeded; don't clean up in finally
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


@app.command("show")
def config_show(
    which: ConfigFile = typer.Argument(
        ConfigFile.hal0,
        help="Which config file to show: hal0 | upstreams | providers.",
        case_sensitive=False,
    ),
) -> None:
    """Print a hal0 config file as it exists on disk (default: hal0.toml)."""
    path = _config_path(which)
    if not path.exists():
        console.print(f"[dim]No config at {path}[/dim]")
        raise typer.Exit(0)
    # Translate PermissionError into a clear hint — pre-v0.1.3 installs
    # left /etc/hal0 mode 0700 in some umask-tightened environments,
    # which makes `hal0 config show` from a non-root shell explode with
    # a raw Python traceback.
    #
    # The remedy is sudo — deliberately NOT `chmod 0644`, which this hint used
    # to recommend. Every file under /etc/hal0 is either secret-bearing
    # (api.env, hal0.toml, slots/*.toml) or inventory the box has no reason to
    # publish to every local account (upstreams.toml, ADR-0002); telling an
    # operator to widen it is telling them to undo `hal0 doctor perms`, which
    # converges the mode straight back.
    #
    # Group membership is offered ONLY when this file actually grants group
    # read: hal0.toml is 0600 by design, so "join the hal0 group" would send an
    # operator through a re-login to hit the identical error.
    try:
        body = path.read_text()
    except PermissionError as exc:
        console.print(f"[red]Permission denied:[/red] {path}")
        group_readable = False
        with contextlib.suppress(OSError):
            group_readable = bool(path.stat().st_mode & stat.S_IRGRP)
        remedy = (
            "Re-run with [bold]sudo[/bold], or add yourself to the file's group "
            "([bold]sudo usermod -aG hal0 $USER[/bold], then re-login)."
            if group_readable
            else "Re-run with [bold]sudo[/bold] — this file is owner-only by design, "
            "so group membership will not help."
        )
        console.print(
            f"[dim]The config is owned by the service account. {remedy} Do not "
            "widen it with chmod — /etc/hal0 holds credentials and provider "
            "inventory, and `hal0 doctor perms` re-tightens it anyway.[/dim]"
        )
        raise typer.Exit(1) from exc
    console.print(
        Panel(
            Syntax(body, "toml", theme="ansi_dark", background_color="default"),
            title=str(path),
            border_style="cyan",
        )
    )


@app.command("edit")
def config_edit(
    which: ConfigFile = typer.Argument(
        ConfigFile.hal0,
        help="Which config file to edit: hal0 | upstreams | providers.",
        case_sensitive=False,
    ),
) -> None:
    """Open a hal0 config file in $EDITOR (default: hal0.toml; falls back to $VISUAL then 'vi')."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    path = _config_path(which)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if which == ConfigFile.hal0:
            _seed_config_file(
                path,
                "# hal0 configuration — see `hal0 config show` for the live shape.\n"
                "[meta]\nschema_version = 1\n\n"
                "[slots]\nport_range_start = 8081\nport_range_end = 8099\n",
                HAL0_TOML_SEED_MODE,
            )
        elif which == ConfigFile.upstreams:
            _seed_config_file(
                path,
                f"# hal0 {which.value} configuration — created by `hal0 config edit`.\n",
                UPSTREAMS_TOML_MODE,
            )
        else:
            _seed_config_file(
                path,
                f"# hal0 {which.value} configuration — created by `hal0 config edit`.\n",
                PROVIDERS_TOML_SEED_MODE,
            )
    try:
        subprocess.run([editor, str(path)], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        die(f"editor {editor!r} failed: {exc}")


@app.command("migrate")
def config_migrate() -> None:
    """Migrate hal0.toml forward to the latest config schema version.

    Reads ``meta.schema_version`` from the on-disk config, runs the
    registered migration chain in ``hal0.config.migrations`` up to the
    latest version, and atomically writes the result back only if the
    version actually advanced. If the config is already current (or
    absent), nothing is written and that is reported honestly.
    """
    from hal0.config.loader import hal0_config_file_lock
    from hal0.config.migrations import MigrationError, latest_version, run_migrations

    path = _hal0_toml_path()
    if not path.exists():
        console.print(f"[dim]No config at {path} - nothing to migrate.[/dim]")
        raise typer.Exit(0)

    # Cross-process serialization (#1721): the CLI can migrate hal0.toml while
    # hal0-api is live and serving a settings PUT, and this read → migrate →
    # write is precisely the shape that loses the other writer's section. Same
    # advisory lock the API's ``hal0_config_txn`` holds, taken across the whole
    # round-trip rather than only the write.
    with hal0_config_file_lock(path):
        _run_config_migration(path, latest_version(), run_migrations, MigrationError)


def _run_config_migration(path, target, run_migrations, MigrationError) -> None:  # type: ignore[no-untyped-def]
    """Body of ``hal0 config migrate``, run under the hal0.toml advisory lock."""
    import tomllib

    from hal0.config.loader import write_toml_atomic

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        die(f"could not read {path}: {exc}")
        return

    current = int((data.get("meta") or {}).get("schema_version", 1) or 1)

    if current >= target:
        console.print(
            f"[green]OK[/green] Config schema is up to date "
            f"(v{current}, latest v{target}) - nothing to migrate."
        )
        raise typer.Exit(0)

    try:
        migrated, new_version = run_migrations(data, target_version=target)
    except MigrationError as exc:
        die(f"migration failed: {exc}")
        return

    try:
        write_toml_atomic(path, migrated)
    except OSError as exc:
        die(f"could not write {path}: {exc}")
        return

    console.print(
        f"[green]OK[/green] Migrated config schema v{current} -> v{new_version} ({path})."
    )


@app.command("validate")
def config_validate() -> None:
    """Validate all config files against the current schema."""
    from hal0.config.loader import (
        load_hal0_config,
        load_providers_config,
        load_upstreams_config,
    )

    problems: list[str] = []
    try:
        load_hal0_config()
    except Exception as exc:
        problems.append(f"hal0.toml: {exc}")
    try:
        load_upstreams_config()
    except Exception as exc:
        problems.append(f"upstreams.toml: {exc}")
    try:
        load_providers_config()
    except Exception as exc:
        problems.append(f"providers.toml: {exc}")
    if problems:
        for p in problems:
            console.print(f"[red]✗[/red] {p}")
        raise typer.Exit(1)
    console.print("[green]✓[/green] All configs pass schema validation.")


@app.command("reload")
def config_reload() -> None:
    """Ask the running hal0 daemon to reload configs (re-reads TOMLs)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        from hal0.cli._shared import api_post

        api_post("/api/settings/reload")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print("[green]✓[/green] Reloaded.")


@app.command("hardware")
def config_hardware(
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Force a fresh hardware probe (POST /api/hardware/probe) instead "
        "of showing the cached payload. Equivalent to the deprecated `hal0 probe`.",
    ),
) -> None:
    """Show the cached hardware probe payload (or force a fresh one with --refresh)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        if refresh:
            from hal0.cli._shared import api_post

            hw = api_post("/api/hardware/probe")
        else:
            hw = api_get("/api/hardware")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        Panel(
            Syntax(
                jsonlib.dumps(hw, indent=2),
                "json",
                theme="ansi_dark",
                background_color="default",
            ),
            title="hardware (refreshed)" if refresh else "hardware",
            border_style="cyan",
        )
    )
