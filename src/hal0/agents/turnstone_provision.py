"""Turnstone bundled-agent bootstrap pipeline.

Turnstone (github.com/turnstonelabs/turnstone) is a self-hosted
agent-orchestration platform distributed as a **Python package on PyPI**
(console scripts ``turnstone`` + ``turnstone-server``). It does NOT host
models — it consumes an OpenAI-compatible backend (``[api] base_url``),
mounts external tools over MCP, and has its own memory + LLM-judge layer.
hal0 already exposes exactly that surface: the hal0-api gateway at
``/v1`` plus the ``hal0-memory`` / ``hal0-admin`` MCP mounts and Honcho
memory. This pipeline wires turnstone onto those seams.

Modeled on :mod:`hal0.agents.hermes_provision` but built on the shared,
agent-agnostic :mod:`hal0.agents.provision_engine` (checkpointing,
skip-if-ok, ``--repair``, fatal-abort, needs-graph validation). The
hal0-facing helpers (slot fetch, chat-slot classification, MCP
probe/call) are reused by import from ``hermes_provision``.

Deploy shape (this pass): turnstone is ``pip install``-ed into a managed
venv at ``/var/lib/hal0/venvs/turnstone`` (exactly like hermes-agent),
with ``turnstone`` / ``turnstone-server`` shimmed onto PATH.
``hal0-agent@turnstone.service`` runs ``turnstone-server`` bound loopback
on :9129 — the :9129 choice mirrors hermes's :9119 convention and dodges
the :8080 collision with hal0-api. Memory is SQLite (no Postgres
coupling); the Postgres-backed multi-node server stack is a documented
follow-up.

State lives at ``/var/lib/hal0/state/agents/turnstone/provision.json`` —
outside ``TURNSTONE_HOME`` so an upstream ``turnstone`` reset can't
trample hal0's bookkeeping.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess  # nosec B404 — needed to run the installer script + smoke exec
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import structlog

from hal0.agents import provision_engine as engine

# Generic hal0-facing helpers — reused by import (not hermes-specific: they
# hit the local hal0-api gateway + MCP mounts). Kept DRY rather than copied.
from hal0.agents.hermes_provision import (
    HAL0_API_URL,
    MIN_FREE_GIB,
    _collect_chat_slots,
    _fetch_model_contexts,
    _fetch_slots,
    _http_get,
    _mcp_memory_call,
    _probe_mcp_server,
    path_is_writable,
)
from hal0.agents.provision_engine import (
    BootstrapState,
    Phase,
    PhaseContext,
    PhaseResult,
    PhaseStatus,
    RunResult,
)

log = structlog.get_logger(__name__)

AGENT_ID = "turnstone"

# ── Canonical on-disk layout ─────────────────────────────────────────────────
# All module-level so tests can monkey-patch onto a tmp_path, same posture as
# hermes_provision's constants.
TURNSTONE_HOME = Path("/var/lib/hal0/.turnstone")
TURNSTONE_CONFIG_PATH = TURNSTONE_HOME / "config.toml"
MCP_SERVERS_JSON = TURNSTONE_HOME / "mcp-servers.json"
PERSONA_PATH = TURNSTONE_HOME / "persona.txt"
# Turnstone is a PyPI package (console scripts `turnstone` + `turnstone-server`),
# so hal0 installs it into a dedicated managed venv exactly like hermes-agent —
# NOT as a standalone Go binary. MANAGED_BIN is the CLI entry point;
# SERVER_BIN is what the systemd unit runs.
VENV = Path("/var/lib/hal0/venvs/turnstone")
MANAGED_BIN = VENV / "bin" / "turnstone"
SERVER_BIN = VENV / "bin" / "turnstone-server"
CLI_SHIM = Path("/usr/local/bin/turnstone")
DATA_DIR = Path("/var/lib/hal0/agents/turnstone")
SQLITE_DB = DATA_DIR / "turnstone.db"
STATE_ROOT = Path("/var/lib/hal0/state/agents/turnstone")

# Install artifacts the manager + driver key off (mirror hermes #432).
INSTALL_SEED_PATH = Path("/etc/hal0/agents/turnstone.toml")
DRIVER_ENV_PATH = Path("/etc/hal0/agents/turnstone.env")
# Outbound secrets vault — root:root 0600, referenced by the systemd
# EnvironmentFile; NEVER written into the world-readable config.toml.
SECRETS_ENV_PATH = Path("/var/lib/hal0/secrets/agents/turnstone.env")

# hal0-managed marker stamped into TURNSTONE_HOME so uninstall/reprovision
# won't trample a home hal0 doesn't own (mirror hermes _claim_hermes_home).
MANAGED_MARKER = ".hal0-managed"

# Loopback server bind. :9129 mirrors hermes :9119; avoids hal0-api :8080.
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9129

# Honcho / hal0-memory identity for turnstone's own memory bank. NAMESPACE is
# what turnstone's OWN config references; the hal0-memory REST shim groups
# identity cards by ``dataset`` (the `agents` dataset, matching hermes) with a
# required ``text`` field — NOT namespace/content.
NAMESPACE = f"private:{AGENT_ID}"
MEMORY_DATASET = "agents"
# RESERVED — the future hal0-brain re-home identity. Documented so a later
# pass can move the dashboard steward onto turnstone with a one-line swap;
# NOT registered this pass (hal0-brain stays on hermes → hermes__hal0-brain).
BRAIN_PROFILE_AGENT_ID_RESERVED = "turnstone__hal0-brain"

# hal0-api MCP mounts (LAN-reachable surfaces, same ones hermes/opencode use).
MEMORY_MCP_PATH = "/mcp/memory"
ADMIN_MCP_PATH = "/mcp/admin"

# Dev sentinel: hal0-api runs auth-disabled by default but turnstone still
# treats the provider as key-requiring, so we always export something.
_DEV_API_KEY = "hal0-local"

# Default model anchor (ADR-0023 canonical). model_automap maps the live
# chat slots onto turnstone model aliases; this is the guaranteed fallback.
_DEFAULT_MODEL_ALIAS = "agent"


def _api_base() -> str:
    """Honour HAL0_API_URL the way the CLI does; default to the loopback bind."""
    return os.environ.get("HAL0_API_URL", HAL0_API_URL).rstrip("/")


def installer_script() -> Path:
    """Absolute path to ``installer/agents/turnstone.sh`` (both layouts)."""
    from hal0.agents.hermes_provision import REPO_ROOT_FOR_INSTALLER

    return REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "turnstone.sh"


# ── State ────────────────────────────────────────────────────────────────────


@dataclass
class TurnstoneState(BootstrapState):
    """Turnstone's provision.json shape — the engine base + turnstone fields."""

    agent_id: str = AGENT_ID
    turnstone_version: str | None = None
    turnstone_home: str = str(TURNSTONE_HOME)
    binary_path: str = str(MANAGED_BIN)
    db_path: str = str(SQLITE_DB)


# ── IO seams ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnstoneIO:
    """The external touchpoints a turnstone phase may use — the monkeypatch
    tax, typed. Defaults bind the real (import-reused) implementations, so a
    default-constructed ``TurnstoneIO`` IS production behaviour; tests pass
    fakes instead of monkeypatching module globals."""

    http_get: Callable[..., int] = _http_get
    fetch_slots: Callable[[], list[dict[str, Any]]] = _fetch_slots
    fetch_model_contexts: Callable[[], dict[str, int]] = _fetch_model_contexts
    probe_mcp_server: Callable[..., dict[str, Any]] = _probe_mcp_server
    mcp_memory_call: Callable[..., dict[str, Any]] = _mcp_memory_call
    run: Callable[..., Any] = subprocess.run


# ── Config builders (pure — unit-testable without IO) ────────────────────────


def _persona_text() -> str:
    """The turnstone ``[session] instructions`` system prompt.

    Deliberately distinct from hal0-brain's steward persona (that stays on
    hermes this pass). Turnstone is framed as a tool-using orchestrator that
    routes to hal0's local models and honours hal0's approval posture.
    """
    return (
        "You are the hal0 turnstone agent — a tool-using orchestrator running "
        "on a self-hosted hal0 home. Your models are served locally by the "
        "hal0-api gateway; never assume a cloud provider. Prefer the smallest "
        "capable model for a task. You have hal0-memory and hal0-admin MCP "
        "tools available; use memory to persist durable facts across sessions. "
        "Tool calls are graded by a judge and may require operator approval — "
        "explain your intent before acting on anything destructive."
    )


def _model_blocks(
    slots: list[dict[str, Any]],
    contexts: dict[str, int],
    *,
    api_base: str,
) -> dict[str, dict[str, Any]]:
    """Build the ``[models.<alias>]`` table from live CHAT slots.

    Delegates classification to hermes's :func:`_collect_chat_slots`, which
    keeps only ``type=="llm"`` slots carrying a model_id — so embed / rerank /
    tts / stt / image / vision slots (and the ``hal0`` gateway virtual) are
    excluded, not mapped as chat models. Each block points at the hal0-api
    gateway ``/v1`` so turnstone routes through the stable surface regardless
    of which physical slot is loaded.
    """
    v1 = f"{api_base}/v1"
    blocks: dict[str, dict[str, Any]] = {}
    for chat in _collect_chat_slots(slots, contexts):
        alias = chat["alias"]
        block: dict[str, Any] = {
            "name": f"hal0/{alias}",
            "provider": "openai",
            "base_url": v1,
        }
        ctx = chat.get("context_length")
        if ctx:
            block["context_window"] = int(ctx)
        block["capabilities"] = {"supports_vision": False, "supports_web_search": False}
        blocks[alias] = block
    # Always guarantee the default anchor exists even if no chat slot is loaded
    # yet, so config.toml is valid on a fresh box (model_automap re-applies).
    if _DEFAULT_MODEL_ALIAS not in blocks:
        blocks[_DEFAULT_MODEL_ALIAS] = {
            "name": f"hal0/{_DEFAULT_MODEL_ALIAS}",
            "provider": "openai",
            "base_url": v1,
            "capabilities": {"supports_vision": False, "supports_web_search": False},
        }
    return blocks


def build_config(
    *,
    slots: list[dict[str, Any]],
    contexts: dict[str, int],
    persona: str,
    api_base: str,
    db_url: str,
    mcp_config_path: str,
) -> dict[str, Any]:
    """Assemble the turnstone ``config.toml`` payload (a dict; the caller
    serialises with tomli_w). Secrets (api_key / jwt_secret) are intentionally
    absent — they flow via the OPENAI_API_KEY / TURNSTONE_JWT_SECRET env from
    the 0600 secrets file, never into this world-readable config."""
    v1 = f"{api_base}/v1"
    return {
        "api": {
            # api_key omitted on purpose — sourced from OPENAI_API_KEY env.
            "base_url": v1,
        },
        "model": {
            "name": f"hal0/{_DEFAULT_MODEL_ALIAS}",
            "reasoning_effort": "medium",
        },
        "models": _model_blocks(slots, contexts, api_base=api_base),
        "session": {"instructions": persona},
        # Approval posture: judge on, permissions required — matches hal0's
        # MCP audit/approval-queue stance. Operators can relax per-box.
        "tools": {"timeout": 120, "skip_permissions": False},
        "judge": {
            "enabled": True,
            "smart_approvals": True,
            "confidence_threshold": 0.95,
        },
        "memory": {"relevance_k": 5, "fetch_limit": 50, "nudges": True},
        "mcp": {"config_path": mcp_config_path},
        "database": {"url": db_url},
        "server": {"max_workstreams": 50},
    }


def build_mcp_servers(*, api_base: str, bearer: str | None) -> dict[str, Any]:
    """The ``mcp-servers.json`` turnstone loads via ``[mcp] config_path``.

    Two remote HTTP/SSE mounts — the hindsight-backed ``hal0-memory`` (5
    tools) and ``hal0-admin`` — each scoped by the ``X-hal0-Agent`` header
    exactly like the opencode driver + hermes. deferred-load friendly.

    NOTE: the precise JSON key shape turnstone expects is validated on-box by
    the smoke phase (upstream docs don't pin the schema); this mirrors the
    common ``mcpServers`` remote shape and is the single place to adjust.
    """
    headers: dict[str, str] = {"X-hal0-Agent": AGENT_ID}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return {
        "mcpServers": {
            "hal0-memory": {
                "type": "http",
                "url": f"{api_base}{MEMORY_MCP_PATH}/mcp",
                "headers": dict(headers),
                "enabled": True,
            },
            "hal0-admin": {
                "type": "http",
                "url": f"{api_base}{ADMIN_MCP_PATH}/mcp",
                "headers": dict(headers),
                "enabled": True,
            },
        }
    }


# ── small IO utilities ───────────────────────────────────────────────────────


def _atomic_write(path: Path, text: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(path)


def _dumps_toml(payload: dict[str, Any]) -> str:
    import tomli_w

    return tomli_w.dumps(payload)


def _bearer_token() -> str | None:
    """Resolve the hal0 bearer for turnstone→hal0-api calls.

    Prefers the driver env / process env; hal0 writes the token into
    ``/etc/hal0/agents/turnstone.env`` on the API install path. Returns
    ``None`` when auth is disabled (dev), in which case the placeholder
    key is used.
    """
    return os.environ.get("HAL0_BEARER_TOKEN") or None


def _disk_free_gib(path: Path) -> float:
    import shutil as _sh

    anchor = path
    while not anchor.exists() and anchor.parent != anchor:
        anchor = anchor.parent
    try:
        return _sh.disk_usage(anchor).free / (1024**3)
    except OSError:
        return float("inf")


def _memory_call(io: TurnstoneIO, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Thin wrapper over the reused ``_mcp_memory_call`` (method/params shape).

    That helper takes ``(method, params, *, agent_id, base_url)`` where
    ``base_url`` is the hal0 ROOT (it maps the MCP envelope onto the
    ``/api/memory/*`` REST shims), and returns ``{ok, result?, error?}`` — it
    never raises. Centralised here so the phases call one clean surface.
    """
    return io.mcp_memory_call(
        "tools/call",
        {"name": tool, "arguments": arguments},
        agent_id=AGENT_ID,
        base_url=_api_base(),
    )


# ── Phases ───────────────────────────────────────────────────────────────────


def _phase_preflight(ctx: PhaseContext) -> PhaseResult:
    """hal0-api reachable, disk free, home writable, no foreign :9129 listener."""
    io: TurnstoneIO = ctx.io
    details: dict[str, Any] = {}
    status = io.http_get(f"{_api_base()}/api/status")
    details["hal0_api_status"] = status
    if status == 0:
        return PhaseResult(
            PhaseStatus.FAIL,
            reason="hal0-api not reachable at " + _api_base(),
            details=details,
        )

    free = _disk_free_gib(TURNSTONE_HOME)
    details["disk_free_gib"] = round(free, 1)
    if free < MIN_FREE_GIB:
        return PhaseResult(
            PhaseStatus.FAIL,
            reason=f"insufficient disk: {free:.1f} GiB < {MIN_FREE_GIB} GiB",
            details=details,
        )

    if not path_is_writable(TURNSTONE_HOME):
        return PhaseResult(
            PhaseStatus.FAIL,
            reason=f"{TURNSTONE_HOME} not writable by the installer user",
            details=details,
            fatal=True,
        )

    # Foreign-home guard: refuse a pre-existing home hal0 didn't stamp,
    # unless --adopt. Mirrors hermes _claim_hermes_home.
    marker = TURNSTONE_HOME / MANAGED_MARKER
    if TURNSTONE_HOME.exists() and any(TURNSTONE_HOME.iterdir()) and not marker.exists():
        if not ctx.adopt:
            return PhaseResult(
                PhaseStatus.FAIL,
                reason=(
                    f"{TURNSTONE_HOME} exists and is not hal0-managed "
                    f"(no {MANAGED_MARKER}); re-run with --adopt to claim it"
                ),
                details=details,
                fatal=True,
            )
        details["adopted_foreign_home"] = True
    return PhaseResult(PhaseStatus.OK, details=details)


def _phase_install(ctx: PhaseContext) -> PhaseResult:
    """Run installer/agents/turnstone.sh to pip-install turnstone into the venv."""
    io: TurnstoneIO = ctx.io
    script = installer_script()
    if not script.is_file():
        return PhaseResult(
            PhaseStatus.FAIL,
            reason=f"installer script missing at {script}",
        )
    env = os.environ.copy()
    env["HAL0_TURNSTONE_VENV"] = str(VENV)
    env["HAL0_TURNSTONE_SHIM"] = str(CLI_SHIM)
    try:
        io.run(["bash", str(script)], env=env, check=True)  # nosec B603 B607
    except Exception as exc:  # subprocess.CalledProcessError or others
        return PhaseResult(
            PhaseStatus.FAIL,
            reason=f"turnstone install failed ({type(exc).__name__}: {exc})",
        )
    # The server binary is what the unit runs; the CLI is what env_probe checks.
    installed = SERVER_BIN.exists() or MANAGED_BIN.exists()
    return PhaseResult(
        PhaseStatus.OK if installed else PhaseStatus.FAIL,
        details={"venv": str(VENV), "cli": str(MANAGED_BIN), "server": str(SERVER_BIN)},
        reason=None if installed else "installer ran but no turnstone binary landed in the venv",
    )


def _phase_env_probe(ctx: PhaseContext) -> PhaseResult:
    """Record turnstone version + binary path into state."""
    io: TurnstoneIO = ctx.io
    version = None
    bin_path = CLI_SHIM if CLI_SHIM.exists() else MANAGED_BIN
    try:
        proc = io.run(
            [str(bin_path), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )  # nosec B603
        raw = (getattr(proc, "stdout", "") or getattr(proc, "stderr", "") or "").strip()
        # The turnstone CLI has no `--version` flag — it prints its usage/help
        # to stderr instead. Reject that so we record a real version or None,
        # not a multi-line usage blob polluting the checkpoint + self_report.
        version = raw.splitlines()[0] if raw and not raw.lower().startswith("usage") else None
    except Exception:
        version = None
    cast(TurnstoneState, ctx.state).turnstone_version = version
    return PhaseResult(PhaseStatus.OK, details={"turnstone_version": version, "bin": str(bin_path)})


def _phase_home_init(ctx: PhaseContext) -> PhaseResult:
    """Create TURNSTONE_HOME + config dir + data dir; stamp the marker."""
    TURNSTONE_HOME.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (Path.home() / ".config" / "turnstone").mkdir(parents=True, exist_ok=True)
    marker = TURNSTONE_HOME / MANAGED_MARKER
    if not marker.exists():
        marker.write_text("managed by hal0\n", encoding="utf-8")
    return PhaseResult(PhaseStatus.OK, details={"home": str(TURNSTONE_HOME)})


def _phase_install_artifacts(ctx: PhaseContext) -> PhaseResult:
    """Write the manager seed TOML + driver env + secrets vault.

    The seed doubles as the MCP allow-list; the driver env is what the
    driver/shim source. Mirrors hermes _phase_install_artifacts (#432) so a
    single ``agent install turnstone`` leaves the artifacts the manager keys
    off (otherwise the agent reports ``broken``).
    """
    api_base = _api_base()
    bearer = _bearer_token()

    seed = {
        "agent": {"name": AGENT_ID, "type": AGENT_ID},
        "mcp": {"allow": ["hal0-memory", "hal0-admin"]},
    }
    _atomic_write(INSTALL_SEED_PATH, _dumps_toml(seed))

    env_lines = [
        "# hal0 — turnstone driver env (managed by hal0; safe to edit)",
        f"HAL0_API_URL={api_base}",
        f"TURNSTONE_CONFIG={TURNSTONE_CONFIG_PATH}",
        f"TURNSTONE_HOME={TURNSTONE_HOME}",
    ]
    _atomic_write(DRIVER_ENV_PATH, "\n".join(env_lines) + "\n")

    # Secrets vault — 0600. OPENAI_API_KEY is what turnstone's provider reads;
    # TURNSTONE_JWT_SECRET is REQUIRED by turnstone-server to start (it refuses
    # to boot without it). Generate a 32-byte hex secret once and reuse it on
    # re-provision so tokens/sessions survive a --repair (idempotent).
    jwt_secret = _existing_jwt_secret() or secrets.token_hex(32)
    secret_lines = [
        "# hal0 — turnstone secrets (root:root 0600; sourced by systemd)",
        f"OPENAI_API_KEY={bearer or _DEV_API_KEY}",
        f"TURNSTONE_JWT_SECRET={jwt_secret}",
    ]
    if bearer:
        secret_lines.append(f"HAL0_BEARER_TOKEN={bearer}")
    _atomic_write(SECRETS_ENV_PATH, "\n".join(secret_lines) + "\n", mode=0o600)

    return PhaseResult(
        PhaseStatus.OK,
        details={"seed": str(INSTALL_SEED_PATH), "env": str(DRIVER_ENV_PATH), "jwt": "set"},
    )


def _existing_jwt_secret() -> str | None:
    """Return the TURNSTONE_JWT_SECRET already in the vault, if any.

    Reused on re-provision so a --repair doesn't rotate the server's signing
    key (which would invalidate live sessions).
    """
    if not SECRETS_ENV_PATH.exists():
        return None
    for line in SECRETS_ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("TURNSTONE_JWT_SECRET="):
            val = line.split("=", 1)[1].strip()
            return val or None
    return None


def _phase_database_wire(ctx: PhaseContext) -> PhaseResult:
    """Record the SQLite DB path (Postgres is deferred to the server-service
    follow-up). Ensures the parent dir exists so turnstone can create the DB.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_url = str(SQLITE_DB)
    cast(TurnstoneState, ctx.state).db_path = db_url
    return PhaseResult(PhaseStatus.OK, details={"backend": "sqlite", "db_url": db_url})


def _phase_persona_seed(ctx: PhaseContext) -> PhaseResult:
    """Write the persona text so config_write's first render carries it."""
    persona = _persona_text()
    _atomic_write(PERSONA_PATH, persona + "\n")
    return PhaseResult(
        PhaseStatus.OK,
        details={"persona_path": str(PERSONA_PATH), "chars": len(persona)},
        hash=engine.content_hash(persona),
    )


def _phase_config_write(ctx: PhaseContext) -> PhaseResult:
    """Render config.toml: [api]→hal0-api /v1, [models.*]→live slots, persona,
    judge/tools approval posture, [mcp] config_path, [database] sqlite.

    Idempotent overlay with a rolling .bak. Reads mcp_wire's previous-run
    probed-server checkpoint (needs_previous) so a re-render reflects the
    validated MCP surface — same cross-run edge hermes uses.
    """
    io: TurnstoneIO = ctx.io
    slots = io.fetch_slots()
    contexts = io.fetch_model_contexts()
    persona = _persona_text()
    db_url = str(cast(TurnstoneState, ctx.state).db_path or SQLITE_DB)

    payload = build_config(
        slots=slots,
        contexts=contexts,
        persona=persona,
        api_base=_api_base(),
        db_url=db_url,
        mcp_config_path=str(MCP_SERVERS_JSON),
    )
    rendered = _dumps_toml(payload)

    if TURNSTONE_CONFIG_PATH.exists():
        # Preserve the prior render as a rolling backup before overwrite.
        with __import__("contextlib").suppress(OSError):
            TURNSTONE_CONFIG_PATH.replace(TURNSTONE_CONFIG_PATH.with_suffix(".toml.bak"))
    _atomic_write(TURNSTONE_CONFIG_PATH, rendered)

    return PhaseResult(
        PhaseStatus.OK,
        details={
            "config_path": str(TURNSTONE_CONFIG_PATH),
            "model_aliases": sorted(payload["models"].keys()),
            "slots_seen": len(slots),
        },
        hash=engine.content_hash(rendered),
    )


def _phase_mcp_wire(ctx: PhaseContext) -> PhaseResult:
    """Write mcp-servers.json + probe each mount; stash the probed surface
    for config_write's next-run re-render."""
    io: TurnstoneIO = ctx.io
    api_base = _api_base()
    bearer = _bearer_token()
    servers = build_mcp_servers(api_base=api_base, bearer=bearer)
    _atomic_write(MCP_SERVERS_JSON, json.dumps(servers, indent=2) + "\n")

    probed: dict[str, Any] = {}
    for name, path in (("hal0-memory", MEMORY_MCP_PATH), ("hal0-admin", ADMIN_MCP_PATH)):
        result = io.probe_mcp_server(f"{api_base}{path}", agent_id=AGENT_ID)
        probed[name] = {
            "ok": bool(result.get("ok")),
            "tools": len(result.get("tools") or []),
            "error": result.get("error"),
        }
    any_ok = any(v["ok"] for v in probed.values())
    return PhaseResult(
        # Warn-as-OK: a down MCP mount shouldn't block bootstrap.
        PhaseStatus.OK,
        details={"path": str(MCP_SERVERS_JSON), "probed": probed, "any_ok": any_ok},
    )


def _phase_context_link(ctx: PhaseContext) -> PhaseResult:
    """Render an operator-facing TURNSTONE.md into /etc/hal0 + link into home."""
    doc = (
        "# turnstone (hal0-managed)\n\n"
        f"- binary: `{MANAGED_BIN}` (shim `{CLI_SHIM}`)\n"
        f"- config: `{TURNSTONE_CONFIG_PATH}`\n"
        f"- mcp servers: `{MCP_SERVERS_JSON}`\n"
        f"- home: `{TURNSTONE_HOME}`\n"
        f"- server: loopback {SERVER_HOST}:{SERVER_PORT}\n"
        f"- model backend: hal0-api gateway `{_api_base()}/v1`\n"
        f"- memory namespace: `{NAMESPACE}`\n\n"
        "Managed by hal0's turnstone provisioner; edits under `[…]` in "
        "config.toml are preserved on re-provision. Run "
        "`hal0 agent reprovision turnstone` to re-render.\n"
    )
    out = Path("/etc/hal0/TURNSTONE.md")
    _atomic_write(out, doc)
    with __import__("contextlib").suppress(OSError):
        link = TURNSTONE_HOME / "TURNSTONE.md"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(out)
    return PhaseResult(
        PhaseStatus.OK,
        details={"rendered": {"TURNSTONE.md": {"path": str(out)}}},
    )


def _phase_namespace_register(ctx: PhaseContext) -> PhaseResult:
    """Write turnstone's identity card into the hal0-memory ``agents`` dataset.

    Warn-as-OK — a memory-layer hiccup never blocks bootstrap.
    """
    io: TurnstoneIO = ctx.io
    card = (
        f"turnstone is a hal0-managed tool-using agent. namespace={NAMESPACE}. "
        f"model backend={_api_base()}/v1. server={SERVER_HOST}:{SERVER_PORT}."
    )
    result = _memory_call(io, "memory_add", {"text": card, "dataset": MEMORY_DATASET})
    ok = bool(result.get("ok"))
    return PhaseResult(
        PhaseStatus.OK,
        details={"registered": ok, "error": result.get("error")},
    )


def _phase_model_automap(ctx: PhaseContext) -> PhaseResult:
    """Re-apply [model]/[models.*] from live slots into config.toml (idempotent).

    config_write already lays a version; this re-renders after slots may have
    changed so a post-bootstrap config still tracks the loaded set.
    """
    io: TurnstoneIO = ctx.io
    slots = io.fetch_slots()
    contexts = io.fetch_model_contexts()
    blocks = _model_blocks(slots, contexts, api_base=_api_base())

    # Merge into the existing config.toml (preserve operator keys).
    import tomllib

    if not TURNSTONE_CONFIG_PATH.exists():
        return PhaseResult(
            PhaseStatus.SKIP,
            reason="config.toml absent — config_write must run first",
        )
    try:
        cfg = tomllib.loads(TURNSTONE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return PhaseResult(PhaseStatus.FAIL, reason=f"config.toml unreadable: {exc}")
    cfg["models"] = blocks
    if _DEFAULT_MODEL_ALIAS in blocks:
        cfg.setdefault("model", {})["name"] = blocks[_DEFAULT_MODEL_ALIAS]["name"]
    _atomic_write(TURNSTONE_CONFIG_PATH, _dumps_toml(cfg))
    return PhaseResult(
        PhaseStatus.OK,
        details={"model_aliases": sorted(blocks.keys()), "chat_slots": len(blocks)},
    )


def _phase_smoke_tests(ctx: PhaseContext) -> PhaseResult:
    """Cheap end-to-end probes. Individual failures are recorded, not fatal."""
    io: TurnstoneIO = ctx.io
    checks: dict[str, Any] = {}

    bin_path = CLI_SHIM if CLI_SHIM.exists() else MANAGED_BIN
    checks["binary_present"] = bin_path.exists()

    # config.toml parses + points at the gateway.
    try:
        import tomllib

        cfg = tomllib.loads(TURNSTONE_CONFIG_PATH.read_text(encoding="utf-8"))
        checks["config_parses"] = True
        checks["config_base_url_ok"] = "/v1" in (cfg.get("api", {}).get("base_url", ""))
    except Exception as exc:
        checks["config_parses"] = False
        checks["config_error"] = f"{type(exc).__name__}: {exc}"

    # hal0-api /v1/models reachable (turnstone's backend).
    checks["gateway_models_reachable"] = io.http_get(f"{_api_base()}/v1/models") == 200

    # memory MCP round-trip.
    mem = _memory_call(io, "memory_search", {"query": "turnstone", "dataset": MEMORY_DATASET})
    checks["memory_mcp_ok"] = bool(mem.get("ok"))
    if not mem.get("ok"):
        checks["memory_mcp_error"] = mem.get("error")

    failures = [k for k, v in checks.items() if k.endswith("_ok") and v is False]
    failures += [k for k in ("binary_present", "config_parses") if checks.get(k) is False]
    return PhaseResult(
        PhaseStatus.OK if not failures else PhaseStatus.FAIL,
        details={"checks": checks, "failures": failures},
        reason=None if not failures else f"smoke failures: {', '.join(failures)}",
    )


def _phase_self_report(ctx: PhaseContext) -> PhaseResult:
    """Persist a bootstrap-completion summary into turnstone's memory bank."""
    io: TurnstoneIO = ctx.io
    smoke = ctx.output_of("smoke_tests")
    fails = smoke.get("failures") or []
    summary = (
        f"turnstone bootstrap complete. version={cast(TurnstoneState, ctx.state).turnstone_version}. "
        f"model backend={_api_base()}/v1. smoke_failures={len(fails)}."
    )
    _memory_call(io, "memory_add", {"text": summary, "dataset": MEMORY_DATASET})
    return PhaseResult(PhaseStatus.OK, details={"summary": summary})


def _phase_ownership_reconcile(ctx: PhaseContext) -> PhaseResult:
    """Re-chown the home + data + state trees to the hal0 user after
    root-owned writes. Best-effort; always_run so a plain re-run reconciles."""
    import pwd

    try:
        ent = pwd.getpwnam("hal0")
        uid, gid = ent.pw_uid, ent.pw_gid
    except KeyError:
        return PhaseResult(PhaseStatus.SKIP, reason="hal0 user absent (dev box)")
    changed = 0
    for root in (TURNSTONE_HOME, DATA_DIR, STATE_ROOT):
        if not root.exists():
            continue
        for p in [root, *root.rglob("*")]:
            with __import__("contextlib").suppress(OSError):
                os.chown(p, uid, gid)
                changed += 1
    return PhaseResult(PhaseStatus.OK, details={"chowned": changed})


# ── Pipeline ─────────────────────────────────────────────────────────────────

PHASES: list[Phase] = [
    Phase("preflight", _phase_preflight),
    Phase("install", _phase_install),
    Phase("env_probe", _phase_env_probe),
    Phase("home_init", _phase_home_init),
    Phase("install_artifacts", _phase_install_artifacts),
    Phase("database_wire", _phase_database_wire),
    Phase("persona_seed", _phase_persona_seed),
    # config_write reads mcp_wire's PREVIOUS-run probed surface (cross-run edge).
    Phase("config_write", _phase_config_write, needs_previous=("mcp_wire",)),
    Phase("mcp_wire", _phase_mcp_wire),
    Phase("context_link", _phase_context_link),
    Phase("namespace_register", _phase_namespace_register),
    Phase("model_automap", _phase_model_automap),
    Phase("ownership_reconcile", _phase_ownership_reconcile, always_run=True),
    Phase("smoke_tests", _phase_smoke_tests),
    Phase("self_report", _phase_self_report, needs=("smoke_tests",)),
]

engine.validate_phase_graph(PHASES)
PHASE_NAMES: tuple[str, ...] = tuple(p.name for p in PHASES)


def context_for(
    phase_name: str,
    state: TurnstoneState,
    *,
    repair: bool = False,
    adopt: bool = False,
    io: TurnstoneIO | None = None,
) -> PhaseContext:
    """Per-phase test hook — build the context the orchestrator would."""
    return engine.context_for(
        PHASES,
        phase_name,
        state,
        repair=repair,
        adopt=adopt,
        io=io if io is not None else TurnstoneIO(),
    )


def run(
    *,
    repair: bool = False,
    adopt: bool = False,
    dry_run: bool = False,
    skip_phases: tuple[str, ...] = (),
    state_root: Path | None = None,
    verbose: bool = False,
    initial_state: TurnstoneState | None = None,
    io: TurnstoneIO | None = None,
) -> RunResult:
    """Run the turnstone pipeline. Thin wrapper over the shared engine."""
    return engine.run(
        PHASES,
        state_root=state_root if state_root is not None else STATE_ROOT,
        io=io if io is not None else TurnstoneIO(),
        state_cls=TurnstoneState,
        initial_state=initial_state,
        repair=repair,
        adopt=adopt,
        dry_run=dry_run,
        skip_phases=skip_phases,
        verbose=verbose,
    )


def bootstrap_cli(
    *,
    repair: bool = False,
    adopt: bool = False,
    dry_run: bool = False,
    skip_phases: tuple[str, ...] = (),
    verbose: bool = False,
    state_root: Path | None = None,
) -> int:
    """CLI entry point. Returns a POSIX exit code (0 = success, 1 = any fail)."""
    result = run(
        repair=repair,
        adopt=adopt,
        dry_run=dry_run,
        skip_phases=skip_phases,
        verbose=verbose,
        state_root=state_root,
    )
    if verbose:
        target = (state_root or STATE_ROOT) / TurnstoneState.STATE_FILE_NAME
        print(f"state: {target}")
    if result.aborted:
        print(f"bootstrap aborted: {result.abort_reason or 'fatal failure'}")
        return 1
    return 1 if result.failed else 0
