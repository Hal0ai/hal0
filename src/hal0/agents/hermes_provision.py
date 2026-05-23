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
    hermes_home: str = "/var/lib/hal0/agents/hermes"
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
REPO_ROOT_FOR_INSTALLER = Path(__file__).resolve().parents[3]


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


def _copy_plugin_tree(src: Path, dst: Path) -> None:
    """Mirror a plugin directory (idempotent)."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _phase_install(state: BootstrapState) -> PhaseResult:
    """Provision the managed Hermes venv + wrapper + plugin stubs.

    The plugin stubs at ``installer/agents/hermes/plugins/{hal0,hal0-memory}/``
    are copied verbatim into ``$HERMES_HOME/plugins/{model-providers/hal0,memory/hal0-memory}/``.
    Real plugin bodies arrive in #241 + #242; this phase just stages
    the directory layout so re-runs after those slices land are a
    file-by-file overlay, not a structural change.

    Skips heavy work when the venv binary already exists at the
    expected version — re-runs of ``hal0 agent bootstrap hermes`` are
    cheap unless ``--repair`` forces re-install.
    """
    details: dict[str, Any] = {}
    venv = Path(state.venv)
    requirements = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "requirements.txt"
    wrapper_src = REPO_ROOT_FOR_INSTALLER / "installer" / "wrappers" / "hal0-hermes"
    plugin_src_root = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "plugins"

    if not requirements.is_file():
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"requirements.txt missing at {requirements}",
        )
    if not wrapper_src.is_file():
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"wrapper source missing at {wrapper_src}",
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
        _copy_wrapper(wrapper_src, WRAPPER_INSTALL_PATH)
        details["wrapper"] = str(WRAPPER_INSTALL_PATH)
    except OSError as exc:
        # Non-root operators land here — surface so the user can sudo.
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"wrapper install to {WRAPPER_INSTALL_PATH} failed: {exc}",
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
        "hal0": hermes_home / "plugins" / "model-providers" / "hal0",
        "hal0-memory": hermes_home / "plugins" / "memory" / "hal0-memory",
    }
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


def _resolve_primary_slot(*, fetcher: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pick the live primary chat slot from the local hal0 daemon.

    Returns a dict with the keys the config template needs. Falls back
    to a safe-but-unwired placeholder when no slot is loaded — the
    self_report phase surfaces this in the bootstrap summary.
    """
    fallback = {
        "model": "primary",
        "base_url": "http://127.0.0.1:8000/api/v1",
        "context_length": 32768,
    }
    if fetcher is None:

        def _real() -> dict[str, Any]:
            from urllib.error import URLError
            from urllib.request import urlopen

            try:
                with urlopen("http://127.0.0.1:8080/v1/health", timeout=2.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (URLError, OSError, json.JSONDecodeError):
                return {}

        fetcher = _real
    data = fetcher() or {}
    loaded = data.get("loaded") or data.get("slots") or []
    if isinstance(loaded, list) and loaded:
        first = loaded[0] if isinstance(loaded[0], dict) else {}
        return {
            "model": first.get("model") or first.get("model_id") or fallback["model"],
            "base_url": first.get("backend_url") or fallback["base_url"],
            "context_length": int(first.get("context_length") or fallback["context_length"]),
        }
    return fallback


def _render_config_yaml(
    *,
    primary: dict[str, Any] | None,
    chat_slots: list[dict[str, Any]] | None = None,
    stt: dict[str, Any] | None = None,
    tts: dict[str, Any] | None = None,
    agent_id: str = "hermes-agent",
    mcp_admin_url: str = "http://127.0.0.1:8080/mcp/admin",
    mcp_memory_url: str = "http://127.0.0.1:8080/mcp/memory",
) -> str:
    """Render the Hermes config.yaml via Jinja2.

    Variable shape matches the template's docstring (see
    ``src/hal0/agents/hermes_templates/config.yaml.j2``). Jinja2 is
    pinned in pyproject so the dep is always present in production
    bootstraps — no fallback needed.
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
        mcp_admin_url=mcp_admin_url,
        mcp_memory_url=mcp_memory_url,
    )


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


def _phase_config_write(state: BootstrapState) -> PhaseResult:
    """Atomically render ``$HERMES_HOME/config.yaml`` from the template.

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
    rendered = _render_config_yaml(
        primary=primary,
        agent_id=state.agent_id,
    )
    rendered = _apply_overrides(rendered, OVERRIDES_PATH)
    new_hash = content_hash(rendered)

    if config_path.exists() and content_hash(config_path.read_text(encoding="utf-8")) == new_hash:
        return PhaseResult(
            status=PhaseStatus.OK,
            hash=new_hash,
            details={"config_path": str(config_path), "unchanged": True},
        )

    hermes_home.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(".yaml.tmp")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, config_path)
    return PhaseResult(
        status=PhaseStatus.OK,
        hash=new_hash,
        details={"config_path": str(config_path), "primary_model": primary["model_id"]},
    )


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

    Uses stdlib urllib because the bootstrap can't assume httpx is
    installed in the hal0 daemon's venv (it usually is — but keeping
    this stdlib-only means env_probe can run on a minimal install).
    """
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-hal0-Agent": agent_id,
    }
    if private:
        headers["X-hal0-Private"] = "1"
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return {"ok": False, "tools": [], "error": str(exc)}
    tools = []
    result = data.get("result") if isinstance(data, dict) else None
    if isinstance(result, dict):
        raw_tools = result.get("tools") or []
        if isinstance(raw_tools, list):
            tools = [t.get("name") for t in raw_tools if isinstance(t, dict)]
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
    base = "http://127.0.0.1:8080"
    servers: list[dict[str, Any]] = [
        {
            "name": "hal0-admin",
            "url": f"{base}/mcp/admin",
            "private": False,
        },
        {
            "name": "hal0-memory",
            "url": f"{base}/mcp/memory",
            "private": True,
        },
    ]

    results: dict[str, Any] = {}
    warnings: list[str] = []
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
            continue
        results[name] = {
            "status": "ok",
            "tool_count": len(probe["tools"]),
            "tools": probe["tools"],
        }

    # Even with warnings we return OK — degraded MCP connectivity is
    # surfaced for smoke_tests + self_report to display, not a fatal
    # bootstrap blocker (per ADR-0013 + the plan §9 contract).
    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "servers": results,
            "allowlist_present": allowlist is not None,
            "warnings": warnings,
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
    vars_ = {
        "env": env_report,
        "hal0_version": _hal0_version_string(),
        "hermes_version": _hermes_version_pin(),
        "primary": None,
        "chat_slots": [],
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
        ("HERMES.md.j2", "HERMES.md"),
        ("AGENTS.md.j2", "AGENTS.md"),
    ):
        try:
            rendered[_out_name] = _render_template(tpl_name, **vars_)
        except Exception as exc:
            warnings.append(f"{tpl_name} render: {exc}; skipping")

    details: dict[str, Any] = {"warnings": warnings, "rendered": {}, "links": []}

    soul_path = hermes_home / "SOUL.md"
    h = _atomic_write(soul_path, rendered["SOUL.md"])
    details["rendered"]["SOUL.md"] = {"path": str(soul_path), "sha256": h}

    if "HERMES.md" in rendered:
        try:
            ETC_HAL0_DIR.mkdir(parents=True, exist_ok=True)
            hpath = ETC_HAL0_DIR / "HERMES.md"
            h = _atomic_write(hpath, rendered["HERMES.md"])
            details["rendered"]["HERMES.md"] = {"path": str(hpath), "sha256": h}
            # Mirror into HERMES_HOME/memories/HOST.md so context shows up in
            # the memory tier as well as cwd auto-injection.
            host_md = hermes_home / "memories" / "HOST.md"
            if _safe_symlink(hpath, host_md):
                details["links"].append(str(host_md))
        except OSError as exc:
            warnings.append(f"HERMES.md write to /etc/hal0: {exc}")

    if "AGENTS.md" in rendered:
        try:
            ETC_HAL0_DIR.mkdir(parents=True, exist_ok=True)
            apath = ETC_HAL0_DIR / "AGENTS.md"
            h = _atomic_write(apath, rendered["AGENTS.md"])
            details["rendered"]["AGENTS.md"] = {"path": str(apath), "sha256": h}
        except OSError as exc:
            warnings.append(f"AGENTS.md write to /etc/hal0: {exc}")

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
        if not deleted["ok"]:
            warnings.append(f"memory_delete: {deleted['error']}")

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


_DEFAULT_PRIMARY_BACKEND_URL = "http://127.0.0.1:8000/api/v1"


def _is_ready(slot: dict[str, Any]) -> bool:
    """True iff the slot reports a live/ready state."""
    state = slot.get("state") or slot.get("status") or ""
    return str(state).lower() in {"ready", "running", "loaded", "ok", "online"}


def _collect_chat_slots(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter `slots` down to chat-capable entries with a usable model_id."""
    out: list[dict[str, Any]] = []
    for s in slots:
        if _slot_kind(s) != "chat":
            continue
        model_id = _slot_model_id(s)
        if not model_id:
            continue
        out.append(
            {
                "alias": _slot_alias(s),
                "model_id": model_id,
                "backend_url": _slot_backend_url(s),
            }
        )
    return out


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
    chat_slots = _collect_chat_slots(slots)
    primary_raw = _resolve_primary_slot()
    primary = {
        "model_id": primary_raw["model"],
        "backend_url": primary_raw["base_url"],
        "context_length": primary_raw["context_length"],
    }

    try:
        rendered = _render_config_yaml(
            primary=primary,
            chat_slots=chat_slots,
            agent_id=state.agent_id,
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
        rendered = _render_config_yaml(
            primary=primary,
            chat_slots=_collect_chat_slots(slots),
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
    base_url = (cfg.get("model") or {}).get("base_url", "")
    if not base_url:
        return (False, "model.base_url unset in config.yaml")
    body = json.dumps({"messages": [{"role": "user", "content": "Reply with 'ready'"}]}).encode(
        "utf-8"
    )
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    req = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return (False, f"chat/completions: {exc}")
    try:
        msg = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return (False, "response missing choices[0].message.content")
    return ("ready" in msg.lower(), msg[:120])


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


PHASES: list[tuple[str, Callable[[BootstrapState], PhaseResult]]] = [
    ("preflight", _phase_preflight),
    ("install", _phase_install),
    ("env_probe", _phase_env_probe),
    ("home_init", _phase_home_init),
    ("config_write", _phase_config_write),
    ("mcp_wire", _phase_mcp_wire),
    ("context_link", _phase_context_link),
    ("namespace_register", _phase_namespace_register),
    ("model_automap", _phase_model_automap),
    ("voice_wire", _phase_voice_wire),
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
