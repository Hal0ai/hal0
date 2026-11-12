"""``hal0 memory models`` and ``hal0 memory agents`` — showcase console CLI
parity for ``/api/memory/models`` and ``/api/memory/agents``.

``agents install`` runs the upstream ``hindsight-coding-agents`` installer and
then sets the harness's own section of ``~/.hindsight/coding-agent.json``
(``harnesses.<harness>``): hal0's ``/api/memory/compat`` and a bridge key
(ADR-0058 decision 4b), plus the showcase defaults (``pageTriggerType:
after-consolidation``, ``observationScopes: shared``, ``retainTags:
[agent:<harness>]``). The flat keys and other harnesses are left alone.
"""

from __future__ import annotations

import json as jsonlib
import shlex
import subprocess
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, die

models_app = typer.Typer(help="Inspect the configured embeddings/reranker/decision models.")
agents_app = typer.Typer(help="Install and configure coding-agent memory integrations.")
console = Console()

_HINDSIGHT_COMMAND = "npx @vectorize-io/hindsight-coding-agents@0.8.5"


def _require_api() -> str:
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    return url


@models_app.command("show", help="Show the configured embeddings/reranker/decision models.")
@models_app.callback(invoke_without_command=True)
def models_cmd(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """``hal0 memory models`` — print the active embeddings/reranker/decision config."""
    if ctx.invoked_subcommand is not None:
        return
    _require_api()
    try:
        result = api_get("/api/memory/models")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    t = Table(title="memory · models")
    t.add_column("role")
    t.add_column("provider")
    t.add_column("model")
    t.add_column("status")
    emb = result.get("embeddings", {})
    rer = result.get("reranker", {})
    dec = result.get("decision", {})
    # #3518: the engine runs the embedder the store was built with
    # ("recorded"), which can differ from the configured provider and model.
    emb_provider = str(emb.get("recorded_provider") or emb.get("provider", "—"))
    emb_model = str(emb.get("recorded") or emb.get("model", "—"))
    emb_notes: list[str] = []
    recorded = (emb.get("recorded_provider"), emb.get("recorded"))
    configured = (emb.get("configured_provider"), emb.get("configured"))
    if emb.get("recorded") and recorded != configured:
        wanted = emb.get("configured")
        if emb.get("recorded_provider") != emb.get("configured_provider"):
            wanted = f"{emb.get('configured_provider')}/{wanted}"
        emb_notes.append(
            f"configured: {wanted} (not applied; `hal0 memory embedder switch` moves the store)"
        )
    if emb.get("mixed"):
        emb_notes.append(
            "store may hold vectors from two embedders; `hal0 memory embedder switch` repairs it"
        )
    if emb.get("conflict"):
        emb_notes.append("record and engine disagree, see hal0 doctor")
    emb_status = f"[yellow]{'; '.join(emb_notes)}[/yellow]" if emb_notes else ""
    t.add_row("embeddings", emb_provider, emb_model, emb_status)
    t.add_row("reranker", str(rer.get("provider", "—")), str(rer.get("model", "—")), "")
    status = "[green]reachable[/green]" if dec.get("reachable") else "[red]unreachable[/red]"
    t.add_row(
        "decision",
        str(dec.get("provider", "—")),
        str(dec.get("model", "—")),
        status if dec.get("enabled") else "[dim]disabled[/dim]",
    )
    console.print(t)


def _hindsight_config_path() -> Path:
    return Path.home() / ".hindsight" / "coding-agent.json"


class _NoBridgeKey(Exception):
    """No usable key for the harness: the message says what to run."""


def _read_config(path: Path) -> dict[str, Any]:
    """The shared client config, ``{}`` when absent. Raises ``ValueError`` for a
    file that is not a JSON object (it is shared state: never replaced)."""
    try:
        data = jsonlib.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (jsonlib.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{path} cannot be read as JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _harness_section(data: dict[str, Any], harness: str) -> dict[str, Any]:
    harnesses = data.get("harnesses", {})
    section = harnesses.get(harness, {}) if isinstance(harnesses, dict) else None
    if not isinstance(harnesses, dict) or not isinstance(section, dict):
        raise ValueError(f"'harnesses.{harness}' in the client config is not an object")
    return section


def _resolve_bridge_key(
    section: dict[str, Any], url: str, harness: str, bridge_key: str | None
) -> str:
    """The key the harness presents to ``url``. Raises :class:`_NoBridgeKey`.

    ``--bridge-key`` wins (refused only when the endpoint rejects it). Else a
    token already in the harness's section is reused when it points at this
    endpoint and the endpoint accepts it. This command never mints a key.
    """
    from hal0.memory.compat_client import compat_accepts

    how_to = (
        f"run `hal0 bridge add {harness} --scope memory` (or `hal0 bridge rotate {harness}` "
        f"if that client exists) and pass the key it prints with --bridge-key"
    )
    if bridge_key:
        if compat_accepts(url, bridge_key) is False:
            raise _NoBridgeKey(
                f"{url} rejected the key passed with --bridge-key: it needs a bridge key "
                f"with the 'memory' scope; {how_to}"
            )
        return bridge_key
    token = section.get("apiToken")
    if (
        isinstance(token, str)
        and token
        and section.get("apiUrl") == url
        and compat_accepts(url, token) is True
    ):
        return token
    raise _NoBridgeKey(f"no working bridge key for {harness!r} on {url}: {how_to}")


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    """Write the shared client config atomically, at most 0600 (it holds a key).

    Writes through a symlink, keeps the owner and never widens an existing
    mode."""
    import contextlib
    import os
    import stat

    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        st: os.stat_result | None = target.stat()
    except FileNotFoundError:
        st = None
    mode = 0o600 if st is None else stat.S_IMODE(st.st_mode) & 0o600
    tmp = target.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (jsonlib.dumps(data, indent=2, sort_keys=True) + "\n").encode())
        os.fchmod(fd, mode)
        if st is not None:
            with contextlib.suppress(PermissionError):
                os.fchown(fd, st.st_uid, st.st_gid)
    finally:
        os.close(fd)
    tmp.replace(target)


def _merge_coding_agent_config(
    data: dict[str, Any], harness: str, *, url: str, key: str
) -> dict[str, Any]:
    """``data`` with the harness's own section set: the showcase defaults and
    hal0's memory endpoint with its key. The flat keys and every other
    harness's section are left as they are (the file is shared)."""
    harnesses = dict(data.get("harnesses") or {})
    section = dict(harnesses.get(harness) or {})
    retain_tags = set(section.get("retainTags") or [])
    retain_tags.add(f"agent:{harness}")
    section.update(
        {
            "serverMode": "self-hosted",
            "apiUrl": url,
            "apiToken": key,
            "pageTriggerType": "after-consolidation",
            "observationScopes": "shared",
            "retainTags": sorted(retain_tags),
        }
    )
    harnesses[harness] = section
    return {**data, "harnesses": harnesses}


def _redacted(merged: dict[str, Any], harness: str) -> dict[str, Any]:
    shown = jsonlib.loads(jsonlib.dumps(merged))
    section = shown.get("harnesses", {}).get(harness, {})
    if section.get("apiToken"):
        section["apiToken"] = "***"
    if isinstance(shown.get("apiToken"), str):
        shown["apiToken"] = "***"
    return shown


@agents_app.command("install")
def agents_install_cmd(
    harness: str = typer.Argument(
        ..., help="Coding-agent harness name (e.g. claude-code, cursor, codex)."
    ),
    bridge_key: str | None = typer.Option(
        None,
        "--bridge-key",
        help="Bridge key (hal0bk_...) with the 'memory' scope the harness presents to "
        "/api/memory/compat. From `hal0 bridge add <name> --scope memory`.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the installer command and config merge without running/writing.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Install a coding-agent's Hindsight memory integration, pointed at hal0.

    The harness's section of ``~/.hindsight/coding-agent.json``
    (``harnesses.<harness>``) gets ``apiUrl`` = hal0's ``/api/memory/compat``
    and ``apiToken`` = a bridge key, so its memory goes through hal0's write
    envelope and project-scoped reads instead of straight to the engine. The
    flat keys and the other harnesses are left alone. The key is
    ``--bridge-key``, or one already in that section that the endpoint
    accepts; this command never mints one.
    """
    from hal0.memory.compat_client import compat_url

    api = _require_api()
    # No --server / --api-url: given those, the upstream installer writes the
    # engine's address into the flat ``apiUrl`` every harness reads. The
    # harness's address is set below, in its own section.
    command = f"{_HINDSIGHT_COMMAND} install {shlex.quote(harness)}"

    config_path = _hindsight_config_path()
    url = compat_url(api)
    if dry_run:
        payload = {
            "command": command,
            "config_path": str(config_path),
            "api_url": url,
            "dry_run": True,
        }
        if json_out:
            typer.echo(jsonlib.dumps(payload, indent=2, sort_keys=True))
        else:
            console.print(
                Panel(
                    f"[yellow]Would run:[/yellow] {command}\n[yellow]Would write:[/yellow] "
                    f"{config_path} (harnesses.{harness}.apiUrl = {url})"
                )
            )
        return

    # The key is settled before anything runs, so a missing one changes nothing.
    try:
        data = _read_config(config_path)
        key = _resolve_bridge_key(_harness_section(data, harness), url, harness, bridge_key)
    except (ValueError, _NoBridgeKey) as exc:
        die(str(exc))
        return

    try:
        proc = subprocess.run(shlex.split(command), capture_output=True, text=True, check=False)
    except OSError as exc:
        die(f"failed to run installer: {exc}")
        return
    if proc.returncode != 0:
        die(f"installer exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}")
        return

    # Re-read: the upstream installer may have written the file.
    try:
        merged = _merge_coding_agent_config(_read_config(config_path), harness, url=url, key=key)
        _write_private_json(config_path, merged)
    except (ValueError, OSError) as exc:
        die(f"could not write {config_path}: {exc}")
        return
    shown = _redacted(merged, harness)
    if json_out:
        typer.echo(
            jsonlib.dumps(
                {
                    "command": command,
                    "config_path": str(config_path),
                    "config": shown,
                    "stdout": proc.stdout,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    console.print(Panel(proc.stdout or "[dim](no output)[/dim]", title=f"installer · {harness}"))
    console.print(
        Panel(jsonlib.dumps(shown, indent=2, sort_keys=True), title=f"wrote {config_path}")
    )


__all__ = ["agents_app", "models_app"]
