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
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape as rich_escape
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


def _bridge_client_hint(harness: str) -> str:
    """What to run for a key, naming a client the bridge will accept.

    ``hal0 bridge add <harness>`` only works when ``<harness>`` is a valid
    client name (:data:`hal0.bridge.keys.NAME_RE`) that is not reserved
    (:func:`hal0.bridge.keys.is_reserved_name`); otherwise the hint suggests a
    name that is, or asks for one.
    """
    from hal0.bridge.keys import is_reserved_name, valid_name

    def usable(name: str) -> bool:
        return valid_name(name) and not is_reserved_name(name)

    tail = (
        "then save the key it prints, alone, to a file and pass it with --bridge-key-file "
        "(or --bridge-key)"
    )
    if usable(harness):
        return (
            f"run `hal0 bridge add {harness} --scope memory` (or `hal0 bridge rotate "
            f"{harness}` if that client exists) {tail}"
        )
    base = "".join(c if c.isalnum() or c in "_-" else "-" for c in harness.lower()).strip("-_")
    suggestion = next(
        (n for n in (f"{base}-memory"[:32], f"agent-{base}"[:32]) if base and usable(n)), None
    )
    why = (
        "is reserved for hal0's own identities"
        if valid_name(harness) and is_reserved_name(harness)
        else "is not a valid bridge client name (lowercase letters, digits, '-' or '_', "
        "at most 32 characters)"
    )
    pick = (
        f"e.g. `hal0 bridge add {suggestion} --scope memory`"
        if suggestion
        else "`hal0 bridge add <name> --scope memory` with a name of your choosing"
    )
    return f"{harness!r} {why}, so add a client under another name: {pick}, {tail}"


def _resolve_bridge_key(
    section: dict[str, Any], url: str, harness: str, bridge_key: str | None
) -> str:
    """The key the harness presents to ``url``. Raises :class:`_NoBridgeKey`.

    A key passed in wins (refused when it is not a bridge key, or when the
    endpoint rejects it). Else a token already in the harness's section is
    reused when it is a bridge key, points at this endpoint and the endpoint
    accepts it. Only the shape ``hal0bk_<name>.<token>`` counts: the endpoint
    also answers 200 for the admin key, which must never be written into a
    file every harness reads. This command never mints or rotates a key, so an
    HTTP answer can at worst stop it, never cost a working key.
    """
    from hal0.bridge.keys import name_of
    from hal0.memory.compat_client import compat_accepts

    how_to = _bridge_client_hint(harness)
    if bridge_key:
        if name_of(bridge_key) is None:
            raise _NoBridgeKey(
                "the key passed in is not a bridge key (hal0bk_<name>.<token>); the admin "
                f"key or any other credential is never written here. {how_to}"
            )
        if compat_accepts(url, bridge_key) is False:
            raise _NoBridgeKey(
                f"{url} rejected the key passed in: it needs a bridge key with the 'memory' "
                "scope. (hal0-api also answers 401 when it cannot read its bridge store, or "
                "was started before [bridge] enabled was switched on: restart it and retry "
                f"if the key is right.) Otherwise {how_to}"
            )
        return bridge_key
    token = section.get("apiToken")
    if (
        isinstance(token, str)
        and token
        and name_of(token) is not None
        and section.get("apiUrl") == url
        and compat_accepts(url, token) is True
    ):
        return token
    raise _NoBridgeKey(f"no working bridge key for {harness!r} on {url}: {how_to}")


def _read_bridge_key_file(path: str) -> str:
    """The key in ``path`` (``-``: stdin), stripped. Raises ``ValueError``."""
    import sys

    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read --bridge-key-file {path}: {exc}") from exc
    key = text.strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError(f"--bridge-key-file {path} does not hold a single key")
    return key


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    """Write the shared client config atomically, at most 0600 (it holds a key).

    :func:`hal0.memory.compat_client.write_client_config`: writes through a
    symlink that stays in the home (as root in its own home, only a link root
    owns in directories only root can write), refuses one leaving it, never follows a
    symlinked temporary file, keeps the owner and never widens a mode. The
    keys keep their order."""
    from hal0.memory.compat_client import write_client_config

    write_client_config(path, data, home=Path.home(), max_mode=0o600)


_FLAT_SERVER_KEYS = ("serverMode", "apiUrl", "apiToken", "apiPort")


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


_BRIDGE_KEY_RE = re.compile(r"hal0bk_[A-Za-z0-9_-]+\.[A-Za-z0-9_.~+/=-]+")


def _masked_text(text: str, merged: dict[str, Any]) -> str:
    """``text`` (the upstream installer's output) with every token in
    ``merged`` and anything shaped like a bridge key replaced by ``***``."""
    tokens = [merged.get("apiToken")]
    harnesses = merged.get("harnesses")
    for section in harnesses.values() if isinstance(harnesses, dict) else ():
        if isinstance(section, dict):
            tokens.append(section.get("apiToken"))
    for token in sorted({t for t in tokens if isinstance(t, str) and t}, key=len, reverse=True):
        text = text.replace(token, "***")
    return _BRIDGE_KEY_RE.sub("***", text)


#: A harness name the upstream installer takes as one plain argument.
_HARNESS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _redacted(merged: dict[str, Any]) -> dict[str, Any]:
    """``merged`` with every token masked: the flat one and each harness's,
    not only the section this run wrote (the file holds other tools' keys)."""
    shown = jsonlib.loads(jsonlib.dumps(merged))
    if isinstance(shown.get("apiToken"), str):
        shown["apiToken"] = "***"
    harnesses = shown.get("harnesses")
    for section in harnesses.values() if isinstance(harnesses, dict) else ():
        if isinstance(section, dict) and section.get("apiToken"):
            section["apiToken"] = "***"
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
        "/api/memory/compat. From `hal0 bridge add <name> --scope memory`. Visible to "
        "every user in the process list while this runs: prefer --bridge-key-file.",
    ),
    bridge_key_file: str | None = typer.Option(
        None,
        "--bridge-key-file",
        help="Read the bridge key from this file, or from stdin when it is '-'. The "
        "file holds only the key (hal0bk_...), nothing else.",
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
    ``--bridge-key-file`` (``-`` reads stdin; it holds only the key) or
    ``--bridge-key``, or one
    already in that section that the endpoint accepts; this command never
    mints or rotates one.
    """
    from hal0.memory.compat_client import compat_url

    if not _HARNESS_RE.fullmatch(harness):
        die(
            f"{harness!r} is not a harness name: use letters, digits, '.', '_' or '-', "
            "starting with a letter or digit, at most 64 characters"
        )
        return
    if bridge_key and bridge_key_file:
        die("pass --bridge-key or --bridge-key-file, not both")
        return
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
        if bridge_key_file:
            bridge_key = _read_bridge_key_file(bridge_key_file)
        data = _read_config(config_path)
        key = _resolve_bridge_key(_harness_section(data, harness), url, harness, bridge_key)
    except (ValueError, _NoBridgeKey) as exc:
        die(str(exc))
        return

    try:
        # stdin from /dev/null: with stdout captured the installer never prompts
        # (it asks only when fds 0 and 1 are both terminals), and a closed stdin
        # keeps it that way under a pty wrapper too.
        proc = subprocess.run(
            shlex.split(command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        die(f"failed to run installer: {exc}")
        return
    if proc.returncode != 0:
        out = _masked_text(proc.stderr.strip() or proc.stdout.strip(), data)
        die(f"installer exited {proc.returncode}: {_masked_text(out, {'apiToken': key})}")
        return

    # Re-read: the upstream installer may have written the file. Given no
    # --server and a file with no flat serverMode/apiUrl, it may carry an old
    # per-harness plugin's endpoint into the flat keys; say so, since every
    # harness without its own section reads them.
    try:
        after = _read_config(config_path)
        carried = [k for k in _FLAT_SERVER_KEYS if after.get(k) != data.get(k)]
        if carried:
            typer.echo(
                f"note: the upstream installer changed the flat {', '.join(carried)} in "
                f"{config_path}; harnesses.{harness} is pointed at hal0 either way",
                err=True,
            )
        merged = _merge_coding_agent_config(after, harness, url=url, key=key)
        _write_private_json(config_path, merged)
    except (ValueError, OSError) as exc:
        die(f"could not write {config_path}: {exc}")
        return
    shown = _redacted(merged)
    installer_out = _masked_text(proc.stdout or "", merged)
    if json_out:
        typer.echo(
            jsonlib.dumps(
                {
                    "command": command,
                    "config_path": str(config_path),
                    "config": shown,
                    "stdout": installer_out,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    console.print(
        Panel(
            rich_escape(installer_out) or "[dim](no output)[/dim]", title=f"installer · {harness}"
        )
    )
    console.print(
        Panel(jsonlib.dumps(shown, indent=2, sort_keys=True), title=f"wrote {config_path}")
    )


__all__ = ["agents_app", "models_app"]
