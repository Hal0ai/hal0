"""Hermes-Agent bootstrap state machine (issue #238 scaffold).

Twelve named phases run in a strict deterministic sequence. Each phase
writes a checkpoint into ``provision.json``. On re-run the orchestrator
loads the checkpoint and skips any phase already marked ``ok`` unless
``--repair`` forces re-execution.

This module is the scaffold — every phase is a no-op stub that returns
``ok``. Real provisioning lands in #240 (preflight/install/home_init),
#241 (env_probe/config_write), #242 (mcp_wire), and the remaining
slices in the v0.3 Hermes stream. The phase order + ``PhaseResult``
contract is locked here so downstream slices only have to fill in the
bodies.

State file lives at ``/var/lib/hal0/state/agents/hermes/provision.json``
— intentionally **outside** ``$HERMES_HOME`` so Hermes can't trample
hal0's bookkeeping when the user runs ``hermes reset`` or similar
upstream subcommands.

See ``docs/internal/hermes-bootstrap-plan-2026-05-23.md`` §3 + §16 for
the full design contract and ``docs/internal/adr/0012-remove-auth-and-caddy.md``
for the agent-identity model (X-hal0-Agent header, not Bearer).
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import shutil
import subprocess  # nosec B404 — needed to spawn python -m venv + pip
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Schema version embedded in every provision.json. Bump when the on-disk
# shape changes in a way that can't be migrated by ignoring unknown
# keys. Currently v1 — the layout in `BootstrapState.to_dict()`.
SCHEMA_VERSION = 1

# Canonical state-file location. Lives outside $HERMES_HOME — Hermes
# owns its own tree, and bootstrap state must survive a `hermes reset`.
_DEFAULT_STATE_ROOT = Path("/var/lib/hal0/state/agents/hermes")
_STATE_FILE_NAME = "provision.json"


class PhaseStatus(StrEnum):
    """Per-phase outcome stored in provision.json.

    ``ok``       — phase completed; downstream phases may proceed.
    ``skip``     — phase didn't run (irrelevant for this env); not an error.
    ``fail``     — phase ran and failed; downstream may still run unless fatal.
    ``repair_needed`` — checkpoint hash drifted from current inputs; ``--repair`` re-runs.

    String-valued so JSON round-trips cleanly without a custom encoder.
    """

    OK = "ok"
    SKIP = "skip"
    FAIL = "fail"
    REPAIR_NEEDED = "repair_needed"


@dataclass
class PhaseResult:
    """Outcome of one phase invocation.

    ``hash`` is the optional content hash a phase computes so future
    re-runs can detect when their inputs changed — checkpoint presence
    alone is insufficient (a phase whose inputs drifted needs re-run
    even without ``--repair``).

    ``details`` is a free-form dict each phase can stash. The
    orchestrator never inspects its contents; it just JSON-serialises
    them into the checkpoint.
    """

    status: PhaseStatus
    details: dict[str, Any] = field(default_factory=dict)
    hash: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status.value}
        if self.hash is not None:
            out["hash"] = self.hash
        if self.reason is not None:
            out["reason"] = self.reason
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class BootstrapState:
    """In-memory mirror of ``provision.json``.

    Persists across runs via :meth:`load` / :meth:`save`. ``phases`` is
    keyed by phase name with values built from :meth:`PhaseResult.to_dict`
    plus an ``at`` timestamp the orchestrator stamps at write time.

    The dataclass shape is the contract; the JSON keys are the same as
    the field names so a human inspecting the file can match it back to
    the source code without a schema doc.
    """

    schema_version: int = SCHEMA_VERSION
    started_at: str | None = None
    completed_at: str | None = None
    hal0_version: str | None = None
    hermes_version: str | None = None
    hermes_home: str = "/var/lib/hal0/.hermes"
    venv: str = "/var/lib/hal0/venvs/hermes"
    agent_id: str = "hermes-agent"
    phases: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BootstrapState:
        # Ignore unknown keys so forward-compat schema bumps don't crash
        # an older orchestrator reading a newer file.
        valid = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in data.items() if k in valid}
        return cls(**kwargs)

    def phase_done(self, name: str) -> bool:
        """True iff the phase already ran to a terminal non-failure state.

        Both ``ok`` and ``skip`` count as "done" — a phase that
        legitimately skipped (no STT/TTS slots configured →
        voice_wire SKIP) shouldn't re-run on every bootstrap
        invocation. ``--repair`` is the explicit force-rerun knob.
        """
        entry = self.phases.get(name)
        if not entry:
            return False
        return entry.get("status") in {PhaseStatus.OK.value, PhaseStatus.SKIP.value}

    def save(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        target = root / _STATE_FILE_NAME
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")
        os.replace(tmp, target)

    @classmethod
    def load(cls, root: Path) -> BootstrapState | None:
        target = root / _STATE_FILE_NAME
        if not target.exists():
            return None
        try:
            data = json.loads(target.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return cls.from_dict(data)


# ── Phase implementations (no-op stubs in #238 scaffold) ─────────────────────
#
# Each phase signature: (state: BootstrapState) -> PhaseResult.
#
# Real impls land in subsequent slices:
#   #240 — preflight, install, home_init
#   #241 — env_probe, config_write
#   #242 — mcp_wire
#   #243 — namespace_register
#   #244 — context_link
#   #245 — model_automap, voice_wire
#   #246 — smoke_tests, self_report
#
# Until then every stub returns OK with a "stub" marker so the
# orchestrator wires through end-to-end and the checkpoint shape stays
# valid.


def _stub(name: str) -> Callable[[BootstrapState], PhaseResult]:
    def _phase(state: BootstrapState) -> PhaseResult:
        return PhaseResult(status=PhaseStatus.OK, details={"stub": True})

    _phase.__name__ = f"_phase_{name}"
    _phase.__doc__ = f"Stub for {name!r} phase — real impl pending in a follow-up slice."
    return _phase


# Pinned constants — keep these in sync with installer/agents/hermes/
# requirements.txt and the wrapper script. The constants are exposed
# at module scope so tests can monkey-patch them onto a tmp path.
PYTHON_MIN = (3, 11)
MIN_FREE_GIB = 4
DAEMON_HEALTH_URL = "http://127.0.0.1:8080/api/status"
WRAPPER_INSTALL_PATH = Path("/usr/local/bin/hal0-hermes")
# Canonical CLI entry point on PATH (locked decision #3). The thin
# ``hermes`` wrapper injects HAL0_AGENT_ID and execs the venv hermes
# WITHOUT pinning HERMES_HOME (the hermes default ~/.hermes resolves to
# /var/lib/hal0/.hermes for the hal0 user). ``hal0-hermes`` stays as a
# back-compat symlink to this.
HERMES_CLI_INSTALL_PATH = Path("/usr/local/bin/hermes")
REPO_ROOT_FOR_INSTALLER = Path(__file__).resolve().parents[3]

# ── Install artifacts (issue #432) ───────────────────────────────────────────
#
# ``hal0 agent bootstrap hermes`` is a separate install path from
# ``AgentManager.install``; the provision pipeline writes data/state but
# never wrote the three artifacts downstream components key off, each of
# which falsely assumed "some other step writes it":
#
#   * the manager seed at /etc/hal0/agents/hermes.toml — without it
#     ``AgentManager._read_record`` short-circuits to ``broken`` before it
#     ever consults driver health;
#   * the driver env file at /etc/hal0/agents/hermes.env — the path the
#     Hermes driver sources (NOT the outbound secrets vault at
#     HERMES_SECRETS_ENV, a different file);
#   * runtime.json under $HERMES_HOME — the embed token chat_proxy sends as
#     ``Authorization: Bearer`` on the browser→hermes hop; absent, every
#     chat request reaches hermes unauthenticated.
#
# All three constants live at module scope so tests can monkey-patch them
# onto a tmp path, same posture as HERMES_SECRETS_ENV / AGENT_ALLOWLIST_PATH.
# INSTALL_SEED_PATH is the SAME file as AGENT_ALLOWLIST_PATH — the seed
# write merges, never clobbers, any operator ``[mcp.servers.*]`` blocks.
INSTALL_SEED_PATH = Path("/etc/hal0/agents/hermes.toml")
DRIVER_ENV_PATH = Path("/etc/hal0/agents/hermes.env")
RUNTIME_JSON_NAME = "runtime.json"


# ── Phase A: preflight ──────────────────────────────────────────────────────


def _http_get(url: str, *, timeout: float = 3.0) -> int:
    """Cheap stdlib reachability check — returns HTTP status or 0 on error.

    Used by preflight to confirm the hal0 daemon is up before we start
    spawning subprocesses. Stdlib-only (no requests / httpx) keeps the
    bootstrap importable on minimal install paths.
    """
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except (URLError, OSError, TimeoutError):
        return 0


def _phase_preflight(state: BootstrapState) -> PhaseResult:
    """Hard-fail when the host can't host Hermes.

    Documented blockers (plan §4):

    * Python ≥ 3.11 available — bootstrap shells out to a venv with
      explicit Python; we verify the running interpreter qualifies so
      we can re-use ``sys.executable`` instead of hunting PATH.
    * ``hal0`` daemon reachable at ``/api/status`` — agents that can't
      reach hal0 are useless. Catch it now instead of during config_write.
    * ``/var/lib/hal0/`` writable — we'll be writing the venv + HERMES_HOME
      there in the next phase.
    * ≥ 4 GiB free under ``/var/lib/hal0/`` — Hermes deps + a typical
      memory cache run ~3 GiB; 4 GiB leaves headroom for venv rebuild.
    """
    failures: list[str] = []
    details: dict[str, Any] = {}

    py_version = sys.version_info[:3]
    details["python_version"] = ".".join(str(p) for p in py_version)
    if py_version < PYTHON_MIN:
        failures.append(
            f"python {'.'.join(str(p) for p in PYTHON_MIN)}+ required, "
            f"have {details['python_version']} — run `apt install python3.11`",
        )

    rc = _http_get(DAEMON_HEALTH_URL)
    details["daemon_http_status"] = rc
    if rc != 200:
        failures.append(
            f"hal0 daemon unreachable at {DAEMON_HEALTH_URL} (status={rc or 'no-response'}) "
            "— run `systemctl start hal0`",
        )

    var_lib = Path(state.venv).parent.parent  # /var/lib/hal0/
    details["var_lib_path"] = str(var_lib)
    if not var_lib.exists() or not os.access(var_lib, os.W_OK):
        failures.append(
            f"{var_lib} not writable — run `sudo install -d -o hal0 -g hal0 -m 0755 {var_lib}`",
        )
    else:
        st = os.statvfs(var_lib)
        free_gib = st.f_bavail * st.f_frsize / (1024**3)
        details["free_gib"] = round(free_gib, 2)
        if free_gib < MIN_FREE_GIB:
            failures.append(
                f"{var_lib} has {free_gib:.1f} GiB free; need >= {MIN_FREE_GIB} — clear space",
            )

    if failures:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            details=details,
            reason="; ".join(failures),
        )
    return PhaseResult(status=PhaseStatus.OK, details=details)


# ── Phase B: install ────────────────────────────────────────────────────────


def _resolve_python311(prober: Callable[[str], str | None] = shutil.which) -> str | None:
    """Find a python3.11 interpreter; fall back to the running one when it qualifies.

    Prefers an explicit ``python3.11`` on PATH so the venv pins minor
    version regardless of what ``sys.executable`` is. Falls back to
    ``sys.executable`` only when the running interpreter is itself
    3.11+ — keeps tests usable on Python 3.12+ CI shards.
    """
    explicit = prober("python3.11")
    if explicit:
        return explicit
    if sys.version_info[:2] >= PYTHON_MIN:
        return sys.executable
    return None


def _venv_python(venv: Path) -> Path:
    return venv / "bin" / "python"


def _install_venv(
    venv: Path,
    requirements: Path,
    *,
    runner: Any = subprocess,
    python_resolver: Callable[[], str | None] = _resolve_python311,
) -> None:
    """Create the venv at ``venv`` and install ``requirements`` into it.

    Two-step: ``python3.11 -m venv`` then ``pip install -r``. We don't
    use ``uv`` here to keep the dependency footprint zero — the
    runtime venv is small and pip is universally available.
    """
    py = python_resolver()
    if py is None:
        raise RuntimeError("no python 3.11 interpreter found on PATH")
    venv.parent.mkdir(parents=True, exist_ok=True)
    if not venv.exists():
        runner.run([py, "-m", "venv", str(venv)], check=True)  # nosec B603
    pip = _venv_python(venv)
    runner.run(  # nosec B603 — argv from local config
        [str(pip), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
    )
    runner.run(  # nosec B603
        [str(pip), "-m", "pip", "install", "-r", str(requirements)],
        check=True,
    )


def _copy_wrapper(wrapper_src: Path, wrapper_dst: Path) -> None:
    """Copy + chmod the wrapper into ``wrapper_dst``."""
    wrapper_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wrapper_src, wrapper_dst)
    wrapper_dst.chmod(0o755)


def _install_backcompat_symlink(target: Path, link: Path) -> None:
    """Point ``link`` -> ``target`` (idempotent), replacing any prior file.

    Used to make the legacy ``hal0-hermes`` entry point a symlink to the
    canonical ``hermes`` wrapper. A pre-existing regular file (an older
    install's copied wrapper) is replaced so the two never drift.
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if os.readlink(link) == str(target):
            return
        link.unlink()
    elif link.exists():
        link.unlink()
    os.symlink(str(target), str(link))


def _copy_plugin_tree(src: Path, dst: Path) -> None:
    """Mirror a plugin directory (idempotent)."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _phase_install(state: BootstrapState) -> PhaseResult:
    """Provision the managed Hermes venv + wrapper + plugin stubs.

    The plugin stub at ``installer/agents/hermes/plugins/hal0-memory/``
    is copied verbatim into ``$HERMES_HOME/plugins/memory/hal0-memory/``.
    The legacy ``hal0`` model-provider plugin was removed (R4 H4): it
    hardcoded ``base_url=http://127.0.0.1:8000/api/v1`` which has no
    listener, and the composite ``hal0`` upstream in :mod:`hal0.api`
    now supersedes it.

    Skips heavy work when the venv binary already exists at the
    expected version — re-runs of ``hal0 agent bootstrap hermes`` are
    cheap unless ``--repair`` forces re-install.
    """
    details: dict[str, Any] = {}
    venv = Path(state.venv)
    requirements = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "requirements.txt"
    # Canonical CLI source is ``installer/wrappers/hermes`` (no HERMES_HOME
    # pin); ``hal0-hermes`` becomes a back-compat symlink to it.
    hermes_wrapper_src = REPO_ROOT_FOR_INSTALLER / "installer" / "wrappers" / "hermes"
    plugin_src_root = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "plugins"

    if not requirements.is_file():
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"requirements.txt missing at {requirements}",
        )
    if not hermes_wrapper_src.is_file():
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"wrapper source missing at {hermes_wrapper_src}",
        )

    hermes_bin = _venv_python(venv).parent / "hermes"
    if not hermes_bin.exists():
        try:
            _install_venv(venv, requirements)
        except (subprocess.SubprocessError, RuntimeError, OSError) as exc:
            return PhaseResult(
                status=PhaseStatus.FAIL,
                reason=f"venv install failed: {exc}",
                details=details,
            )
    details["venv"] = str(venv)
    details["hermes_bin"] = str(hermes_bin)

    try:
        # Canonical entry point: /usr/local/bin/hermes (no HERMES_HOME pin).
        _copy_wrapper(hermes_wrapper_src, HERMES_CLI_INSTALL_PATH)
        details["hermes_cli"] = str(HERMES_CLI_INSTALL_PATH)
        # Back-compat: hal0-hermes -> hermes symlink so any caller still
        # invoking the old name resolves to the canonical wrapper. The
        # symlink, not a stale copy, keeps the two in lockstep forever.
        _install_backcompat_symlink(HERMES_CLI_INSTALL_PATH, WRAPPER_INSTALL_PATH)
        details["wrapper"] = str(WRAPPER_INSTALL_PATH)
    except OSError as exc:
        # Non-root operators land here — surface so the user can sudo.
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"wrapper install to {HERMES_CLI_INSTALL_PATH} failed: {exc}",
            details=details,
        )

    # Plugin stubs into HERMES_HOME-shaped locations. Real bodies in #241/#242.
    # Claim HERMES_HOME with the .hal0-managed marker FIRST so home_init's
    # "is this my tree?" check passes — install populates HERMES_HOME with
    # plugin dirs, so it has to be the phase that stamps the marker.
    hermes_home = Path(state.hermes_home)
    claimed, reason = _claim_hermes_home(hermes_home)
    if not claimed:
        return PhaseResult(status=PhaseStatus.FAIL, reason=reason)
    plugin_targets = {
        "hal0-memory": hermes_home / "plugins" / "memory" / "hal0-memory",
    }
    # Remove the legacy broken ``hal0`` model-provider plugin if a
    # previous bootstrap left it behind. Idempotent — silently no-op if
    # already gone.
    legacy_hal0_plugin = hermes_home / "plugins" / "model-providers" / "hal0"
    if legacy_hal0_plugin.exists():
        try:
            shutil.rmtree(legacy_hal0_plugin)
        except OSError as exc:
            log.warning(
                "hermes_provision.legacy_plugin_cleanup_failed",
                path=str(legacy_hal0_plugin),
                error=str(exc),
            )
    for src_name, dst in plugin_targets.items():
        src = plugin_src_root / src_name
        if not src.exists():
            return PhaseResult(
                status=PhaseStatus.FAIL,
                reason=f"plugin source missing at {src}",
            )
        try:
            _copy_plugin_tree(src, dst)
        except OSError as exc:
            return PhaseResult(
                status=PhaseStatus.FAIL,
                reason=f"plugin copy {src} -> {dst} failed: {exc}",
            )
    details["plugins"] = [str(p) for p in plugin_targets.values()]
    return PhaseResult(status=PhaseStatus.OK, details=details)


# ── Phase D: home_init ──────────────────────────────────────────────────────


_HAL0_MANAGED_MARKER = ".hal0-managed"


def _claim_hermes_home(hermes_home: Path) -> tuple[bool, str | None]:
    """Stamp the ``.hal0-managed`` marker — or refuse if HERMES_HOME isn't ours.

    Returns ``(claimed, reason)``: ``claimed=True`` on success;
    ``claimed=False`` with a ``reason`` when the dir is populated and
    lacks the marker (user's pre-existing ~/.hermes — bail). Used by
    both install (which has to write plugins into the tree) and
    home_init (which makes the layout canonical).
    """
    marker = hermes_home / _HAL0_MANAGED_MARKER
    if hermes_home.exists() and not marker.exists() and any(hermes_home.iterdir()):
        return (
            False,
            f"{hermes_home} exists and is not hal0-managed "
            f"(missing {_HAL0_MANAGED_MARKER}). Move it aside before re-running.",
        )
    hermes_home.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        marker.write_text(
            "hal0 — this HERMES_HOME is managed by hal0 (issue #240). Edits may be overwritten.\n",
            encoding="utf-8",
        )
    return (True, None)


def _phase_home_init(state: BootstrapState) -> PhaseResult:
    """Make the ``$HERMES_HOME`` layout canonical.

    Install (#240's first phase) already claimed the marker; home_init
    is responsible for the wider directory tree Hermes expects.
    Re-claiming via :func:`_claim_hermes_home` is harmless when install
    already did so, and necessary when home_init runs first
    (``--skip-phase install``).
    """
    hermes_home = Path(state.hermes_home)
    claimed, reason = _claim_hermes_home(hermes_home)
    if not claimed:
        return PhaseResult(status=PhaseStatus.FAIL, reason=reason)

    standard_subdirs = (
        "memories",
        "skills",
        "plugins",
        "plugins/memory",
        "plugins/model-providers",
        "logs",
        "sessions",
        "profiles",
        "mcp-tokens",
    )
    for sub in standard_subdirs:
        (hermes_home / sub).mkdir(parents=True, exist_ok=True)

    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "hermes_home": str(hermes_home),
            "marker": str(hermes_home / _HAL0_MANAGED_MARKER),
        },
    )


# ── Phase C: env_probe ──────────────────────────────────────────────────────
#
# Walks the hal0-admin MCP probe tools (#237) and stashes a snapshot
# under ``$HERMES_HOME/`` so config_write + context_link can render
# from the same point-in-time view. We call the probe functions
# directly rather than HTTP-roundtripping the local MCP — same data,
# zero dispatcher hop, easier to test.


def _read_env_probe() -> dict[str, Any]:
    """Compose the env_report snapshot. Late-imports keep the bootstrap
    importable when the MCP probes module shifts location."""
    from hal0.mcp import probes  # local import for late binding

    return {
        "env_report": probes.env_report(),
        "gpu_target_version": probes.gpu_target_version(),
        "npu_status": probes.npu_status(),
        "ai_models": probes.model_store_probe("/mnt/ai-models"),
    }


def _phase_env_probe(state: BootstrapState) -> PhaseResult:
    """Capture a host-environment snapshot for downstream phases.

    Writes the snapshot to ``$HERMES_HOME/env-<ts>.json`` AND keeps a
    pointer in ``provision.json``. Snapshot is overwritten on every
    re-run because it's a point-in-time view, not a checkpoint.
    """
    snapshot = _read_env_probe()
    ts = _utcnow().replace(":", "").replace("-", "")
    hermes_home = Path(state.hermes_home)
    hermes_home.mkdir(parents=True, exist_ok=True)
    snapshot_path = hermes_home / f"env-{ts}.json"
    snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "snapshot_path": str(snapshot_path),
            "strix_halo": snapshot["env_report"].get("cpu", {}).get("strix_halo"),
            "gfx": snapshot["gpu_target_version"].get("gfx"),
            "npu_present": snapshot["npu_status"].get("present"),
        },
    )


# ── Phase E: config_write ───────────────────────────────────────────────────


CONFIG_TEMPLATE_PATH = Path(__file__).resolve().parent / "hermes_templates" / "config.yaml.j2"


def _resolve_primary_slot(
    *,
    slots_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Pick the live primary chat slot from the local hal0 daemon.

    Reads ``/api/slots`` (canonical post-Lemonade source) and selects
    the entry named ``primary`` (or the first ready ``type=='llm'``
    slot when no name matches). Returns the keys the config template
    needs. Falls back to a safe-but-unwired placeholder when no slot
    is loaded — self_report surfaces that in the bootstrap summary.

    Until v0.2 this read Lemonade's ``/v1/health`` and looked for
    ``loaded``/``slots`` keys, which post-Lemonade-embed are absent
    (the payload uses ``all_models_loaded``). The result was a silent
    fall-through to a placeholder URL on port 8000 — a daemon-less
    address that never wired Hermes to anything real.
    """
    fallback = {
        "model": "primary",
        "base_url": _DEFAULT_PRIMARY_BACKEND_URL,
        "context_length": 32768,
    }
    fetch = slots_fetcher or _fetch_slots
    slots = fetch() or []

    def _chat(s: dict[str, Any]) -> bool:
        # `type` is the post-Lemonade canonical key (llm/embedding/...);
        # `kind` survives from the pre-Lemonade schema.
        kind = str(s.get("type") or s.get("kind") or "").lower()
        return kind in {"llm", "chat"}

    candidates = [s for s in slots if isinstance(s, dict) and _chat(s)]
    primary = next((s for s in candidates if s.get("name") == "primary"), None)
    if primary is None:
        primary = next((s for s in candidates if _is_ready(s)), None)
    if primary is None:
        return fallback

    model = _slot_model_id(primary) or fallback["model"]
    base_url = _slot_backend_url(primary)
    # The slot's `backend_url` points at the upstream llama-server
    # (e.g. http://127.0.0.1:8001/v1). Hermes should talk to hal0's
    # OpenAI-compat router instead so caching/dispatch stays intact.
    # hal0-api mounts the OpenAI surface at `/v1` (NOT `/api/v1` —
    # Lemonade's native prefix is dropped at the wrapper layer).
    if not base_url or "127.0.0.1:8001" in base_url:
        base_url = f"{HAL0_API_URL}/v1"
    ctx = primary.get("context_length") or primary.get("ctx_size") or fallback["context_length"]
    try:
        ctx = int(ctx)
    except (TypeError, ValueError):
        ctx = fallback["context_length"]
    return {"model": model, "base_url": base_url, "context_length": ctx}


def _default_mcp_servers() -> list[dict[str, Any]]:
    """Builtin MCP server inventory (matches PR-1's allowlist + auto-register).

    Phase 6 (PR-3): the template loops over this list rather than
    hard-coding two entries. Adding a server is now an installer-side
    edit to the allowlist + a probe — no template change required.
    """
    return [
        {
            "name": "hal0-admin",
            "url": "http://127.0.0.1:8080/mcp/admin/mcp",
            "type": "http",
            "private": False,
            "timeout": 60,
            "usage_hint": (
                "query/manage hal0 platform state (slots, services, models, hardware). "
                "Use when the operator asks about system state or wants to inspect a slot."
            ),
        },
        {
            "name": "hal0-memory",
            "url": "http://127.0.0.1:8080/mcp/memory/mcp",
            "type": "http",
            "private": True,
            "timeout": 30,
            "usage_hint": (
                "read/write persistent context across sessions. Use when the operator "
                "references prior conversations or asks you to remember a fact."
            ),
        },
    ]


def _render_config_yaml(
    *,
    primary: dict[str, Any] | None,
    chat_slots: list[dict[str, Any]] | None = None,
    stt: dict[str, Any] | None = None,
    tts: dict[str, Any] | None = None,
    agent_id: str = "hermes-agent",
    mcp_servers: list[dict[str, Any]] | None = None,
    system_prompt: str = "",
    personality_name: str = "",
    delegation: dict[str, Any] | None = None,
    auxiliary_tasks: dict[str, dict[str, Any]] | None = None,
    custom_providers: list[dict[str, Any]] | None = None,
) -> str:
    """Render the Hermes config.yaml via Jinja2.

    Variable shape matches the template's docstring (see
    ``src/hal0/agents/hermes_templates/config.yaml.j2``). Jinja2 is
    pinned in pyproject so the dep is always present in production
    bootstraps — no fallback needed.

    Phase 6/7/8 inputs:
      mcp_servers      — list of {name, url, private, timeout, usage_hint}
                         from Phase 6's allowlist+probe. Defaults to the
                         builtin pair when caller omits (preserves
                         pre-PR-3 behavior for tests that haven't been
                         updated).
      system_prompt    — persona-rendered prelude (Phase 7); empty string
                         falls back to upstream's default prompt.
      personality_name — display name for upstream's CLI personality
                         picker (Phase 8 cosmetic; the system prompt
                         already carries the persona's prelude).

    Role-slot inputs (feat/hermes-role-slots):
      delegation       — dict {model, base_url, provider} or None. When
                         set, renders the ``delegation:`` block so
                         subagents run on the ``agent-hermes`` slot's
                         model. None → block omitted → subagents inherit
                         the chat model.
      auxiliary_tasks  — dict task→{provider, model, base_url} driving the
                         ``auxiliary:`` block. Defaults (when omitted) to
                         the all-provider:"main" inventory so callers that
                         predate role-slots keep the original behavior.
      custom_providers — list[dict] ({name, base_url, models}) or None.
                         Per-model context_length lookup keyed by model_id
                         under the hal0 base_url. None → block omitted.
                         NOTE: ``model.context_length`` is intentionally
                         NOT rendered — hermes treats it as a global
                         override that bleeds onto cloud models; per-model
                         context lives here instead.
    """
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(CONFIG_TEMPLATE_PATH.parent)),
        keep_trailing_newline=True,
        autoescape=False,  # YAML output — escaping would corrupt literal strings.
    )
    tpl = env.get_template(CONFIG_TEMPLATE_PATH.name)
    return tpl.render(
        primary=primary,
        chat_slots=chat_slots or [],
        stt=stt,
        tts=tts,
        agent_id=agent_id,
        mcp_servers=mcp_servers if mcp_servers is not None else _default_mcp_servers(),
        system_prompt=system_prompt,
        personality_name=personality_name,
        delegation=delegation,
        auxiliary_tasks=(
            auxiliary_tasks if auxiliary_tasks is not None else _default_auxiliary_tasks()
        ),
        custom_providers=custom_providers,
    )


def _default_auxiliary_tasks() -> dict[str, dict[str, Any]]:
    """All-``provider:"main"`` auxiliary inventory.

    Used when a caller renders without resolving live slots. Matches the
    pre-role-slots hard-coded template block (vision / web_extract /
    session_search) plus the rest of the confirmed task keys so the
    rendered ``auxiliary:`` block is always fully populated.
    """
    tasks: dict[str, dict[str, Any]] = {}
    for task in (*_MAIN_AUX_TASKS, *_UTILITY_AUX_TASKS):
        tasks[task] = {"provider": "main", "model": "", "base_url": ""}
    return tasks


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge — overlay wins; nested dicts merge."""
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_overrides(rendered_yaml: str, overrides_path: Path) -> str:
    """Deep-merge ``overrides_path`` (if present) on top of rendered YAML.

    Re-emits YAML via stdlib (json round-trip is the fallback when
    PyYAML isn't installed — the resulting JSON is still valid YAML).
    """
    if not overrides_path.exists():
        return rendered_yaml
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return rendered_yaml  # PyYAML not installed; ship as-is.
    base = yaml.safe_load(rendered_yaml) or {}
    overlay = yaml.safe_load(overrides_path.read_text()) or {}
    merged = _deep_merge(base, overlay)
    return yaml.safe_dump(merged, sort_keys=False, default_flow_style=False)


OVERRIDES_PATH = Path("/etc/hal0/agents/hermes/overrides.yaml")


def _personas_root_for(state: BootstrapState) -> Path:
    """Resolve the personas dir for a given BootstrapState.

    Defaults to ``$HERMES_HOME/personas`` so tests (which point
    ``hermes_home`` at ``tmp_path``) get a writeable location without
    monkey-patching the personas module's constant. Operators on the
    canonical install path get the same ``/var/lib/hal0/.hermes/
    personas/`` location they'd see from the personas-module default.
    """
    return Path(state.hermes_home) / "personas"


def _active_persona_render(
    state: BootstrapState,
    *,
    mcp_servers: list[dict[str, Any]] | None = None,
    personas_root: Path | None = None,
) -> tuple[str, str]:
    """Look up the active persona + compose the system-prompt prelude.

    Returns ``(system_prompt, personality_name)``. When no personas have
    been seeded yet (very first config_write before persona_seed runs),
    falls back to ``("", "")`` so the render still succeeds — the
    second pass after persona_seed lands the real prelude. The persona
    layer is intentionally optional so an operator who never seeds one
    can still get a functional config; the dashboard surfaces the
    "no persona" state via the empty system_prompt.
    """
    from hal0.agents import personas as _personas

    if personas_root is not None:
        root = personas_root
    else:
        root = _personas_root_for(state)
        # Back-compat: if nothing's been seeded under hermes_home and the
        # legacy /var/lib path still has the active pointer, fall back to
        # it. New installs always use the hermes_home-scoped path.
        if not root.exists() and _personas.PERSONAS_ROOT.exists():
            root = _personas.PERSONAS_ROOT
    active_id = _personas.get_active(root=root)
    if active_id is None:
        return ("", "")
    try:
        persona = _personas.load_persona(active_id, root=root)
    except (_personas.PersonaError, FileNotFoundError) as exc:
        log.warning("hermes_provision.persona_load_failed", id=active_id, error=str(exc))
        return ("", "")
    chat_slots_summary = mcp_servers or _default_mcp_servers()
    prompt = _personas.build_prompt_addendum(persona, mcp_servers=chat_slots_summary)
    return (prompt, persona.display_name)


def _phase_config_write(state: BootstrapState) -> PhaseResult:
    """Atomically render ``$HERMES_HOME/config.yaml`` from the template.

    PR-3 overhaul: passes chat_slots + persona-rendered system_prompt +
    probed mcp_servers list so a single-shot bootstrap renders the full
    aliases block, the persona prelude, AND the MCP registration block
    on the first pass. Pre-PR-3, Phase 9 (model_automap) re-rendered
    half of these post-hoc; that's now demoted to an idempotency check.

    Idempotent: hash-equal output skips the write. Overrides at
    ``/etc/hal0/agents/hermes/overrides.yaml`` deep-merge on top.
    """
    hermes_home = Path(state.hermes_home)
    config_path = hermes_home / "config.yaml"
    primary_raw = _resolve_primary_slot()
    # The template names the dict keys ``model_id``/``backend_url``;
    # _resolve_primary_slot returns ``model``/``base_url`` for less
    # cognitive load at call sites. Translate at the seam.
    primary = {
        "model_id": primary_raw["model"],
        "backend_url": primary_raw["base_url"],
        "context_length": primary_raw["context_length"],
    }
    # PR-3 Phase 5: pull chat_slots into the first render so the
    # ``model_aliases:`` block lands on the first config_write pass
    # (Phase 9 used to be the only place this worked).
    slots_all = _fetch_slots()
    chat_slots = _collect_chat_slots(slots_all, contexts=_fetch_model_contexts())
    # feat/hermes-role-slots: resolve per-role models from live slot NAMES.
    # delegation ← `agent-hermes` slot; auxiliary ← `utility` slot. Both
    # talk to hal0's /v1 endpoint (same base_url as the main model), so a
    # missing slot degrades safely (delegation omitted / aux → "main").
    hal0_v1_base = primary["backend_url"]
    delegation = _resolve_delegation(slots_all, hal0_base_url=hal0_v1_base)
    auxiliary_tasks = _resolve_auxiliary_tasks(slots_all, hal0_base_url=hal0_v1_base)
    # Per-model context_length lives in custom_providers (NOT the global
    # model.context_length override) so cloud models keep their own ctx.
    custom_providers = _resolve_custom_providers(chat_slots, hal0_base_url=hal0_v1_base)
    # PR-3 Phase 6: probe-driven mcp_servers list. For the very first
    # config_write (before mcp_wire runs the live probe) we fall back to
    # the default inventory; mcp_wire then captures the probed shape and
    # config gets re-rendered idempotently on Phase 9 / next bootstrap.
    cached_servers = (state.phases.get("mcp_wire") or {}).get("details", {}).get("rendered_servers")
    mcp_servers = cached_servers if isinstance(cached_servers, list) and cached_servers else None
    system_prompt, personality_name = _active_persona_render(state, mcp_servers=mcp_servers)
    rendered = _render_config_yaml(
        primary=primary,
        chat_slots=chat_slots,
        agent_id=state.agent_id,
        mcp_servers=mcp_servers,
        system_prompt=system_prompt,
        personality_name=personality_name,
        delegation=delegation,
        auxiliary_tasks=auxiliary_tasks,
        custom_providers=custom_providers,
    )
    rendered = _apply_overrides(rendered, OVERRIDES_PATH)
    new_hash = content_hash(rendered)

    if config_path.exists() and content_hash(config_path.read_text(encoding="utf-8")) == new_hash:
        return PhaseResult(
            status=PhaseStatus.OK,
            hash=new_hash,
            details={
                "config_path": str(config_path),
                "unchanged": True,
                "chat_slot_count": len(chat_slots),
                "persona": personality_name or None,
                "mcp_server_count": len(mcp_servers) if mcp_servers else 0,
            },
        )

    hermes_home.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(".yaml.tmp")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, config_path)
    return PhaseResult(
        status=PhaseStatus.OK,
        hash=new_hash,
        details={
            "config_path": str(config_path),
            "primary_model": primary["model_id"],
            "chat_slot_count": len(chat_slots),
            "persona": personality_name or None,
            "mcp_server_count": len(mcp_servers) if mcp_servers else 0,
            "delegation_model": (delegation or {}).get("model"),
            "auxiliary_utility_model": _utility_aux_model(auxiliary_tasks),
        },
    )


def _utility_aux_model(auxiliary_tasks: dict[str, dict[str, Any]] | None) -> str | None:
    """Surface the utility-slot model used by the aux compaction group.

    Returns the ``compression`` task's model (representative of the whole
    utility group) for self_report visibility, or ``None`` when the group
    degraded to provider:"main".
    """
    if not auxiliary_tasks:
        return None
    comp = auxiliary_tasks.get("compression") or {}
    return comp.get("model") or None


# ── Phase F: mcp_wire ───────────────────────────────────────────────────────
#
# Verifies hal0-admin + hal0-memory MCP servers respond to tools/list +
# records the discovered tool surface in provision.json for downstream
# phases (#243 namespace_register, #245 model_automap). Honors ADR-0013:
# the per-agent allow-list at /etc/hal0/agents/hermes.toml gates which
# servers the bootstrap will attempt to connect to.


AGENT_ALLOWLIST_PATH = Path("/etc/hal0/agents/hermes.toml")


def _load_agent_allowlist(
    path: Path | None = None,
) -> dict[str, dict[str, Any]] | None:
    """Read ``[mcp.servers.*]`` blocks from the per-agent allow-list.

    Returns ``None`` when the file is missing (the agent installer
    drops it during install; absence means "allow everything that's
    builtin" per ADR-0013's installer-managed convention). Returns
    ``{server_name: section_dict}`` when present.
    """
    target = path or AGENT_ALLOWLIST_PATH
    if not target.exists():
        return None
    try:
        import tomllib
    except ImportError:  # pragma: no cover — Python 3.11+ always has it
        return None
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    mcp = data.get("mcp") or {}
    servers = mcp.get("servers") or {}
    return servers if isinstance(servers, dict) else None


def _probe_mcp_server(
    url: str,
    *,
    agent_id: str,
    timeout: float = 5.0,
    private: bool = False,
) -> dict[str, Any]:
    """List the tools an MCP server advertises. Returns shape:
    ``{"ok": bool, "tools": [...], "error": str | None}``.

    Speaks FastMCP Streamable-HTTP transport: POST ``<url>/mcp`` with
    an ``initialize`` request, capture the ``Mcp-Session-Id`` response
    header, then POST ``tools/list`` with that session id. Accepts
    both raw-JSON and ``text/event-stream`` framed responses (FastMCP
    picks either depending on Accept).

    Uses stdlib urllib because the bootstrap can't assume httpx is
    installed in the hal0 daemon's venv (it usually is — but keeping
    this stdlib-only means env_probe can run on a minimal install).
    """
    import contextlib
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    transport_url = url.rstrip("/") + "/mcp"
    base_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-hal0-Agent": agent_id,
    }
    if private:
        base_headers["X-hal0-Private"] = "1"

    def _parse_jsonrpc(body: str) -> dict[str, Any]:
        body = (body or "").strip()
        if not body:
            return {}
        if body[0] == "{":
            return json.loads(body)
        # text/event-stream framing: `event: message\ndata: {...}\n\n`
        for line in body.splitlines():
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
        return {}

    def _post(payload: dict[str, Any], session_id: str | None) -> tuple[dict[str, Any], str | None]:
        headers = dict(base_headers)
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        req = Request(
            transport_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                returned_sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get(
                    "mcp-session-id"
                )
        except HTTPError as exc:  # 4xx/5xx — still try to parse the body
            body = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
            returned_sid = exc.headers.get("Mcp-Session-Id") if exc.headers else None
            parsed = _parse_jsonrpc(body)
            if isinstance(parsed, dict) and parsed.get("error"):
                return parsed, returned_sid
            raise
        return _parse_jsonrpc(body), returned_sid

    try:
        init, sid = _post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "hal0-bootstrap-probe", "version": "0.1"},
                },
            },
            session_id=None,
        )
        if isinstance(init, dict) and init.get("error"):
            return {"ok": False, "tools": [], "error": f"initialize: {init['error']}"}

        # Fire-and-forget; some FastMCP versions gate tools/list on it.
        with contextlib.suppress(URLError, HTTPError, OSError, TimeoutError):
            _post(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                session_id=sid,
            )

        tools_resp, _ = _post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            session_id=sid,
        )
    except (URLError, HTTPError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return {"ok": False, "tools": [], "error": str(exc)}

    if isinstance(tools_resp, dict) and tools_resp.get("error"):
        return {"ok": False, "tools": [], "error": f"tools/list: {tools_resp['error']}"}

    tools: list[str] = []
    result = tools_resp.get("result") if isinstance(tools_resp, dict) else None
    if isinstance(result, dict):
        raw_tools = result.get("tools") or []
        if isinstance(raw_tools, list):
            tools = [t.get("name") for t in raw_tools if isinstance(t, dict) and t.get("name")]
    return {"ok": True, "tools": tools, "error": None}


def _phase_mcp_wire(state: BootstrapState) -> PhaseResult:
    """Verify the two hal0-bundled MCP servers respond + record their tool list.

    ADR-0013 compliance: when an allow-list exists at
    ``/etc/hal0/agents/hermes.toml``, the bootstrap only attempts
    connection for servers listed under ``[mcp.servers.*]``. A
    missing entry (or a missing allow-list file entirely) is a
    warning, NOT a hard fail — bootstrap continues so the operator
    can wire the missing piece by hand after install.
    """
    allowlist = _load_agent_allowlist()
    # PR-3 Phase 6: source the canonical inventory from
    # ``_default_mcp_servers()`` so the probe loop and the template loop
    # see identical entries. Allowlist trims this; probe drops failures.
    servers: list[dict[str, Any]] = list(_default_mcp_servers())

    results: dict[str, Any] = {}
    warnings: list[str] = []
    rendered_servers: list[dict[str, Any]] = []
    for entry in servers:
        name = entry["name"]
        if allowlist is not None and name not in allowlist:
            warnings.append(
                f"{name}: not listed in /etc/hal0/agents/hermes.toml "
                f"[mcp.servers.{name}] — skipping per ADR-0013"
            )
            results[name] = {"status": "skipped_by_allowlist"}
            continue
        probe = _probe_mcp_server(entry["url"], agent_id=state.agent_id, private=entry["private"])
        if not probe["ok"]:
            warnings.append(f"{name}: {probe['error']}")
            results[name] = {"status": "degraded", "error": probe["error"]}
            # Still render the server entry so the agent can retry on a
            # later turn — degraded probe is usually just "MCP server
            # warming up", not a permanent failure. The system prompt
            # already tells the agent to retry on connection errors.
            rendered_servers.append(entry)
            continue
        results[name] = {
            "status": "ok",
            "tool_count": len(probe["tools"]),
            "tools": probe["tools"],
        }
        rendered_servers.append(entry)

    # Even with warnings we return OK — degraded MCP connectivity is
    # surfaced for smoke_tests + self_report to display, not a fatal
    # bootstrap blocker (per ADR-0013 + the plan §9 contract).
    #
    # ``rendered_servers`` is consumed by Phase 5 (config_write) on the
    # next bootstrap run — it's how Phase 6 hands the template the live
    # probe result. The list survives via the persisted ``provision.json``
    # checkpoint so re-runs use the same shape.
    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "servers": results,
            "allowlist_present": allowlist is not None,
            "warnings": warnings,
            "rendered_servers": rendered_servers,
        },
    )


# ── Phase H.5: persona_seed (PR-3) ──────────────────────────────────────────
#
# Seeds the operator-visible personas hal0 manages on top of Hermes's own
# personality slot. Two personas land on first install — ``hermes``
# (default, helpful) and ``coder`` (software focus, narrower auto-approve).
# Operator edits survive re-runs; ``--repair`` re-writes the seeds back to
# their canonical content. The active pointer flips to ``hermes`` only
# when missing or dangling — an operator-chosen active persona survives
# re-seed.


def _phase_persona_seed(state: BootstrapState) -> PhaseResult:
    """Seed the default personas + ``active.txt`` pointer.

    Phase 8 (PR-3): idempotent persona file write. The next config_write
    pass picks up the active persona's system_prompt and renders it into
    the prelude block.

    Personas land under ``$HERMES_HOME/personas`` (NOT the personas
    module's default ``/var/lib/hal0/.hermes/personas``) so the
    seed phase honours the state's ``hermes_home`` field — tests get a
    writeable location for free, and on the canonical install path the
    two resolve to the same directory.
    """
    from hal0.agents import personas as _personas

    root = _personas_root_for(state)
    # Honor ``--repair`` by forcing seed overwrite. Operator edits stand
    # in the steady-state case; repair explicitly resets to known-good.
    overwrite = bool(state.phases.get("_repair_flag"))
    written = _personas.seed_default_personas(
        agent_id=state.agent_id,
        root=root,
        overwrite=overwrite,
    )
    active = _personas.get_active(root=root)
    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "personas_root": str(root),
            "active": active,
            "seeded": [p.id for p in written],
            "all_personas": [p.id for p in _personas.list_personas(root=root)],
        },
    )


# ── Phase G: context_link ───────────────────────────────────────────────────
#
# Renders SOUL.md + HERMES.md + AGENTS.md from Jinja2 templates and
# symlinks hal0-bundled skills into /etc/hal0/agent-skills/. The
# templates live next to config.yaml.j2 in the wheel's package-data
# (see pyproject.toml [tool.hatch.build.targets.wheel.force-include]
# — if the package layout shifts, the template loader fails fast at
# import-time which surfaces in CI before bootstrap ever runs).
#
# Per #244 sharpening: SOUL.md render failure falls back to upstream's
# DEFAULT_SOUL_MD; HERMES.md / AGENTS.md render failures log + skip.
# Symlink-create is idempotent (only relinks when target differs).


CONTEXT_TEMPLATE_DIR = CONFIG_TEMPLATE_PATH.parent
HAL0_BUNDLED_SKILLS = Path("/usr/share/hal0/skills")
ETC_HAL0_DIR = Path("/etc/hal0")
ETC_HAL0_AGENT_SKILLS = ETC_HAL0_DIR / "agent-skills"

# STATE.md — the volatile live snapshot rewritten on every restart / model
# swap — lives under the hal0-owned /var/lib/hal0 rather than the root-owned
# /etc/hal0 (#473): the hermes unit runs User=hal0 with ProtectSystem=strict,
# so its ExecStartPre `render-context` can only write to paths in
# ReadWritePaths — /var/lib/hal0 is already there, /etc/hal0 is not. HERMES.md
# (structural, cwd-injected from /etc/hal0) and the rest of the config stay
# under ETC_HAL0_DIR, written at provision time as root.
RUNTIME_SNAPSHOT_DIR = Path("/var/lib/hal0")


def _latest_env_snapshot(hermes_home: Path) -> dict[str, Any]:
    """Load the most recent env-<ts>.json snapshot env_probe wrote.

    Falls back to empty dict when no snapshot exists — templates use
    Jinja2 ``default`` filters so partial data is OK.
    """
    candidates = sorted(hermes_home.glob("env-*.json"))
    if not candidates:
        return {}
    try:
        return json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _render_template(name: str, **vars_: Any) -> str:
    """Render a Jinja2 template from the hermes_templates dir."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(CONTEXT_TEMPLATE_DIR)),
        keep_trailing_newline=True,
        autoescape=False,  # Markdown output; escaping would corrupt prose.
    )
    return env.get_template(name).render(**vars_)


def _atomic_write(path: Path, content: str) -> str:
    """Tmp-write + rename for atomicity. Returns the sha256 of content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return content_hash(content)


def _safe_symlink(target: Path, link: Path) -> bool:
    """Create ``link`` -> ``target`` only when the link doesn't already
    resolve there. Returns True when a (re)link happened."""
    if link.is_symlink():
        try:
            if os.readlink(link) == str(target):
                return False
        except OSError:
            pass
        link.unlink()
    elif link.exists():
        # Existing non-symlink file at link path — leave alone (operator
        # may have hand-managed it; we don't clobber).
        return False
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(target), str(link))
    return True


def _mirror_bundled_skills(src_root: Path, dst_root: Path) -> tuple[list[str], list[str]]:
    """Symlink every immediate child of ``src_root`` into ``dst_root``.

    Returns ``(linked, warnings)``. Missing src is a warning, not a
    failure — bundled skills are optional in a dev install.
    """
    linked: list[str] = []
    warnings: list[str] = []
    if not src_root.exists():
        warnings.append(f"hal0-bundled skills source {src_root} not present; nothing to mirror")
        return linked, warnings
    dst_root.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src_root.iterdir()):
        link = dst_root / entry.name
        try:
            if _safe_symlink(entry, link):
                linked.append(entry.name)
        except OSError as exc:
            warnings.append(f"symlink {entry.name}: {exc}")
    return linked, warnings


def _phase_context_link(state: BootstrapState) -> PhaseResult:
    """Render persona + context files; mirror bundled skills.

    Files rendered (atomically):
      - $HERMES_HOME/SOUL.md
      - /etc/hal0/HERMES.md
      - /etc/hal0/AGENTS.md
      - $HERMES_HOME/memories/HOST.md (symlink -> /etc/hal0/HERMES.md)

    SOUL.md render failure falls back to a minimal hal0-themed default
    (we can't import upstream's DEFAULT_SOUL_MD from inside hal0; the
    fallback is short + accurate). HERMES.md + AGENTS.md render
    failures log + skip per #244 sharpening.
    """
    hermes_home = Path(state.hermes_home)
    snapshot = _latest_env_snapshot(hermes_home)
    env_report = snapshot.get("env_report", {}) if isinstance(snapshot, dict) else {}

    # Resolve live slot state so HERMES.md actually advertises the
    # active primary + chat slots (otherwise the dashboard "no chat
    # slots loaded" branch always wins — surprising operators who
    # have a working primary and trip the
    # `hermes_md_contains_primary` smoke test).
    slots_all: list[dict[str, Any]] = []
    # _fetch_slots is already failure-tolerant (returns [] on transport
    # error). No try/except needed here — it can't raise.
    slots_all = _fetch_slots()
    chat_slots = _collect_chat_slots(slots_all, contexts=_fetch_model_contexts())
    primary_raw = _resolve_primary_slot()
    primary_for_template: dict[str, Any] | None = None
    primary_alias = "primary"
    primary_slot = next(
        (s for s in slots_all if isinstance(s, dict) and s.get("name") == "primary"), None
    )
    if primary_slot:
        primary_alias = _slot_alias(primary_slot)
    # primary_raw["model"] is a real model_id when a slot is live, or
    # the placeholder string "primary" when nothing is loaded — treat
    # the placeholder as "no primary" for template purposes.
    if primary_raw["model"] and primary_raw["model"] != "primary":
        primary_for_template = {
            "alias": primary_alias,
            "model_id": primary_raw["model"],
            "backend_url": primary_raw["base_url"],
        }

    vars_ = {
        "env": env_report,
        "hal0_version": _hal0_version_string(),
        "hermes_version": _hermes_version_pin(),
        "primary": primary_for_template,
        "chat_slots": chat_slots,
        "peer_agents": [],
    }

    rendered: dict[str, str] = {}
    warnings: list[str] = []
    fallback_soul = (
        "# Identity\n\n"
        "You are the hal0 admin agent — the right-hand assistant for this "
        "homelab inference platform. Use `hal0_admin` MCP tools to probe slot "
        "state before changes; use `hal0_memory` for durable facts.\n"
    )
    try:
        rendered["SOUL.md"] = _render_template("SOUL.md.j2", **vars_)
    except Exception as exc:
        warnings.append(f"SOUL.md render: {exc}; falling back to default")
        rendered["SOUL.md"] = fallback_soul

    for tpl_name, _out_name in (
        ("AGENTS.md.j2", "AGENTS.md"),
        ("MCP-CLIENTS.md.j2", "MCP-CLIENTS.md"),
    ):
        try:
            rendered[_out_name] = _render_template(tpl_name, **vars_)
        except Exception as exc:
            warnings.append(f"{tpl_name} render: {exc}; skipping")

    details: dict[str, Any] = {"warnings": warnings, "rendered": {}, "links": []}

    soul_path = hermes_home / "SOUL.md"
    h = _atomic_write(soul_path, rendered["SOUL.md"])
    details["rendered"]["SOUL.md"] = {"path": str(soul_path), "sha256": h}

    # STATE.md + HERMES.md are the live files — render via the one shared
    # path used by the per-restart / per-swap writers. Best-effort: failure
    # here must not fail bootstrap (SOUL/AGENTS already written).
    try:
        # NB: render_live_context re-fetches /api/slots + /v1/models itself
        # (separate from the vars_ fetch above). Acceptable at bootstrap
        # frequency; keeps it usable standalone from the restart/swap writers.
        live = render_live_context(hermes_home=hermes_home)
        details["rendered"]["STATE.md"] = {"path": live["state_path"]}
        details["rendered"]["HERMES.md"] = {
            "path": str(ETC_HAL0_DIR / "HERMES.md"),
            "written": live["hermes_written"],
        }
        if live["degraded"]:
            warnings.append("STATE.md rendered with daemon degraded")
        if live.get("hermes_error"):
            warnings.append(f"HERMES.md render: {live['hermes_error']}")
        # render_live_context writes HERMES.md but not the HOST.md mirror;
        # re-establish the symlink that the memory tier reads.
        hpath = ETC_HAL0_DIR / "HERMES.md"
        if hpath.exists():
            host_md = hermes_home / "memories" / "HOST.md"
            if _safe_symlink(hpath, host_md):
                details["links"].append(str(host_md))
    except Exception as exc:  # best-effort
        warnings.append(f"render_live_context: {exc}")

    if "AGENTS.md" in rendered:
        try:
            ETC_HAL0_DIR.mkdir(parents=True, exist_ok=True)
            apath = ETC_HAL0_DIR / "AGENTS.md"
            h = _atomic_write(apath, rendered["AGENTS.md"])
            details["rendered"]["AGENTS.md"] = {"path": str(apath), "sha256": h}
        except OSError as exc:
            warnings.append(f"AGENTS.md write to /etc/hal0: {exc}")

    if "MCP-CLIENTS.md" in rendered:
        try:
            ETC_HAL0_DIR.mkdir(parents=True, exist_ok=True)
            mcppath = ETC_HAL0_DIR / "MCP-CLIENTS.md"
            h = _atomic_write(mcppath, rendered["MCP-CLIENTS.md"])
            details["rendered"]["MCP-CLIENTS.md"] = {"path": str(mcppath), "sha256": h}
        except OSError as exc:
            warnings.append(f"MCP-CLIENTS.md write to /etc/hal0: {exc}")

    # Mirror bundled skills last so a failure here doesn't block context files.
    linked, skill_warnings = _mirror_bundled_skills(HAL0_BUNDLED_SKILLS, ETC_HAL0_AGENT_SKILLS)
    details["bundled_skills_linked"] = linked
    warnings.extend(skill_warnings)
    details["warnings"] = warnings

    return PhaseResult(status=PhaseStatus.OK, details=details)


# ── Phase H: namespace_register ─────────────────────────────────────────────
#
# Writes the Hermes-Agent identity card to the `agents` Cognee dataset
# (ADR-0011). Card is immutable post-write — re-bootstrap deletes the
# existing card and writes a fresh one to refresh metadata (the only
# legitimate post-install write). On hal0-memory failure, log + continue
# (per #243 sharpening + ADR-0013); the card is nice-to-have and
# bootstrap MUST NOT fail because the peer registry is down.


AGENT_IDENTITY_TAG = "agent-identity"
AGENTS_DATASET = "agents"


def _hermes_version_pin() -> str:
    req = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "requirements.txt"
    try:
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("hermes-agent=="):
                return line.split("==", 1)[1].split("#", 1)[0].strip()
    except OSError:
        pass
    return "unknown"


def _hal0_version_string() -> str:
    try:
        from hal0 import __version__

        return __version__
    except (ImportError, AttributeError):
        return "unknown"


def _build_identity_card(state: BootstrapState) -> dict[str, Any]:
    """Schema v1 per ADR-0011 §4. Text + structured metadata."""
    return {
        "text": (
            "I am Hermes, the hal0 admin agent. I have read/write access to the slot "
            "lifecycle and the memory store on this host. I can do generalist chat and "
            "code review on the LAN."
        ),
        "tags": [AGENT_IDENTITY_TAG, "hermes"],
        "dataset": AGENTS_DATASET,
        "metadata": {
            "agent_id": state.agent_id,
            "display_name": "Hermes (hal0 admin)",
            "namespace": f"private:{state.agent_id}",
            "roles": ["homelab-admin", "generalist-chat", "memory-curator"],
            "endpoint": {
                "type": "mcp-serve",
                "url": "http://127.0.0.1:8081/mcp",
                "transport": "streamable-http",
            },
            "delegation": {
                "accepts_tasks_from": ["claude-code", "pi-coder", "user"],
                "max_concurrent": 3,
            },
            "hal0_state": {
                "registered_at": _utcnow(),
                "bootstrap_version": 1,
                "hal0_version": _hal0_version_string(),
                "hermes_version": _hermes_version_pin(),
            },
        },
    }


def _mcp_memory_call(
    method: str,
    params: dict[str, Any],
    *,
    agent_id: str,
    base_url: str = "http://127.0.0.1:8080",
    timeout: float = 5.0,
    private: bool = False,
) -> dict[str, Any]:
    """Call the hal0-memory surface. Returns ``{ok, result?, error?}``.

    **Was** a one-shot JSON-RPC POST to ``/mcp/memory`` — broken per
    #302 because real FastMCP requires the initialize handshake at
    ``/mcp/memory/mcp`` with session-tagged subsequent calls. That made
    every call here silently fail with HTTP 405 + the failure-tolerant
    path in :func:`_phase_namespace_register` swallowed the error,
    meaning identity cards were never being written.

    **Now** translates the MCP ``tools/call`` shape to the REST shims
    at ``/api/memory/{add,search,delete}`` (added in #302). The method/
    params shape is preserved so existing call sites don't change.

    Supported method/tool combinations:
      - ``method="tools/call"``, ``params.name="memory_search"`` → POST /api/memory/search
      - ``method="tools/call"``, ``params.name="memory_add"`` → POST /api/memory/add
      - ``method="tools/call"``, ``params.name="memory_delete"`` → POST /api/memory/delete

    Anything else returns ``{"ok": False, "error": "unsupported method"}``
    — proper MCP tool calls still need an MCP SDK client. That's tracked
    as a v0.4 cleanup (see #302 comment).
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    base_url = base_url.rstrip("/")

    # Translate MCP envelope → REST endpoint.
    if method == "tools/call" and isinstance(params, dict):
        tool = params.get("name")
        arguments = params.get("arguments") or {}
        route_map = {
            "memory_search": "/api/memory/search",
            "memory_add": "/api/memory/add",
            "memory_delete": "/api/memory/delete",
        }
        path = route_map.get(tool)
        if path is None:
            return {"ok": False, "error": f"unsupported tool {tool!r}"}
        body_bytes = json.dumps(arguments).encode("utf-8")
        url = f"{base_url}{path}"
    else:
        return {"ok": False, "error": f"unsupported method {method!r}"}

    headers = {"Content-Type": "application/json", "X-hal0-Agent": agent_id}
    if private:
        headers["X-hal0-Private"] = "1"
    req = Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        # Surface the body if it's a hal0 error envelope so the warning
        # message in the caller is operator-actionable.
        try:
            err_body = json.loads(exc.read().decode("utf-8"))
            err_msg = (err_body.get("error") or {}).get("message") or str(exc)
        except Exception:
            err_msg = str(exc)
        return {"ok": False, "error": err_msg}
    except (URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return {"ok": False, "error": str(exc)}
    # REST shims return the wrapper's dict directly (e.g.
    # {"items": [...]} for search, {"id": ..., "timestamp": ...} for
    # add). Preserve the old ``result`` envelope key for call-site
    # compat — every reader does ``call["result"].get("items")`` etc.
    return {"ok": True, "result": data}


def _phase_namespace_register(state: BootstrapState) -> PhaseResult:
    """Write the Hermes identity card to the `agents` Cognee dataset.

    Idempotency: search for an existing card by ``agent_id`` first;
    if present, delete it before writing the fresh one (cards are
    immutable per ADR-0011 §2, but bootstrap rewrites refresh the
    snapshot of hal0_version + hermes_version).

    Failure mode: any MCP transport error logs + returns OK with a
    warning. Bootstrap MUST NOT block on registry unavailability.
    """
    card = _build_identity_card(state)
    warnings: list[str] = []

    # Look up existing card so re-bootstrap doesn't accumulate duplicates.
    search = _mcp_memory_call(
        "tools/call",
        {
            "name": "memory_search",
            "arguments": {
                "query": state.agent_id,
                "tags": [AGENT_IDENTITY_TAG],
                "dataset": AGENTS_DATASET,
                "limit": 50,
            },
        },
        agent_id=state.agent_id,
    )
    existing_ids: list[str] = []
    if search["ok"] and isinstance(search["result"], dict):
        items = search["result"].get("items") or []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            md = item.get("metadata") or {}
            if md.get("agent_id") == state.agent_id and item.get("id"):
                existing_ids.append(item["id"])
    elif not search["ok"]:
        warnings.append(f"memory_search: {search['error']}")

    if existing_ids:
        deleted = _mcp_memory_call(
            "tools/call",
            {"name": "memory_delete", "arguments": {"ids": existing_ids}},
            agent_id=state.agent_id,
        )
        # #448: a delete that returns HTTP 200 but removes fewer ids than
        # requested (e.g. the custom-dataset skip bug behind #446) looks
        # identical to a real prune. Re-adding on top of un-deleted priors
        # floods the Peer view with duplicates. Verify the count, not just
        # the transport status — on any shortfall, skip the rewrite.
        if not deleted["ok"]:
            warnings.append(f"memory_delete: {deleted['error']}")
            return PhaseResult(
                status=PhaseStatus.OK,
                details={
                    "registered": False,
                    "refreshed_existing": False,
                    "warnings": warnings,
                    "card": card,
                },
                reason="memory_delete failed; not rewriting to avoid duplicate accumulation",
            )
        removed = (deleted.get("result") or {}).get("deleted", 0)
        if removed != len(existing_ids):
            warnings.append(
                f"memory_delete: requested {len(existing_ids)}, removed {removed} "
                "— not rewriting to avoid duplicate accumulation"
            )
            return PhaseResult(
                status=PhaseStatus.OK,
                details={
                    "registered": False,
                    "refreshed_existing": False,
                    "warnings": warnings,
                    "card": card,
                },
                reason="memory_delete count mismatch; not rewriting to avoid duplicate accumulation",
            )

    add = _mcp_memory_call(
        "tools/call",
        {"name": "memory_add", "arguments": card},
        agent_id=state.agent_id,
    )
    if not add["ok"]:
        # Bootstrap continues — the card is nice-to-have, not a blocker.
        warnings.append(f"memory_add: {add['error']}")
        return PhaseResult(
            status=PhaseStatus.OK,
            details={"registered": False, "warnings": warnings, "card": card},
            reason="hal0-memory unreachable; identity card not registered (continuing)",
        )

    memory_id = None
    if isinstance(add["result"], dict):
        memory_id = add["result"].get("id")
    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "registered": True,
            "memory_id": memory_id,
            "card": card,
            "warnings": warnings,
            "refreshed_existing": bool(existing_ids),
        },
    )


# ── Phase I: model_automap ──────────────────────────────────────────────────
#
# Walks the live slot/model surface and rewrites the [model_aliases]
# block of $HERMES_HOME/config.yaml so /model <alias> inside Hermes
# picks the right backend. Embed/rerank/img stay UNWIRED per grilling
# Q6 (no top-level embed surface in Hermes; memory MCP handles it).


HAL0_API_URL = "http://127.0.0.1:8080"


def _fetch_slots() -> list[dict[str, Any]]:
    """Pull the full slot list from the local hal0 daemon.

    Returns an empty list when the daemon is unreachable — the phase
    surfaces ``status=degraded`` so downstream consumers can tell the
    diff between "no slots" and "couldn't ask."
    """
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    req = Request(f"{HAL0_API_URL}/api/slots", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return []
    if isinstance(data, dict):
        # Some routes wrap in {"slots": [...]}, others return a bare list.
        data = data.get("slots") or []
    return list(data) if isinstance(data, list) else []


def _fetch_model_contexts() -> dict[str, int]:
    """Map gateway model id -> context_length from ``/v1/models``.

    ``/api/slots`` carries no context field, so the slot dict can't supply
    one. The gateway's ``/v1/models`` is the authoritative source (it
    resolves ctx_size/context_size + the model-registry ``defaults``), keyed
    by the slot ALIAS (== the ``/v1/models`` ``id``). Returns ``{}`` when the
    daemon is unreachable or no chat slot is loaded.
    """
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    req = Request(f"{HAL0_API_URL}/v1/models", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return {}
    out: dict[str, int] = {}
    for entry in (data or {}).get("data") or []:
        mid = entry.get("id")
        ctx = entry.get("context_length")
        if mid and isinstance(ctx, int) and ctx > 0:
            out[str(mid)] = ctx
    return out


def _slot_kind(slot: dict[str, Any]) -> str:
    """Best-effort capability classifier — handles a few schema variants."""
    for key in ("capability", "kind", "type"):
        v = slot.get(key)
        if isinstance(v, str) and v:
            return v.lower()
    return ""


def _slot_alias(slot: dict[str, Any]) -> str:
    for key in ("name", "alias", "slug"):
        v = slot.get(key)
        if isinstance(v, str) and v:
            return v
    return "primary"


def _slot_model_id(slot: dict[str, Any]) -> str | None:
    for key in ("model_id", "model", "default_model"):
        v = slot.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _slot_backend_url(slot: dict[str, Any]) -> str:
    for key in ("backend_url", "base_url", "url"):
        v = slot.get(key)
        if isinstance(v, str) and v:
            return v
    return _DEFAULT_PRIMARY_BACKEND_URL


_DEFAULT_PRIMARY_BACKEND_URL = f"{HAL0_API_URL}/v1"


def _is_ready(slot: dict[str, Any]) -> bool:
    """True iff the slot reports a live/ready state."""
    state = slot.get("state") or slot.get("status") or ""
    return str(state).lower() in {"ready", "running", "loaded", "ok", "online"}


def _slot_context_length(slot: dict[str, Any]) -> int | None:
    """Resolve a slot's effective context length (the value /v1/models
    advertises), or ``None`` when the slot reports none.

    Reads ``context_length`` then ``ctx_size`` — the same precedence
    :func:`_resolve_primary_slot` uses — so the per-model entry in
    ``custom_providers`` matches what the gateway serves.
    """
    raw = slot.get("context_length") or slot.get("ctx_size")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# capability slot `type` (from /api/slots) -> STATE.md rollup label.
_CAPABILITY_TYPE_LABELS = {
    "embedding": "embed",
    "stt": "voice-stt",
    "tts": "voice-tts",
    "image": "img",
    "img": "img",
    "rerank": "rerank",
}


def _collect_capability_rollup(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ready non-chat capability slots, mapped to STATE.md rollup rows.

    Chat (``type=='llm'``) slots are handled by the primary/chat path and
    excluded here. Only ready slots are advertised so we never tell the
    agent about a capability that isn't actually loaded.
    """
    out: list[dict[str, Any]] = []
    for s in slots:
        if not isinstance(s, dict):
            continue
        label = _CAPABILITY_TYPE_LABELS.get((s.get("type") or "").lower())
        if not label:
            continue
        if not _is_ready(s):
            continue
        out.append(
            {
                "capability": label,
                "model_id": _slot_model_id(s),
                "backend": s.get("backend"),
            }
        )
    return out


def _igpu_sclk_mhz(sysfs_root: Path = Path("/sys/class/drm")) -> int | None:
    """Active iGPU shader clock (MHz) from amdgpu sysfs, or None.

    Reads ``pp_dpm_sclk`` and returns the MHz of the active ('*') DPM
    level. Best-effort: any read/parse error returns None so the template
    simply omits the clock line. Tries card0..card3 (Strix Halo dev nodes);
    ``sysfs_root`` is injectable for tests.
    """
    for idx in range(4):
        path = sysfs_root / f"card{idx}" / "device" / "pp_dpm_sclk"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if line.rstrip().endswith("*"):
                # e.g. "2: 2900Mhz *"
                for tok in line.replace("Mhz", " ").replace("MHz", " ").split():
                    if tok.isdigit():
                        return int(tok)
        # no active line on this card — try the next one
    return None


def _state_body_minus_timestamp(text: str) -> str:
    """STATE.md body with the volatile ``_as_of:`` line removed.

    Used for content-hash gating so a regen that finds nothing
    substantive changed does not churn the file (and bust prompt-cache).
    Assumes ``_as_of:`` is not a prefix of any substantive content line
    (guaranteed by STATE.md.j2, which emits it only as the final footer).
    """
    return "\n".join(line for line in text.splitlines() if not line.startswith("_as_of:"))


def render_live_context(
    *,
    hermes_home: Path,
    slots_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Re-probe live slot/capability state; (re)write HERMES.md + STATE.md.

    STATE.md is content-hash gated: rewritten (and its ``_as_of`` line
    bumped) only when the substantive body changes. HERMES.md is written
    atomically (identical content => identical bytes => prompt-cache safe).
    Never raises on a daemon-unreachable read — leaves last-good files and
    reports ``degraded=True``.

    Returns: {"state_written": bool, "hermes_written": bool,
              "degraded": bool, "state_path": str}.
    """
    fetch = slots_fetcher or _fetch_slots
    slots_all = fetch() or []
    # Reachability is independent of slot count. A reachable daemon with
    # zero configured slots is NOT degraded (we render "no chat model
    # loaded" + reachable). degraded == the daemon couldn't be reached at
    # all — in which case we must NOT clobber a last-good snapshot. A
    # non-empty fetch implies the daemon answered, so we only probe health
    # when the slot list came back empty.
    reachable = True if slots_all else _http_get(DAEMON_HEALTH_URL) == 200
    degraded = not reachable

    contexts = _fetch_model_contexts()
    chat_slots = _collect_chat_slots(slots_all, contexts=contexts)
    primary_raw = _resolve_primary_slot(slots_fetcher=lambda: slots_all)

    primary_slot = next(
        (s for s in slots_all if isinstance(s, dict) and s.get("name") == "primary"),
        None,
    )
    primary_for_template: dict[str, Any] | None = None
    if primary_raw["model"] and primary_raw["model"] != "primary":
        primary_for_template = {
            "alias": _slot_alias(primary_slot) if primary_slot else "primary",
            "model_id": primary_raw["model"],
            "backend_url": primary_raw["base_url"],
            "context_length": primary_raw["context_length"],
            "backend": (primary_slot or {}).get("backend"),
        }

    capabilities = _collect_capability_rollup(slots_all)

    # NPU: present from the cached env snapshot; loaded model from any FLM
    # backend slot (NPU LLM path is FastFlowLM).
    env_report = _latest_env_snapshot(hermes_home).get("env_report", {})
    npu_model = next(
        (
            _slot_model_id(s)
            for s in slots_all
            if isinstance(s, dict) and "flm" in str(s.get("backend") or "").lower()
        ),
        None,
    )
    npu = {"present": bool(env_report.get("npu", {}).get("present")), "model_id": npu_model}

    now = now_iso or datetime.datetime.now(datetime.UTC).isoformat()

    state_vars = {
        "primary": primary_for_template,
        "capabilities": capabilities,
        "npu": npu,
        "igpu_sclk_mhz": _igpu_sclk_mhz(),
        "dashboard_url": os.environ.get("HAL0_DASHBOARD_URL", "https://hal0.thinmint.dev"),
        "lemonade_base": os.environ.get("HAL0_LEMONADE_BASE", "http://127.0.0.1:13305"),
        "daemon": "degraded" if degraded else "reachable",
        "as_of": now,
    }
    new_state = _render_template("STATE.md.j2", **state_vars)

    out: dict[str, Any] = {
        "state_written": False,
        "hermes_written": False,
        "degraded": degraded,
        "state_path": str(RUNTIME_SNAPSHOT_DIR / "STATE.md"),
    }

    # STATE.md — content-hash gated (ignore the as_of line). Written under the
    # hal0-owned RUNTIME_SNAPSHOT_DIR so render-context works under the User=hal0
    # / ProtectSystem=strict hermes sandbox (#473).
    RUNTIME_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    state_path = RUNTIME_SNAPSHOT_DIR / "STATE.md"
    existing = ""
    if state_path.exists():
        existing = state_path.read_text(encoding="utf-8")

    # Daemon unreachable but we already have a last-good snapshot: preserve
    # it (spec — never clobber good state with a degraded one, e.g. when
    # ExecStartPre fires before hal0-api is up). Leave mtime stale so the
    # session hook keeps retrying the regen until the daemon returns.
    if degraded and existing:
        return out  # state_written=False, hermes_written=False, degraded=True

    if _state_body_minus_timestamp(existing) != _state_body_minus_timestamp(new_state):
        _atomic_write(state_path, new_state)
        out["state_written"] = True
    elif reachable:
        # Content unchanged, but we just confirmed it current against a
        # reachable daemon — bump mtime so the on_session_start hook's TTL
        # staleness check settles instead of firing a background regen every
        # session forever. mtime is not content, so Hermes's injected text
        # is byte-identical and the prompt-cache prefix stays warm.
        os.utime(state_path, None)

    # HERMES.md — structural map; atomic write (identical content => identical
    # bytes => prompt-cache safe). Render failure is non-fatal.
    try:
        hermes_md = _render_template(
            "HERMES.md.j2",
            env=env_report,
            hal0_version=_hal0_version_string(),
            hermes_version=_hermes_version_pin(),
            primary=primary_for_template,
            chat_slots=chat_slots,
            peer_agents=[],
        )
        hpath = ETC_HAL0_DIR / "HERMES.md"
        if not hpath.exists() or hpath.read_text(encoding="utf-8") != hermes_md:
            _atomic_write(hpath, hermes_md)
            out["hermes_written"] = True
    except Exception as exc:  # best-effort; STATE.md already written
        log.warning("hermes_provision.render_live_context_hermes_failed", error=str(exc))
        out["hermes_error"] = str(exc)

    return out


def _collect_chat_slots(
    slots: list[dict[str, Any]],
    contexts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Filter ``slots`` to chat-capable entries (``type=="llm"``) with a model_id.

    ``contexts`` is an optional ``{alias: context_length}`` map (from
    :func:`_fetch_model_contexts`); callers pass it so the per-model context
    comes from the gateway's ``/v1/models`` rather than the context-less
    ``/api/slots`` state. When omitted (e.g. unit tests) no network call is
    made and the per-slot fallback (:func:`_slot_context_length`) is used.

    The real ``/api/slots`` payload sets ``type=="llm"`` for chat slots and
    ``kind=="local"`` for the deployment shape. The previous ``_slot_kind``
    check looked at ``kind`` first and rejected 100% of real slots (R4 H1)
    — the rendered ``model_aliases`` block never appeared, so Hermes only
    ever saw the primary upstream's single model in ``/v1/models``.

    Only slots reporting a live/ready state are advertised so we don't tell
    the agent about a model that isn't actually loaded — matches the
    dashboard chat-filter at ``src/hal0/api/routes/slots.py``.

    Each alias's ``backend_url`` is the STABLE hal0 gateway (`:8080/v1`),
    NOT the slot's raw ``backend_url``. lemond reassigns the per-slot
    upstream port (`:8001/:8002/…`) on every model reload, so a baked-in
    alias port goes stale immediately — and could then point at a port
    now serving a DIFFERENT co-resident model. The gateway resolves both
    the alias name and the model_id to the correct co-resident slot, so
    `model_id` + `:8080/v1` stays correct across reloads (the same source
    the ``model:`` / ``delegation:`` / ``auxiliary:`` blocks use). This is
    what lets the in-agent model switcher pick a slot up after a restart.
    """
    # Context lives on the gateway's /v1/models (keyed by alias), NOT on the
    # /api/slots state dict — callers pass it in; fall back to any context the
    # slot dict happens to carry.
    ctx_map = contexts or {}
    out: list[dict[str, Any]] = []
    for s in slots:
        if (s.get("type") or "").lower() != "llm":
            continue
        if not _is_ready(s):
            continue
        model_id = _slot_model_id(s)
        if not model_id:
            continue
        alias = _slot_alias(s)
        out.append(
            {
                "alias": alias,
                "model_id": model_id,
                "backend_url": _DEFAULT_PRIMARY_BACKEND_URL,
                "context_length": ctx_map.get(alias) or _slot_context_length(s),
            }
        )
    return out


def _resolve_custom_providers(
    chat_slots: list[dict[str, Any]],
    *,
    hal0_base_url: str,
) -> list[dict[str, Any]] | None:
    """Build the ``custom_providers`` block from live chat slots.

    hermes 0.14.0 `agent/model_metadata.py:get_model_context_length`
    treats the top-level ``model.context_length`` as a GLOBAL override
    applied to EVERY model — switching to a cloud model (deepseek/
    openrouter) then wrongly inherits our local value. The supported
    per-model mechanism is ``custom_providers[].models.<model_id>.
    context_length``, matched by base_url + model in `hermes_cli/config.py:
    get_custom_provider_context_length` (used by startup, /model switch and
    /info) and merged into the picker via `get_compatible_custom_providers`.
    It does NOT bleed across base_urls/providers.

    Returns a single-element list ``[{name, base_url, models}]`` where the
    ``models`` KEYS are model_ids (what hermes looks up at runtime), not
    slot aliases. Degrade-safe: only slots that resolve a context_length
    contribute an entry; returns ``None`` when none do so the template
    omits the block entirely.
    """
    models: dict[str, dict[str, Any]] = {}
    for slot in chat_slots:
        model_id = slot.get("model_id")
        ctx = slot.get("context_length")
        if not model_id or not ctx:
            continue
        # First writer wins on a model_id collision (declaration order
        # mirrors _collect_chat_slots / /api/slots ordering).
        models.setdefault(model_id, {"context_length": int(ctx)})
    if not models:
        return None
    return [{"name": "hal0", "base_url": hal0_base_url, "models": models}]


# ── Role→slot resolution (delegation + auxiliary) ───────────────────────────
#
# hermes-agent supports per-ROLE models beyond the main chat block:
#   * subagents  → the `delegation:` block (delegate_tool.py
#     `_resolve_delegation_credentials` reads delegation.{model,provider,
#     base_url}; a `base_url` forces provider → "custom").
#   * side-tasks → `auxiliary.<task>.{provider,model,base_url}` read by
#     auxiliary_client.py `_resolve_task_provider_model` (a base_url +
#     non-"auto" provider routes the task to that direct endpoint).
#
# We resolve these from LIVE slot NAMES, not hardcoded model ids, so
# swapping a slot's model flows through on the next `--repair`:
#   chat       → slot `primary`      (the existing model: block)
#   subagents  → slot `agent-hermes` (delegation: block)
#   side-tasks → slot `utility`      (auxiliary.* compaction/search/title)
#
# Vision + web_extract have no dedicated slot — they stay provider:"main".

# The hal0-routed side-tasks (everything that should run on the cheap
# `utility` slot). vision/web_extract are intentionally excluded — they
# keep provider:"main" so they inherit the chat model (which may carry a
# vision label) rather than the tiny utility model.
_UTILITY_AUX_TASKS: tuple[str, ...] = (
    "compression",
    "session_search",
    "title_generation",
    "skills_hub",
    "mcp",
)

# Tasks that always stay on the main chat provider regardless of slot
# state. Rendered verbatim so the auxiliary: block is fully parameterized
# (no hard-coded entries left in the template).
_MAIN_AUX_TASKS: tuple[str, ...] = ("vision", "web_extract")

# Canonical role→slot names. Kept here (not in the template) so the
# resolution stays data-driven and a future slot rename is a one-line edit.
_DELEGATION_SLOT_NAME = "agent-hermes"
_UTILITY_SLOT_NAME = "utility"


def _find_named_ready_slot(slots: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Return the ready ``type=='llm'`` slot whose name matches ``name``.

    Degrade-safe: returns ``None`` when the slot is absent OR present but
    not ready/loaded OR carries no model_id, so callers can fall back
    gracefully (delegation omitted; aux tasks revert to provider:"main").
    """
    for s in slots:
        if not isinstance(s, dict):
            continue
        if _slot_alias(s) != name:
            continue
        if (s.get("type") or "").lower() != "llm":
            continue
        if not _is_ready(s):
            continue
        if not _slot_model_id(s):
            continue
        return s
    return None


def _resolve_delegation(
    slots: list[dict[str, Any]],
    *,
    hal0_base_url: str,
) -> dict[str, Any] | None:
    """Build the ``delegation`` template dict from the ``agent-hermes`` slot.

    Returns ``{model, base_url, provider}`` when the slot is live, else
    ``None`` so the template omits the block and subagents inherit the
    parent (chat) model. ``base_url`` is the hal0 /v1 endpoint already
    used for the main model — setting it makes upstream auto-resolve the
    provider to "custom".
    """
    slot = _find_named_ready_slot(slots, _DELEGATION_SLOT_NAME)
    if slot is None:
        return None
    return {
        "model": _slot_model_id(slot),
        "base_url": hal0_base_url,
        "provider": "custom",
    }


def _resolve_auxiliary_tasks(
    slots: list[dict[str, Any]],
    *,
    hal0_base_url: str,
) -> dict[str, dict[str, Any]]:
    """Build the ``auxiliary_tasks`` template dict (task → {provider, model, base_url}).

    vision/web_extract always render as provider:"main" (no dedicated
    slot). The compaction/search/title group routes to the ``utility``
    slot when it's live; if that slot is missing the group degrades to
    provider:"main" so side-tasks fall back to the chat model rather than
    breaking. Resolution keys off the slot NAME (``utility``) and sends
    the slot's model_id — swapping the slot's model flows through on the
    next ``--repair``.
    """
    tasks: dict[str, dict[str, Any]] = {}
    for task in _MAIN_AUX_TASKS:
        tasks[task] = {"provider": "main", "model": "", "base_url": ""}

    utility = _find_named_ready_slot(slots, _UTILITY_SLOT_NAME)
    for task in _UTILITY_AUX_TASKS:
        if utility is not None:
            tasks[task] = {
                "provider": "custom",
                "model": _slot_model_id(utility),
                "base_url": hal0_base_url,
            }
        else:
            # Degrade safely: no utility slot → inherit the chat model.
            tasks[task] = {"provider": "main", "model": "", "base_url": ""}
    return tasks


def _phase_model_automap(state: BootstrapState) -> PhaseResult:
    """Refresh ``model_aliases`` in ``$HERMES_HOME/config.yaml``.

    Re-renders the whole config (so model + aliases stay consistent)
    and atomic-swaps if the hash drifted. Hash-equal output skips the
    write per #245 idempotency criterion.

    Embed/rerank/img slots are deliberately NOT mapped per ADR-0011 §3
    (Hermes has no top-level embed abstraction; memory MCP handles it).
    """
    hermes_home = Path(state.hermes_home)
    config_path = hermes_home / "config.yaml"
    if not config_path.exists():
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"{config_path} missing — config_write must run first",
        )

    slots = _fetch_slots()
    chat_slots = _collect_chat_slots(slots, contexts=_fetch_model_contexts())
    primary_raw = _resolve_primary_slot()
    primary = {
        "model_id": primary_raw["model"],
        "backend_url": primary_raw["base_url"],
        "context_length": primary_raw["context_length"],
    }
    # PR-3 Phase 9 (demoted): re-render uses the SAME inputs as Phase 5
    # so a no-drift run produces a byte-identical config. Mismatch means
    # something changed (slot churn, persona swap, MCP servers came up)
    # and we want the new config; match → no-op (hash check below).
    cached_servers = (state.phases.get("mcp_wire") or {}).get("details", {}).get("rendered_servers")
    mcp_servers = cached_servers if isinstance(cached_servers, list) and cached_servers else None
    system_prompt, personality_name = _active_persona_render(state, mcp_servers=mcp_servers)
    # feat/hermes-role-slots: identical role-slot resolution to config_write
    # so a no-drift re-render stays byte-identical (#245 idempotency).
    hal0_v1_base = primary["backend_url"]
    delegation = _resolve_delegation(slots, hal0_base_url=hal0_v1_base)
    auxiliary_tasks = _resolve_auxiliary_tasks(slots, hal0_base_url=hal0_v1_base)
    custom_providers = _resolve_custom_providers(chat_slots, hal0_base_url=hal0_v1_base)

    try:
        rendered = _render_config_yaml(
            primary=primary,
            chat_slots=chat_slots,
            agent_id=state.agent_id,
            mcp_servers=mcp_servers,
            system_prompt=system_prompt,
            personality_name=personality_name,
            delegation=delegation,
            custom_providers=custom_providers,
            auxiliary_tasks=auxiliary_tasks,
        )
        rendered = _apply_overrides(rendered, OVERRIDES_PATH)
    except Exception as exc:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"config render failed: {type(exc).__name__}: {exc}",
        )

    new_hash = content_hash(rendered)
    try:
        current = config_path.read_text(encoding="utf-8")
        current_hash: str | None = content_hash(current)
    except OSError:
        current_hash = None

    skipped = [_slot_alias(s) for s in slots if _slot_kind(s) in {"embed", "rerank", "img"}]
    aliases_written = [s["alias"] for s in chat_slots]

    if current_hash == new_hash:
        return PhaseResult(
            status=PhaseStatus.OK,
            hash=new_hash,
            details={
                "config_path": str(config_path),
                "unchanged": True,
                "aliases_written": aliases_written,
                "skipped": skipped,
                "chat_slot_count": len(chat_slots),
            },
        )

    try:
        _atomic_write(config_path, rendered)
    except OSError as exc:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"config write failed: {exc}",
        )
    return PhaseResult(
        status=PhaseStatus.OK,
        hash=new_hash,
        details={
            "config_path": str(config_path),
            "aliases_written": aliases_written,
            "skipped": skipped,
            "chat_slot_count": len(chat_slots),
            "slots_total": len(slots),
        },
    )


# ── Phase J: voice_wire ─────────────────────────────────────────────────────
#
# Conditional. Emits STT/TTS provider config + writes
# /var/lib/hal0/secrets/agents/hermes.env. Per the post-ADR-0012
# correction on #246, that secrets file is OUTBOUND credentials only
# now (HF token + external MCP tokens + STT_/TTS_OPENAI_BASE_URL).
# voice_wire skips with reason when neither slot is `ready`.


HERMES_SECRETS_ENV = Path("/var/lib/hal0/secrets/agents/hermes.env")

# ── Gateway secrets drop-in (#437, SYSTEM scope) ─────────────────────────────
#
# The Hermes gateway runs as a SYSTEM-scope unit
# (/etc/systemd/system/hermes-gateway.service, User=hal0). Its platform
# tokens (Telegram + Discord bot tokens, allowed-user lists, FAL_KEY,
# OPENROUTER_API_KEY) live in the root:root 0600 vault at
# HERMES_SECRETS_ENV and are wired into the unit via a systemd drop-in —
# NOT a main-unit edit. A drop-in survives ``hermes gateway install``
# regenerating the main .service (hermes_cli rewrites the .service body
# but never touches the .d/ tree), so platform connectivity persists
# across main-unit regeneration. Under a system unit, pid1 (root) reads
# the EnvironmentFile, so the vault can stay 0600 root:root while the
# drop-in itself is world-readable 0644 like any normal unit fragment.
GATEWAY_SYSTEMD_DROPIN_DIR = Path("/etc/systemd/system/hermes-gateway.service.d")
GATEWAY_SYSTEMD_DROPIN_FILE = GATEWAY_SYSTEMD_DROPIN_DIR / "10-hal0-secrets.conf"


def _gateway_dropin_body() -> str:
    """Render the gateway secrets drop-in body.

    Mirrors the live drop-in: a why-comment header plus a ``[Service]``
    ``EnvironmentFile=`` pointing at the secrets vault. The path is
    absolute + stable, so the body is deterministic — the content hash
    only changes if HERMES_SECRETS_ENV or this header changes, which is
    what makes the idempotent hash-skip below correct.
    """
    return (
        "# hal0-managed (issue #437) — DO NOT EDIT BY HAND.\n"
        "#\n"
        "# Wires the Hermes gateway's platform tokens (Telegram + Discord\n"
        "# bot tokens, allowed-user lists, OPENROUTER_API_KEY, FAL_KEY) into\n"
        "# the SYSTEM-scope hermes-gateway.service. Lives in a drop-in (not a\n"
        "# main-unit edit) so it survives `hermes gateway install` rewriting\n"
        "# the main .service body. pid1 (root) reads the 0600 vault below.\n"
        "#\n"
        "# Re-apply: `systemctl daemon-reload && systemctl restart hermes-gateway`.\n"
        "[Service]\n"
        f"EnvironmentFile={HERMES_SECRETS_ENV}\n"
    )


def _phase_gateway_secrets_wire(state: BootstrapState) -> PhaseResult:
    """Idempotently write the gateway secrets drop-in + daemon-reload (#437).

    Owns ONLY the drop-in ``10-hal0-secrets.conf`` under
    ``/etc/systemd/system/hermes-gateway.service.d/`` — NOT the main
    ``hermes-gateway.service`` unit. The main unit is generated by
    ``hermes gateway install --system --run-as-user hal0`` (run by the
    orchestrator during cutover); keeping unit generation out of this
    phase avoids the hermes_cli generator's custom-HERMES_HOME trap
    (a root ``.bashrc`` pin would leak the old agents/hermes path into
    the emitted unit). The drop-in survives every main-unit regeneration
    because ``refresh_systemd_unit_if_needed`` rewrites the ``.service``
    but never the ``.d/`` tree.

    Posture mirrors :func:`_merge_env_file` / config_write:

    * Hash-skip — when the on-disk drop-in already matches the rendered
      body, skip both the write AND the ``systemctl daemon-reload`` so a
      bootstrap re-run doesn't churn systemd needlessly.
    * Atomic write — tmpfile + ``os.replace``.
    * Mode 0644 (NOT 0600): systemd unit fragments must be world-readable;
      the *secrets* live in the 0600 vault the drop-in references, not in
      the drop-in itself.
    * daemon-reload only fires when the file actually changed.

    Non-root guard: writing under ``/etc/systemd/system`` and invoking
    ``systemctl`` both require root, so a non-root provision SKIPs with a
    clear reason rather than failing the whole bootstrap.
    """
    # Defense-in-depth (regression: 2026-06-04 outage). The euid!=0 guard
    # below is the only thing that normally keeps the test suite off the
    # host's real systemd tree — but it is DEFEATED when pytest runs as
    # root (or, as on hal0-dev, where /etc/systemd is ACL-writable). A
    # fixture that monkeypatches HERMES_SECRETS_ENV but forgets
    # GATEWAY_SYSTEMD_DROPIN_FILE then writes the live drop-in with a
    # pytest-tmp EnvironmentFile path, restart-looping the gateway once the
    # tmp dir is reaped. So: under pytest, refuse to touch the real /etc
    # tree. A test that genuinely exercises the write monkeypatches the
    # drop-in dir to tmp_path, which moves it out from under /etc.
    if os.environ.get("PYTEST_CURRENT_TEST") and str(GATEWAY_SYSTEMD_DROPIN_DIR).startswith(
        "/etc/"
    ):
        return PhaseResult(
            status=PhaseStatus.SKIP,
            reason=(
                "running under pytest with an un-sandboxed system drop-in path "
                "— refusing to write the real /etc/systemd tree; monkeypatch "
                "GATEWAY_SYSTEMD_DROPIN_DIR/FILE to tmp in the test fixture"
            ),
            details={"dropin_path": str(GATEWAY_SYSTEMD_DROPIN_FILE)},
        )

    if os.geteuid() != 0:
        return PhaseResult(
            status=PhaseStatus.SKIP,
            reason=(
                "not root (euid != 0) — cannot write /etc/systemd/system "
                "or run `systemctl daemon-reload`; re-run gateway wiring as root"
            ),
            details={"dropin_path": str(GATEWAY_SYSTEMD_DROPIN_FILE)},
        )

    body = _gateway_dropin_body()
    content_sha = content_hash(body)

    # Hash-skip: an unchanged drop-in needs neither a rewrite nor a
    # daemon-reload (#437 idempotency criterion, mirroring config_write).
    if GATEWAY_SYSTEMD_DROPIN_FILE.exists():
        try:
            current = GATEWAY_SYSTEMD_DROPIN_FILE.read_text(encoding="utf-8")
        except OSError:
            current = None
        if current is not None and content_hash(current) == content_sha:
            return PhaseResult(
                status=PhaseStatus.OK,
                hash=content_sha,
                details={
                    "dropin_path": str(GATEWAY_SYSTEMD_DROPIN_FILE),
                    "content_hash": content_sha,
                    "daemon_reload": False,
                    "unchanged": True,
                },
            )

    try:
        GATEWAY_SYSTEMD_DROPIN_DIR.mkdir(parents=True, exist_ok=True)
        GATEWAY_SYSTEMD_DROPIN_DIR.chmod(0o755)
        tmp = GATEWAY_SYSTEMD_DROPIN_FILE.with_suffix(".conf.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, GATEWAY_SYSTEMD_DROPIN_FILE)
        GATEWAY_SYSTEMD_DROPIN_FILE.chmod(0o644)
    except OSError as exc:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"gateway drop-in write to {GATEWAY_SYSTEMD_DROPIN_FILE} failed: {exc}",
            details={"dropin_path": str(GATEWAY_SYSTEMD_DROPIN_FILE)},
        )

    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)  # nosec B603 B607
    except (subprocess.SubprocessError, OSError) as exc:
        # The drop-in is on disk; the operator can daemon-reload by hand.
        # Surface as a non-fatal warning rather than failing bootstrap —
        # the wiring lands on the next `systemctl daemon-reload`.
        return PhaseResult(
            status=PhaseStatus.OK,
            hash=content_sha,
            reason=f"drop-in written but `systemctl daemon-reload` failed: {exc}",
            details={
                "dropin_path": str(GATEWAY_SYSTEMD_DROPIN_FILE),
                "content_hash": content_sha,
                "daemon_reload": False,
            },
        )

    return PhaseResult(
        status=PhaseStatus.OK,
        hash=content_sha,
        details={
            "dropin_path": str(GATEWAY_SYSTEMD_DROPIN_FILE),
            "content_hash": content_sha,
            "daemon_reload": True,
        },
    )


def _find_slot(slots: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    for s in slots:
        if _slot_kind(s) == kind and _is_ready(s):
            return s
    return None


def _merge_env_file(path: Path, updates: dict[str, str]) -> None:
    """Idempotent in-place update of a KEY=VALUE env file.

    Preserves existing lines (comments + other entries the operator
    added by hand) and replaces values when keys match. Atomic via
    tmpfile + rename.
    """
    existing: list[str] = []
    seen: set[str] = set()
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing = []

    out_lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            out_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out_lines.append(line)
    for key, val in updates.items():
        if key not in seen:
            out_lines.append(f"{key}={val}")

    import contextlib

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _phase_voice_wire(state: BootstrapState) -> PhaseResult:
    """Emit STT/TTS provider config + secrets env when both slots are ready.

    Skip semantics: when neither STT nor TTS is configured + ready,
    return SKIP with a clear reason — same posture as voice_wire in
    the plan §13.
    """
    slots = _fetch_slots()
    stt = _find_slot(slots, "stt")
    tts = _find_slot(slots, "tts")
    if stt is None and tts is None:
        return PhaseResult(
            status=PhaseStatus.SKIP,
            reason="no stt/tts slots ready",
            details={"slots_total": len(slots)},
        )

    updates: dict[str, str] = {}
    details: dict[str, Any] = {"stt": None, "tts": None}
    if stt is not None:
        url = _slot_backend_url(stt)
        updates["STT_OPENAI_BASE_URL"] = url
        updates["STT_OPENAI_API_KEY"] = "dummy"  # voice OpenAI client wants a key value
        details["stt"] = {"backend_url": url, "model": _slot_model_id(stt)}
    if tts is not None:
        url = _slot_backend_url(tts)
        updates["TTS_OPENAI_BASE_URL"] = url
        updates["TTS_OPENAI_API_KEY"] = "dummy"
        details["tts"] = {"backend_url": url, "model": _slot_model_id(tts)}

    try:
        _merge_env_file(HERMES_SECRETS_ENV, updates)
    except OSError as exc:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"secrets env write to {HERMES_SECRETS_ENV} failed: {exc}",
            details=details,
        )

    # Re-render config.yaml so the stt: / tts: blocks land. The render
    # uses the same template config_write does — passing stt/tts kwargs
    # turns on the conditional sections.
    hermes_home = Path(state.hermes_home)
    config_path = hermes_home / "config.yaml"
    try:
        primary_raw = _resolve_primary_slot()
        primary = {
            "model_id": primary_raw["model"],
            "backend_url": primary_raw["base_url"],
            "context_length": primary_raw["context_length"],
        }
        cached_servers = (
            (state.phases.get("mcp_wire") or {}).get("details", {}).get("rendered_servers")
        )
        mcp_servers = (
            cached_servers if isinstance(cached_servers, list) and cached_servers else None
        )
        system_prompt, personality_name = _active_persona_render(state, mcp_servers=mcp_servers)
        # feat/hermes-role-slots: keep role-slot blocks consistent with the
        # other render call sites so re-render stays idempotent.
        hal0_v1_base = primary["backend_url"]
        chat_slots = _collect_chat_slots(slots, contexts=_fetch_model_contexts())
        delegation = _resolve_delegation(slots, hal0_base_url=hal0_v1_base)
        auxiliary_tasks = _resolve_auxiliary_tasks(slots, hal0_base_url=hal0_v1_base)
        custom_providers = _resolve_custom_providers(chat_slots, hal0_base_url=hal0_v1_base)
        rendered = _render_config_yaml(
            primary=primary,
            chat_slots=chat_slots,
            stt={
                "provider": "openai",
                "backend_url": details["stt"]["backend_url"] if details["stt"] else None,
                "model": details["stt"]["model"] if details["stt"] else None,
            }
            if details["stt"]
            else None,
            tts={
                "provider": "openai",
                "backend_url": details["tts"]["backend_url"] if details["tts"] else None,
                "model": details["tts"]["model"] if details["tts"] else None,
            }
            if details["tts"]
            else None,
            agent_id=state.agent_id,
            mcp_servers=mcp_servers,
            system_prompt=system_prompt,
            personality_name=personality_name,
            delegation=delegation,
            auxiliary_tasks=auxiliary_tasks,
            custom_providers=custom_providers,
        )
        rendered = _apply_overrides(rendered, OVERRIDES_PATH)
    except Exception as exc:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"config render with voice failed: {exc}",
            details=details,
        )

    if config_path.exists():
        new_hash = content_hash(rendered)
        try:
            current_hash: str | None = content_hash(config_path.read_text(encoding="utf-8"))
        except OSError:
            current_hash = None
        if current_hash != new_hash:
            try:
                _atomic_write(config_path, rendered)
            except OSError as exc:
                return PhaseResult(
                    status=PhaseStatus.FAIL,
                    reason=f"config write failed: {exc}",
                    details=details,
                )

    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            **details,
            "secrets_env": str(HERMES_SECRETS_ENV),
            "config_path": str(config_path),
        },
    )


# ── Phase K: smoke_tests ────────────────────────────────────────────────────
#
# Six non-fatal probes per plan §14 + #246. Each surface check writes a
# `passed: bool` row into PhaseResult.details["results"]; failures
# also carry a remediation hint operators can paste at the user.
#
# The phase status is OK even with failures — smoke_tests are
# diagnostic, not gating. self_report surfaces the rollup in the
# bootstrap-completion memory item.


def _wrapper_bin() -> Path:
    return WRAPPER_INSTALL_PATH


def _smoke_chat_completions(state: BootstrapState) -> tuple[bool, str]:
    """POST against model.base_url/chat/completions; assert 'ready' in reply.

    Reads the live config.yaml so we hit whatever model_automap left
    behind, not a hardcoded URL.
    """
    import yaml  # type: ignore[import-untyped]

    config_path = Path(state.hermes_home) / "config.yaml"
    if not config_path.exists():
        return (False, "config.yaml missing — bootstrap incomplete")
    try:
        cfg = yaml.safe_load(config_path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return (False, f"config parse: {exc}")
    model_cfg = cfg.get("model") or {}
    base_url = model_cfg.get("base_url", "")
    model_name = model_cfg.get("default", "")
    if not base_url:
        return (False, "model.base_url unset in config.yaml")
    if not model_name:
        return (False, "model.default unset in config.yaml")
    # Thinking-mode models (Qwen3, etc.) burn most of their token budget
    # on a `<think>...</think>` reasoning block before emitting any
    # visible content. A 16-token cap drains entirely into reasoning
    # and the `content` field comes back empty — which falsely flags
    # the wiring as broken. Give the model enough room to think + reply
    # and accept matches in either field.
    body = json.dumps(
        {
            "model": model_name,
            "messages": [{"role": "user", "content": "Reply with the single word 'ready'."}],
            "max_tokens": 256,
        }
    ).encode("utf-8")
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    req = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Thinking models can spend tens of seconds on the reasoning block
    # before emitting visible content; 10s wasn't long enough.
    try:
        with urlopen(req, timeout=60.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return (False, f"chat/completions: {exc}")
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return (False, "response missing choices[0].message")
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or ""
    haystack = f"{content}\n{reasoning}".lower()
    detail = (content or reasoning).strip().replace("\n", " ")[:120] or "(empty)"
    return ("ready" in haystack, detail)


def _smoke_memory_roundtrip(state: BootstrapState) -> tuple[bool, str]:
    add = _mcp_memory_call(
        "tools/call",
        {
            "name": "memory_add",
            "arguments": {
                "text": "hal0 smoke-test marker",
                "tags": ["smoke-test"],
                "dataset": f"private:{state.agent_id}",
            },
        },
        agent_id=state.agent_id,
        private=True,
    )
    if not add["ok"]:
        return (False, f"memory_add: {add['error']}")
    search = _mcp_memory_call(
        "tools/call",
        {
            "name": "memory_search",
            "arguments": {
                "query": "smoke-test marker",
                "tags": ["smoke-test"],
                "dataset": f"private:{state.agent_id}",
                "limit": 5,
            },
        },
        agent_id=state.agent_id,
        private=True,
    )
    if not search["ok"]:
        return (False, f"memory_search: {search['error']}")
    items = (search["result"] or {}).get("items") if isinstance(search["result"], dict) else []
    if items:
        return (True, f"{len(items)} item(s) returned")
    return (False, "memory_search returned no items for just-written marker")


def _smoke_admin_tools_list(state: BootstrapState) -> tuple[bool, str]:
    probe = _probe_mcp_server(
        "http://127.0.0.1:8080/mcp/admin",
        agent_id=state.agent_id,
        private=False,
    )
    if not probe["ok"]:
        return (False, probe["error"] or "unreachable")
    n = len(probe["tools"])
    return (n >= 5, f"{n} tools advertised")


def _smoke_hermes_md_contains_primary(state: BootstrapState) -> tuple[bool, str]:
    hermes_md = ETC_HAL0_DIR / "HERMES.md"
    if not hermes_md.exists():
        return (False, f"{hermes_md} not present")
    config = Path(state.hermes_home) / "config.yaml"
    if not config.exists():
        return (False, "config.yaml missing")
    import yaml  # type: ignore[import-untyped]

    try:
        cfg = yaml.safe_load(config.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return (False, f"config parse: {exc}")
    primary = (cfg.get("model") or {}).get("default", "")
    if not primary:
        return (True, "no primary configured; skipping content check")
    body = hermes_md.read_text(encoding="utf-8")
    return (
        primary in body,
        f"primary='{primary}' {'in' if primary in body else 'missing from'} HERMES.md",
    )


def _smoke_wrapper_ready(_state: BootstrapState) -> tuple[bool, str]:
    wrapper = _wrapper_bin()
    if not wrapper.exists():
        return (False, f"wrapper missing at {wrapper}")
    try:
        result = subprocess.run(  # nosec B603 — known-safe argv
            [str(wrapper), "--hal0-ready"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, f"wrapper exec: {exc}")
    return (result.returncode == 0, f"--hal0-ready rc={result.returncode}")


def _smoke_hermes_doctor(_state: BootstrapState) -> tuple[bool, str]:
    venv_hermes = _venv_python(Path(_state.venv)).parent / "hermes"
    if not venv_hermes.exists():
        return (False, f"hermes binary missing at {venv_hermes}")
    try:
        result = subprocess.run(  # nosec B603 — known-safe argv
            [str(venv_hermes), "doctor"],
            check=False,
            capture_output=True,
            timeout=30,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, f"hermes doctor: {exc}")
    return (result.returncode == 0, f"rc={result.returncode}")


def _phase_smoke_tests(state: BootstrapState) -> PhaseResult:
    """Run six diagnostic probes; collect results into the checkpoint."""
    probes = [
        ("wrapper_ready", _smoke_wrapper_ready),
        ("hermes_doctor", _smoke_hermes_doctor),
        ("chat_completions", _smoke_chat_completions),
        ("memory_roundtrip", _smoke_memory_roundtrip),
        ("admin_tools_list", _smoke_admin_tools_list),
        ("hermes_md_contains_primary", _smoke_hermes_md_contains_primary),
    ]
    results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for name, fn in probes:
        try:
            passed, detail = fn(state)
        except Exception as exc:
            passed, detail = (False, f"{type(exc).__name__}: {exc}")
        results[name] = {"passed": passed, "detail": detail}
        if not passed:
            failures.append(f"{name}: {detail}")
    return PhaseResult(
        status=PhaseStatus.OK,
        details={"results": results, "failures": failures},
    )


# ── Phase L: self_report ────────────────────────────────────────────────────
#
# Final summary memory item under private:<agent_id> — first thing
# the agent recalls on next session start. Includes the smoke-test
# rollup so a degraded install surfaces in chat.


def _phase_self_report(state: BootstrapState) -> PhaseResult:
    """Write a bootstrap-completion summary into the agent's private namespace.

    Failure of the memory write is non-fatal — same posture as
    namespace_register (#243): the memory layer being unavailable
    shouldn't fail bootstrap.
    """
    smoke = (state.phases.get("smoke_tests") or {}).get("details") or {}
    smoke_failures = smoke.get("failures") or []
    primary_alias = ""
    config_path = Path(state.hermes_home) / "config.yaml"
    if config_path.exists():
        try:
            import yaml  # type: ignore[import-untyped]

            cfg = yaml.safe_load(config_path.read_text()) or {}
            primary_alias = (cfg.get("model") or {}).get("default", "")
        except (OSError, Exception):
            pass

    text = (
        f"Hermes-Agent bootstrap completed. Pinned to "
        f"hermes-agent {_hermes_version_pin()} on hal0 {_hal0_version_string()}. "
        f"Primary model: {primary_alias or 'unwired'}. "
        f"Smoke failures: {len(smoke_failures)}."
    )
    add = _mcp_memory_call(
        "tools/call",
        {
            "name": "memory_add",
            "arguments": {
                "text": text,
                "tags": ["bootstrap", "self-report"],
                "dataset": f"private:{state.agent_id}",
                "metadata": {
                    "bootstrap_version": 1,
                    "smoke_failures": smoke_failures,
                    "completed_at": _utcnow(),
                },
            },
        },
        agent_id=state.agent_id,
        private=True,
    )
    if not add["ok"]:
        return PhaseResult(
            status=PhaseStatus.OK,
            details={"published": False, "warning": add["error"]},
        )
    summary_id = None
    if isinstance(add["result"], dict):
        summary_id = add["result"].get("id")
    return PhaseResult(status=PhaseStatus.OK, details={"published": True, "summary_id": summary_id})


# ── Phase: install_artifacts (issue #432) ────────────────────────────────────
#
# Writes the three manager/proxy install artifacts the provision pipeline
# used to leak (seed TOML, driver env file, runtime.json embed token). Runs
# right after home_init so $HERMES_HOME exists for runtime.json, and before
# the phases that read the seed/allowlist (mcp_wire) so a single bootstrap
# converges. Idempotent: the embed token is generated once and re-used on
# re-runs (so the secret doesn't rotate under a running proxy); ``--repair``
# forces a fresh token + rewrites every artifact.


def _seed_payload(state: BootstrapState) -> dict[str, Any]:
    """Build the ``[agent]`` seed block, mirroring AgentManager._write_seed.

    Shape matches ``hal0.agents.manager.AgentManager._write_seed`` so the
    manager's ``_read_record`` parses an identical layout regardless of which
    install path wrote the file.
    """
    return {
        "agent": {
            "name": "hermes",
            "installed_at": datetime.datetime.now(tz=datetime.UTC).isoformat(),
            # Track-latest by design (ADR-0004 §3). No version pin.
            "version_pin": False,
        },
        "data_dir": str(Path("/var/lib/hal0/agents/hermes")),
    }


def _write_seed_toml(state: BootstrapState, *, repair: bool) -> tuple[Path, bool]:
    """Write/merge the manager seed at :data:`INSTALL_SEED_PATH`.

    The seed file doubles as the MCP allow-list (``[mcp.servers.*]``), so we
    deep-merge: refresh ``[agent]`` + ``data_dir`` while preserving any
    operator-added server blocks. Returns ``(path, wrote)`` — ``wrote`` is
    ``False`` when an existing ``[agent]`` block already carried an
    ``installed_at`` and ``repair`` is off (idempotent no-op on re-run).
    """
    import tomllib

    import tomli_w

    path = INSTALL_SEED_PATH
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            existing = {}

    has_seed = bool((existing.get("agent") or {}).get("installed_at"))
    if has_seed and not repair:
        return path, False

    payload = _seed_payload(state)
    merged = dict(existing)
    merged["agent"] = payload["agent"]
    merged["data_dir"] = payload["data_dir"]

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_bytes(tomli_w.dumps(merged).encode("utf-8"))
    os.replace(tmp, path)
    return path, True


def _write_driver_env(state: BootstrapState) -> tuple[Path, bool]:
    """Write the driver env file at :data:`DRIVER_ENV_PATH`.

    Mirrors ``HermesDriver._write_env_file``: the wrapper sources this on
    every invocation for the hal0 API URL + MCP endpoints. Content is
    deterministic, so a hash-equal file is left untouched. Returns
    ``(path, wrote)``.
    """
    api_base = HAL0_API_URL.rstrip("/")
    body = (
        "# hal0 — Hermes-Agent env (managed by hal0; safe to edit)\n"
        f"HAL0_API_URL={api_base}\n"
        f"HAL0_MCP_ADMIN_URL={api_base}/mcp/admin\n"
        f"HAL0_MCP_MEMORY_URL={api_base}/mcp/memory\n"
    )
    path = DRIVER_ENV_PATH
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == body:
                return path, False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
    return path, True


def _write_runtime_json(state: BootstrapState, *, repair: bool) -> tuple[Path, bool]:
    """Write ``runtime.json`` (embed token) under ``$HERMES_HOME`` chmod 0600.

    The embed token is the shared secret chat_proxy sends as
    ``Authorization: Bearer`` on the browser→hermes hop. Generated once and
    re-used on re-runs so the secret never rotates under a running proxy;
    ``repair`` forces a fresh token. Returns ``(path, wrote)``.
    """
    import secrets as _secrets

    path = Path(state.hermes_home) / RUNTIME_JSON_NAME
    token: str | None = None
    if path.exists() and not repair:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            existing = data.get("token") or data.get("embed_token")
            if isinstance(existing, str) and existing:
                token = existing
        except (OSError, json.JSONDecodeError):
            token = None
    if token is not None:
        # Re-tighten perms; the token is already on disk and unchanged.
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        return path, False

    token = _secrets.token_urlsafe(32)
    payload = {"token": token, "written_at": _utcnow()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path, True


def _phase_install_artifacts(state: BootstrapState) -> PhaseResult:
    """Write the seed TOML, driver env file, and runtime.json (issue #432).

    These three artifacts were previously only written by
    ``AgentManager.install``; the ``hal0 agent bootstrap hermes`` path skipped
    them entirely, leaving the manager reporting ``broken`` and the chat proxy
    sending no Bearer. Idempotent + ``--repair``-aware (mirrors persona_seed).

    Sandbox guard (mirrors gateway_secrets_wire): under pytest, refuse to
    write the seed/env when they still point at the real ``/etc/`` tree.
    A test that genuinely exercises these writes monkeypatches
    INSTALL_SEED_PATH / DRIVER_ENV_PATH to tmp_path; the runtime.json write
    is always tmp-safe because it tracks ``state.hermes_home``.
    """
    repair = bool(state.phases.get("_repair_flag"))

    under_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    if under_pytest and (
        str(INSTALL_SEED_PATH).startswith("/etc/") or str(DRIVER_ENV_PATH).startswith("/etc/")
    ):
        # Don't strand artifacts entirely — runtime.json is tmp-safe.
        runtime_path, token_wrote = _write_runtime_json(state, repair=repair)
        return PhaseResult(
            status=PhaseStatus.SKIP,
            reason=(
                "running under pytest with un-sandboxed /etc seed/env paths "
                "— refusing to write the real /etc/hal0 tree; monkeypatch "
                "INSTALL_SEED_PATH/DRIVER_ENV_PATH to tmp in the test fixture"
            ),
            details={
                "seed_path": str(INSTALL_SEED_PATH),
                "env_path": str(DRIVER_ENV_PATH),
                "runtime_json_path": str(runtime_path),
                "token_wrote": token_wrote,
            },
        )

    seed_path, seed_wrote = _write_seed_toml(state, repair=repair)
    env_path, env_wrote = _write_driver_env(state)
    runtime_path, token_wrote = _write_runtime_json(state, repair=repair)

    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "seed_path": str(seed_path),
            "seed_wrote": seed_wrote,
            "env_path": str(env_path),
            "env_wrote": env_wrote,
            "runtime_json_path": str(runtime_path),
            "token_wrote": token_wrote,
        },
    )


PHASES: list[tuple[str, Callable[[BootstrapState], PhaseResult]]] = [
    ("preflight", _phase_preflight),
    ("install", _phase_install),
    ("env_probe", _phase_env_probe),
    ("home_init", _phase_home_init),
    # #432: write the manager seed + driver env + runtime.json embed token
    # right after $HERMES_HOME exists and before mcp_wire reads the seed's
    # allow-list, so a single `bootstrap hermes` run leaves the artifacts the
    # manager + chat_proxy key off (previously only AgentManager.install wrote
    # them, so the bootstrap path left the agent reporting `broken`).
    ("install_artifacts", _phase_install_artifacts),
    # PR-3 Phase 8: seed personas BEFORE config_write so the first
    # config render gets the active persona's system_prompt prelude.
    # mcp_wire runs after config_write to probe the live MCP surface;
    # the probe results feed Phase 9 (model_automap)'s re-render so a
    # post-bootstrap config still picks up the validated server list.
    ("persona_seed", _phase_persona_seed),
    ("config_write", _phase_config_write),
    ("mcp_wire", _phase_mcp_wire),
    ("context_link", _phase_context_link),
    ("namespace_register", _phase_namespace_register),
    ("model_automap", _phase_model_automap),
    ("voice_wire", _phase_voice_wire),
    # #437 (SYSTEM scope): wire the gateway secrets drop-in so fresh
    # provisions/reinstalls come up with Telegram + Discord connected,
    # surviving hermes_cli main-unit regeneration. Runs after voice_wire
    # (which may write the secrets vault this drop-in references) and
    # before smoke_tests. The orchestrator runs `hermes gateway install`
    # separately to lay down the main unit; this phase only owns the
    # drop-in + daemon-reload.
    ("gateway_secrets_wire", _phase_gateway_secrets_wire),
    ("smoke_tests", _phase_smoke_tests),
    ("self_report", _phase_self_report),
]

PHASE_NAMES: tuple[str, ...] = tuple(name for name, _ in PHASES)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat().replace("+00:00", "Z")


def content_hash(*pieces: str | bytes) -> str:
    """Stable content hash phases use to detect "inputs unchanged".

    Phases that produce on-disk outputs (config.yaml, HERMES.md) hash
    the rendered content and stash it in ``PhaseResult.hash``. A
    re-run computes the hash again; mismatch → ``repair_needed``.
    """
    h = hashlib.sha256()
    for piece in pieces:
        if isinstance(piece, str):
            piece = piece.encode("utf-8")
        h.update(piece)
    return h.hexdigest()


# ── Orchestrator ─────────────────────────────────────────────────────────────


@dataclass
class RunResult:
    """Aggregate result of one :func:`run` invocation.

    ``phases`` mirrors ``BootstrapState.phases`` post-run for
    test-side assertions; ``state`` is the persisted dataclass.
    """

    state: BootstrapState
    phases: dict[str, dict[str, Any]]
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def run(
    *,
    repair: bool = False,
    dry_run: bool = False,
    skip_phases: tuple[str, ...] = (),
    state_root: Path | None = None,
    verbose: bool = False,
    initial_state: BootstrapState | None = None,
) -> RunResult:
    """Run every phase in order, persisting checkpoints to ``state_root``.

    * ``repair`` — re-run every phase regardless of checkpoint state.
    * ``dry_run`` — execute each phase but don't persist the state file.
    * ``skip_phases`` — skip the named phases (logged as ``skip``).
    * ``state_root`` — overrides the default ``provision.json`` location;
      tests pass a ``tmp_path``.
    * ``initial_state`` — seed state when no checkpoint exists; tests
      pass one with `hermes_home` + `venv` pointed at `tmp_path` so the
      real install/home_init phases don't need write access to /var/lib.

    Returns a :class:`RunResult` capturing the post-run state + the
    per-phase outcomes the CLI surface pretty-prints.
    """
    root = state_root if state_root is not None else _DEFAULT_STATE_ROOT
    state = BootstrapState.load(root) or initial_state or BootstrapState()
    if state.started_at is None or repair:
        state.started_at = _utcnow()
        state.completed_at = None

    skipped: list[str] = []
    failed: list[str] = []
    # Surface ``--repair`` to phase bodies that change behavior under it
    # (persona_seed re-writes its seeds on repair). Stash a sentinel on
    # ``state.phases`` so the runtime doesn't need a new signature. The
    # entry is stripped before save so it never appears in provision.json.
    if repair:
        state.phases["_repair_flag"] = {"status": "stub", "details": {}}

    for name, phase in PHASES:
        if name in skip_phases:
            entry = {
                "status": PhaseStatus.SKIP.value,
                "at": _utcnow(),
                "reason": "--skip-phase",
            }
            state.phases[name] = entry
            skipped.append(name)
            if verbose:
                print(f"[skip] {name} (--skip-phase)")
            continue

        if not repair and state.phase_done(name):
            if verbose:
                print(f"[skip] {name} (already ok)")
            skipped.append(name)
            continue

        if verbose:
            print(f"[run ] {name}")

        result = phase(state)
        entry = result.to_dict()
        entry["at"] = _utcnow()
        state.phases[name] = entry

        if result.status == PhaseStatus.FAIL:
            failed.append(name)
            state.errors.append(f"{name}: {result.reason or 'unspecified failure'}")

    # Strip the repair sentinel before persistence — operator never sees
    # it in provision.json, and the next run computes it from CLI flags.
    state.phases.pop("_repair_flag", None)

    if not failed:
        state.completed_at = _utcnow()

    if not dry_run:
        state.save(root)

    return RunResult(state=state, phases=dict(state.phases), skipped=skipped, failed=failed)


# ── CLI surface ──────────────────────────────────────────────────────────────


def bootstrap_cli(
    *,
    repair: bool,
    dry_run: bool,
    skip_phases: tuple[str, ...],
    verbose: bool,
    state_root: Path | None = None,
) -> int:
    """CLI entry point. Returns a POSIX exit code (0 = success, 1 = any fail)."""
    result = run(
        repair=repair,
        dry_run=dry_run,
        skip_phases=skip_phases,
        verbose=verbose,
        state_root=state_root,
    )
    if verbose:
        target = (state_root or _DEFAULT_STATE_ROOT) / _STATE_FILE_NAME
        print(f"state: {target}")
    if result.failed:
        print(f"bootstrap failed in phases: {', '.join(result.failed)}")
        return 1
    return 0
