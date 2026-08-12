"""Hermes-Agent provisioner — one linear, convergent :func:`install_hermes` pass.

The public entry point is :func:`install_hermes`: resolve a supported Python →
build the pinned-SDK venv → dir-drop the plugin trees → apply hal0's config keys
(``hermes config set`` + a targeted YAML deep-merge; never a wholesale
config.yaml rewrite) → render the Jinja context files → write the install
artifacts → wire the gateway secrets drop-in + API key → smoke test. Each step
converges its slice of host state and reports whether it changed anything, so a
second run over an already-provisioned box mutates nothing (``report.converged``).
Idempotency comes from every write being a converging write, NOT from stored
checkpoints — the old resumable ``provision.json`` / ``PHASES`` / ``PhaseContext``
machinery is gone.

The brain/persona/memory-identity steps (persona_seed, namespace_register,
brain_profile_seed, brain_profile_mcp_wire, self_report) still run here but are
marked ``# RELOCATE(brain-lane):`` — a concurrent lane moves them into the
hal0-api lifespan; they stay functioning until it lands.

A last-run report is written to ``/var/lib/hal0/state/agents/hermes/provision.json``
(outside ``$HERMES_HOME`` so a ``hermes reset`` can't trample it) purely for
``hal0 agent status`` / ``log`` to render — it is a snapshot, not a checkpoint.

Born-owned contract (§7.4 F.7): every ``$HERMES_HOME`` write here is born
``hal0:hal0`` because the CLI drops provisioning to the hal0 service user before
running this module (``cli/agent_commands._provision_hermes``: a root-only prelude
installs the ``/usr/local/bin`` wrapper + ensures the setgid hal0-owned skeleton,
then re-execs ``hal0 agent bootstrap hermes`` as hal0). Root:root artifacts (seed
TOML, driver env, gateway drop-in) go through the ``hal0-agentenv`` /
``hal0-systemctl`` sudo seams. ``--repair`` (root) additionally reconciles a
root-clobbered tree via :func:`reconcile_ownership_on_repair` before the
converging writes.
"""

from __future__ import annotations

import contextlib
import datetime
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import sqlite3
import subprocess  # nosec B404 — needed to spawn python -m venv + pip
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

from hal0.agents.role_slots import candidate_from_slot_mapping, resolve_role_slots
from hal0.system import seam as _seam

log = structlog.get_logger(__name__)

# Canonical last-run-report location. Lives outside $HERMES_HOME — Hermes owns
# its own tree, and the report must survive a `hermes reset`. This is no longer
# a resumable checkpoint (the linear installer has none), just a snapshot of the
# most recent run for `hal0 agent status` to render.
_DEFAULT_STATE_ROOT = Path("/var/lib/hal0/state/agents/hermes")
_STATE_FILE_NAME = "provision.json"


class PhaseStatus(StrEnum):
    """Per-step outcome.

    ``ok``   — step completed cleanly.
    ``warn`` — step completed but recorded non-fatal failures the operator
               should see (e.g. smoke_tests with one or more failed probes).
               Does NOT fail the overall install (:attr:`InstallReport.ok`
               only looks at ``fail``) — a warn phase converged, it just has
               something worth surfacing (#1793).
    ``skip`` — step didn't apply for this env (e.g. voice_wire with no slots).
    ``fail`` — step ran and failed.

    String-valued so JSON round-trips cleanly without a custom encoder.
    """

    OK = "ok"
    WARN = "warn"
    SKIP = "skip"
    FAIL = "fail"


@dataclass
class PhaseResult:
    """The return of one install-step body.

    ``details`` is a free-form dict the step stashes; ``install_hermes`` reads
    ``details["changed"]`` for the convergence signal and folds the rest into
    the :class:`InstallReport` / last-run report.
    """

    status: PhaseStatus
    details: dict[str, Any] = field(default_factory=dict)
    hash: str | None = None
    reason: str | None = None


@dataclass
class BootstrapState:
    """The resolved install target: where the venv + HERMES_HOME live + agent id.

    Formerly the in-memory mirror of the (retired) ``provision.json`` checkpoint;
    now a plain config holder threaded through the step bodies. Kept importable
    (``BootstrapState().agent_id``) for the CLI's memory-uninstall path.
    """

    hermes_home: str = "/var/lib/hal0/.hermes"
    venv: str = "/var/lib/hal0/venvs/hermes"
    agent_id: str = "hermes"


def _utcnow() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat().replace("+00:00", "Z")


def content_hash(*pieces: str | bytes) -> str:
    """Stable content hash steps use to detect "inputs unchanged".

    Steps that produce on-disk outputs (config.yaml, HERMES.md) hash the rendered
    content; a re-run computes the hash again and skips the write on a match.
    """
    h = hashlib.sha256()
    for piece in pieces:
        if isinstance(piece, str):
            piece = piece.encode("utf-8")
        h.update(piece)
    return h.hexdigest()


# ── Install-step implementations ─────────────────────────────────────────────
#
# Each step body has signature ``(ctx: _StepCtx) -> PhaseResult`` and converges
# one slice of host state. :func:`install_hermes` (bottom of file) runs them in
# order and folds each ``PhaseResult`` into an :class:`InstallReport`.


# Pinned constants — keep these in sync with installer/agents/hermes/
# requirements.txt and the wrapper script. The constants are exposed
# at module scope so tests can monkey-patch them onto a tmp path.
PYTHON_MIN = (3, 12)
# Exclusive cap for the hermes venv interpreter, mirroring hermes-agent's
# wheel metadata: every release since 0.16.0 pins `requires-python
# >=3.11,<3.14`. On a >=3.14 interpreter pip filters those wheels out and
# falls back to 0.15.2 — whose wheel is broken (imports
# hermes_cli.dashboard_auth, ships without the subpackage) — so 3.14 must be
# rejected, not merely deprioritized (#1248). Single source of truth for the
# resolver, the preflight message, and the stale-venv rebuild; relax it here
# when upstream ships 3.14 support (#1249).
PYTHON_MAX_EXCLUSIVE = (3, 13)
MIN_FREE_GIB = 4
DAEMON_HEALTH_URL = "http://127.0.0.1:8080/api/health"
WRAPPER_INSTALL_PATH = Path("/usr/local/bin/hal0-hermes")
# Canonical CLI entry point on PATH (locked decision #3). The thin
# ``hermes`` wrapper injects HAL0_AGENT_ID and execs the venv hermes
# WITHOUT pinning HERMES_HOME (the hermes default ~/.hermes resolves to
# /var/lib/hal0/.hermes for the hal0 user). ``hal0-hermes`` stays as a
# back-compat symlink to this.
HERMES_CLI_INSTALL_PATH = Path("/usr/local/bin/hermes")


def _resolve_installer_root(*, module_file: Path | None = None, prefix: str | None = None) -> Path:
    """Locate the tree that contains ``installer/agents`` for both layouts.

    **Dev / editable:** the repo root is an ancestor of this module
    (``…/src/hal0/agents/hermes_provision.py`` → repo root at
    ``parents[3]``), and the top-level ``installer/`` lives there.

    **Prod / non-editable FHS install:** ``install.sh`` pip-installs hal0
    non-editable, so the hal0 package is a *copy* under the venv's
    site-packages and ``parents[3]`` lands inside the venv
    (``…/venv/lib/pythonX.Y``) — where the top-level ``installer/`` is
    absent (it is NOT shipped in the wheel). The versioned source tree
    that ``install.sh`` lays down sits next to the venv:
    ``…/hal0/venv`` (``sys.prefix``) ↔ ``…/hal0/current/installer``.

    We probe candidates in order and return the first that actually
    holds ``installer/agents``; falling back to the editable heuristic so
    a downstream "file missing" error names a concrete path rather than
    crashing here. ``module_file`` / ``prefix`` are injectable for tests.
    """
    mod = (module_file or Path(__file__)).resolve()
    pfx = Path(prefix if prefix is not None else sys.prefix)
    candidates = [
        mod.parents[3],  # editable repo root
        pfx.parent / "current",  # FHS: …/hal0/current (symlink)
        pfx.parent,  # FHS without the `current` symlink
    ]
    for cand in candidates:
        if (cand / "installer" / "agents").is_dir():
            return cand
    return candidates[0]


REPO_ROOT_FOR_INSTALLER = _resolve_installer_root()

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


def path_is_writable(target: str | Path) -> bool:
    """Whether we can actually create a file at (or under) ``target``.

    Walks up to ``target``'s nearest existing ancestor — the directory the
    bootstrap will start creating things in — and attempts a real
    ``touch``/``unlink`` there. We probe rather than calling
    :func:`os.access` because ``os.access`` reports stale answers under
    SELinux, POSIX ACLs and NFS root-squash, and because it can't see that an
    *existing* root-owned ``$HERMES_HOME`` (created by the root daemon before
    ``agent install`` runs) is unwritable to a non-root installer — exactly
    the case that detonated several phases deep on a Fedora install.
    """
    anchor = Path(target)
    while not anchor.exists():
        parent = anchor.parent
        if parent == anchor:  # reached the filesystem root with nothing existing
            return False
        anchor = parent
    if not anchor.is_dir():
        return False
    probe = anchor / f".hal0-writeprobe-{os.getpid()}"
    try:
        probe.touch()
    except OSError:
        return False
    with contextlib.suppress(OSError):  # created but cleanup may fail — still writable
        probe.unlink()
    return True


def _phase_preflight(ctx: _StepCtx) -> PhaseResult:
    """Hard-fail when the host can't host Hermes.

    Documented blockers (plan §4):

    * A hermes-compatible Python (3.11-3.13) resolvable — the venv is
      built with an explicit interpreter, and hermes-agent wheels cap
      ``requires-python <3.14``, so \"≥ 3.11\" alone is not enough
      (Ubuntu 26.04 ships 3.14 only, #1248). A host with uv passes even
      without one — the install phase provisions a managed interpreter
      (#1250).
    * ``hal0`` daemon reachable at ``/api/health`` — agents that can't
      reach hal0 are useless. Catch it now instead of during config_write.
      Probes the OPEN shallow-liveness endpoint (spec-kb1-auth exposure
      allowlist), NOT the admin-scoped ``/api/status`` which 401s even when
      ``auth_enabled`` is false and would fail every provision (#kb1).
    * venv tree + ``$HERMES_HOME`` write-probed — we create both in later
      phases; a real touch/unlink catches a root-owned ``$HERMES_HOME`` (or
      SELinux/ACL block) that an ``os.access`` check on ``/var/lib/hal0`` misses.
    * ≥ 4 GiB free under ``/var/lib/hal0/`` — Hermes deps + a typical
      memory cache run ~3 GiB; 4 GiB leaves headroom for venv rebuild.
    """
    state = ctx.state
    failures: list[str] = []
    details: dict[str, Any] = {}

    py_version = sys.version_info[:3]
    details["python_version"] = ".".join(str(p) for p in py_version)
    # What matters is the VENV interpreter, not the running one — resolve it
    # the same way the install phase will, so a 3.14-only host fails here
    # with a real explanation instead of three phases later inside pip.
    venv_python = _resolve_supported_python()
    details["venv_python"] = venv_python
    if venv_python is None:
        # No system interpreter in range. uv can still provision one during
        # the install phase (#1250) — we only check availability here, not
        # download: preflight must stay fast and side-effect free. Fail only
        # when that fallback is closed too.
        uv = _uv_available()
        details["uv_python_fallback"] = uv is not None
        if uv is None:
            failures.append(_python_range_error())

    rc = ctx.io.http_get(DAEMON_HEALTH_URL)
    details["daemon_http_status"] = rc
    if rc != 200:
        failures.append(
            f"hal0 daemon unreachable at {DAEMON_HEALTH_URL} (status={rc or 'no-response'}) "
            "— run `systemctl start hal0`",
        )

    var_lib = Path(state.venv).parent.parent  # /var/lib/hal0/
    details["var_lib_path"] = str(var_lib)

    # Write-probe the ACTUAL targets — the venv tree and $HERMES_HOME — not
    # just os.access() on /var/lib/hal0. A root-owned $HERMES_HOME created by
    # the (root) daemon before `agent install` sails past a var_lib-only check,
    # then env_probe detonates with a raw EACCES several phases deep (observed
    # on Fedora: hermes provisioned as a non-root login user against root-owned
    # /var/lib/hal0). os.access() also lies under SELinux / ACLs / NFS.
    blocked = sorted(
        str(p) for p in (Path(state.venv), Path(state.hermes_home)) if not path_is_writable(p)
    )
    details["write_blocked"] = blocked
    if blocked:
        hint = (
            f"run `sudo install -d -o hal0 -g hal0 -m 0755 {var_lib}`"
            if os.geteuid() == 0
            else "re-run as root: `sudo hal0 agent install hermes`"
        )
        failures.append(f"not writable: {', '.join(blocked)} — {hint}")
    else:
        # var_lib's nearest existing ancestor (it exists if the probe passed).
        anchor = var_lib
        while not anchor.exists():
            anchor = anchor.parent
        st = os.statvfs(anchor)
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


def _python_range_error() -> str:
    """Actionable failure text for hosts with no hermes-compatible Python."""
    lo = ".".join(str(p) for p in PYTHON_MIN)
    cap = ".".join(str(p) for p in PYTHON_MAX_EXCLUSIVE)
    return (
        f"exact Python 3.12 was not found — Hermes requires a deterministic "
        f"3.12 venv (upstream metadata is `{lo} <= version < {cap}`). "
        f"Install python3.12 with its venv module, or install uv "
        f"(https://astral.sh/uv); hal0 will provision Python {UV_PYTHON_FALLBACK} "
        f"under {UV_PYTHON_INSTALL_DIR}"
    )


#: Minor version uv provisions when no system interpreter qualifies (#1250).
#: Newest supported release — keep inside [PYTHON_MIN, PYTHON_MAX_EXCLUSIVE).
UV_PYTHON_FALLBACK = "3.12"

#: Where uv-managed interpreters land. uv's default (~/.local/share/uv) is
#: under /root with mode 0700 when provisioning runs as root — but the hermes
#: venv executes as the ``hal0`` user via a symlinked base interpreter, which
#: would then be unreachable. A world-readable tree under /var/lib/hal0 keeps
#: the interpreter usable by the service and survives root-homedir cleanups.
UV_PYTHON_INSTALL_DIR = Path("/var/lib/hal0/python")

#: The hal0 service state root — the fallback HOME for provisioning subprocesses
#: (see :func:`_hal0_subprocess_env`). install.sh chowns ``.cache``/``.config``
#: under here to hal0, so tools that write dot-dirs land in a hal0-owned tree.
_HAL0_STATE_ROOT = Path("/var/lib/hal0")
#: uv cache home, pinned under the hal0 state root (never ~/.cache → /root).
UV_CACHE_DIR = _HAL0_STATE_ROOT / ".cache" / "uv"
HERMES_PYTHON_ENV = Path("/etc/hal0/hermes-python.env")


def _validate_hermes_python(path: str, *, runner: Any = subprocess) -> tuple[int, int]:
    """Execute *path* and require the exact Hermes Python policy version."""
    if not path or not Path(path).is_absolute() or any(c in path for c in "\n\r\\\"';&|$"):
        raise ValueError(f"invalid HAL0_HERMES_PYTHON path: {path!r}")
    try:
        result = runner.run(
            [path, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError(f"Hermes Python override is not executable: {path}") from exc
    version = (result.stdout or "").strip()
    if version != "3.12":
        raise ValueError(
            f"Hermes Python must be exactly 3.12: {path} reports {version or 'unknown'}"
        )
    return (3, 12)


def _read_hermes_python_env(path: Path = HERMES_PYTHON_ENV) -> str | None:
    """Read the persisted single-variable Hermes interpreter contract."""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    values = [
        line.removeprefix("HAL0_HERMES_PYTHON=")
        for line in lines
        if line.startswith("HAL0_HERMES_PYTHON=")
    ]
    if len(lines) != 1 or len(values) != 1 or not values[0]:
        raise ValueError(f"invalid Hermes Python environment file: {path}")
    return values[0]


def _persist_hermes_python(
    path: str, env_path: Path = HERMES_PYTHON_ENV, *, runner: Any = subprocess
) -> None:
    """Atomically persist a validated Hermes interpreter path."""
    _validate_hermes_python(path, runner=runner)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    content = f"HAL0_HERMES_PYTHON={path}\n"
    if env_path.exists() and env_path.read_text(encoding="utf-8") == content:
        return
    tmp = env_path.with_name(f".{env_path.name}.{os.getpid()}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, env_path)


def resolve_hermes_python(
    *,
    env: dict[str, str] | None = None,
    env_path: Path = HERMES_PYTHON_ENV,
    prober: Callable[[str], str | None] = shutil.which,
    runner: Any = subprocess,
) -> str:
    """Resolve exact Python 3.12 using the documented precedence order."""
    environ = os.environ if env is None else env
    source = "environment"
    candidate = environ.get("HAL0_HERMES_PYTHON")
    if candidate is None:
        candidate = _read_hermes_python_env(env_path)
        source = "persisted"
    if candidate is None:
        candidate = prober("python3.12")
        source = "system"
    if candidate is None:
        candidate = _provision_python_via_uv(prober, runner)
        source = "uv-managed"
    if candidate is None:
        raise RuntimeError("Python 3.12 is unavailable; install python3.12 or uv and retry")
    _validate_hermes_python(candidate, runner=runner)
    log.info("hermes-python-resolved", source=source, path=candidate, version="3.12")
    return candidate


def _hal0_service_home() -> str:
    """The hal0 service account's home dir, for subprocess HOME sanitization.

    Provisioning runs as the ``hal0`` service user (the CLI drops privileges
    before this module runs), but the *inherited* environment can still carry
    the root caller's ``HOME=/root`` (O15): the drop-to-hal0 seam sets HOME, yet
    a caller that bypasses it — or a stale env — leaves ``HOME`` pointing at
    root's 0700 home. uv/pip then reach into ``/root`` (``uv.toml`` /
    ``.cache``) and fail with ``Permission denied`` as hal0. Resolve the real
    hal0 home from passwd, falling back to the state root.
    """
    try:
        home = pwd.getpwnam("hal0").pw_dir
    except (KeyError, OSError):
        home = None
    return home or str(_HAL0_STATE_ROOT)


def _hal0_subprocess_env(**overrides: str) -> dict[str, str]:
    """os.environ with HOME pinned to the hal0 home + caller ``overrides`` (O15).

    Every provisioning subprocess spawned *after* the drop to hal0 (uv, venv,
    pip, hermes-cli) inherits this so a leaked ``HOME=/root`` can never send a
    tool reaching for ``~/uv.toml`` / ``~/.cache`` into root's unwritable home.
    HOME is forced (not defaulted) precisely because the leak is that HOME is
    already set to the wrong value.
    """
    env = {**os.environ, "HOME": _hal0_service_home()}
    env.update(overrides)
    return env


def _uv_available(prober: Callable[[str], str | None] = shutil.which) -> str | None:
    return prober("uv")


def _provision_python_via_uv(
    prober: Callable[[str], str | None] = shutil.which,
    runner: Any = subprocess,
) -> str | None:
    """Fetch a uv-managed Python as the last resort (#1250).

    ``uv python install`` is idempotent (a no-op when the version is already
    present), and ``uv python find`` returns a stable path under
    ``UV_PYTHON_INSTALL_DIR`` — so repeat runs and ``--repair`` deterministically
    reuse the interpreter from the first provisioning instead of hunting anew.
    Returns ``None`` when uv is absent or the fetch fails (offline) — callers
    fall through to the actionable range error.
    """
    uv = _uv_available(prober)
    if uv is None:
        return None
    # Sanitize HOME + pin uv's cache to hal0-owned paths (O15): as hal0 with a
    # leaked HOME=/root, uv would try to open /root/uv.toml and /root/.cache/uv
    # → "Permission denied" → bootstrap failed. Force HOME to the hal0 home and
    # UV_CACHE_DIR under the state root so uv never reaches into /root.
    env = _hal0_subprocess_env(
        UV_PYTHON_INSTALL_DIR=str(UV_PYTHON_INSTALL_DIR),
        UV_CACHE_DIR=str(UV_CACHE_DIR),
    )
    # Create the install dir world-traversable BEFORE uv writes into it.
    # uv inherits root's umask, so on a restrictive-umask host (e.g. 077) the
    # tree lands 0700 and the hal0 service user can't traverse to the symlinked
    # base interpreter — silently reproducing the very "gateway venv python
    # won't start" failure this /var/lib/hal0 move was meant to fix. chmod the
    # leaf to 0o755 to guarantee traversal; idempotent under --repair, and
    # mirrors the other hal0-reachable dirs (gateway drop-in .chmod(0o755)).
    # Best-effort: a perms hiccup (e.g. unprivileged
    # caller) must not abort an otherwise-working provision, so log and
    # continue rather than fall through to the None/range-error path.
    try:
        UV_PYTHON_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        UV_PYTHON_INSTALL_DIR.chmod(0o755)
    except OSError as exc:
        log.warning(
            "uv-install-dir-perms-failed",
            path=str(UV_PYTHON_INSTALL_DIR),
            error=str(exc),
        )
    try:
        runner.run(  # nosec B603 — argv is a constant uv invocation
            [uv, "python", "install", UV_PYTHON_FALLBACK],
            check=True,
            env=env,
        )
        found = runner.run(  # nosec B603
            [uv, "python", "find", UV_PYTHON_FALLBACK],
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    path = (found.stdout or "").strip()
    return path or None


def _ensure_supported_python(
    prober: Callable[[str], str | None] = shutil.which,
    *,
    runner: Any = subprocess,
    running: tuple[int, int] | None = None,
) -> str | None:
    """Resolve the exact Python 3.12 interpreter for the Hermes venv.

    A qualifying system ``python3.12`` wins before uv downloads a managed 3.12;
    explicit and persisted overrides are validated before either fallback.
    """
    configured = os.environ.get("HAL0_HERMES_PYTHON")
    if configured is None:
        configured = _read_hermes_python_env()
    if configured is not None:
        _validate_hermes_python(configured, runner=runner)
        return configured
    found = _resolve_supported_python(prober, running=running)
    if found is not None:
        return found
    return _provision_python_via_uv(prober, runner)


def _resolve_supported_python(
    prober: Callable[[str], str | None] = shutil.which,
    *,
    running: tuple[int, int] | None = None,
) -> str | None:
    """Find an exact Python 3.12 interpreter for Hermes venv creation.

    Probes explicit ``python3.12`` on PATH so the venv minor is deterministic.
    The running interpreter is accepted only when it is itself Python 3.12.
    """
    for minor in range(PYTHON_MAX_EXCLUSIVE[1] - 1, PYTHON_MIN[1] - 1, -1):
        explicit = prober(f"python3.{minor}")
        if explicit:
            return explicit
    current = running if running is not None else sys.version_info[:2]
    if PYTHON_MIN <= current < PYTHON_MAX_EXCLUSIVE:
        return sys.executable
    return None


def _venv_python(venv: Path) -> Path:
    return venv / "bin" / "python"


def _venv_python_minor(venv: Path) -> tuple[int, int] | None:
    """Minor version of an existing venv, read from its ``lib/pythonX.Y`` dir.

    Filesystem-only on purpose — the venv may be too broken to execute
    (that's exactly the case we're probing for). ``None`` when the layout
    is unrecognizable; callers should treat that as \"leave it alone\".
    """
    for entry in sorted(venv.glob("lib/python3.*")):
        try:
            major, minor = entry.name.removeprefix("python").split(".", 1)
            return (int(major), int(minor))
        except ValueError:
            continue
    return None


def _install_venv(
    venv: Path,
    requirements: Path,
    *,
    runner: Any = subprocess,
    python_resolver: Callable[[], str | None] = _ensure_supported_python,
) -> None:
    """Create the venv at ``venv`` and install ``requirements`` into it.

    Two-step: ``python3.12 -m venv`` then ``pip install -r``. The venv itself
    is stdlib-built — uv enters only inside the resolver, as the last-resort
    interpreter fetch on hosts with no packaged Python 3.12.
    """
    py = python_resolver()
    if py is None:
        raise RuntimeError(_python_range_error())
    venv.parent.mkdir(parents=True, exist_ok=True)
    existing_minor = _venv_python_minor(venv) if venv.exists() else None
    needs_replacement = existing_minor is not None and existing_minor != (3, 12)
    target = venv
    rollback: Path | None = None
    if needs_replacement:
        # Build beside the live venv. The live tree remains runnable if pip,
        # network, or the replacement interpreter fails.
        target = Path(tempfile.mkdtemp(prefix=f".{venv.name}.build-", dir=venv.parent))
    try:
        env = _hal0_subprocess_env()
        target_preexists = target.exists()
        if needs_replacement or not target_preexists:
            runner.run([py, "-m", "venv", str(target)], check=True, env=env)  # nosec B603
        pip = _venv_python(target)
        runner.run([str(pip), "-m", "pip", "install", "--upgrade", "pip"], check=True, env=env)  # nosec B603
        runner.run([str(pip), "-m", "pip", "install", "-r", str(requirements)], check=True, env=env)  # nosec B603
        if needs_replacement:
            rollback = venv.with_name(f"{venv.name}.rollback-{os.getpid()}")
            os.replace(venv, rollback)
            try:
                os.replace(target, venv)
            except BaseException:
                os.replace(rollback, venv)
                raise
            shutil.rmtree(rollback)
    except BaseException:
        if needs_replacement and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise


#: Default managed-venv + requirements + HERMES_HOME for the upgrade path.
#: Mirrors the BootstrapState defaults so `hal0 agent upgrade hermes` can run
#: standalone (it pip-upgrades + migrates before re-running the state machine).
HERMES_VENV_DEFAULT = Path("/var/lib/hal0/venvs/hermes")
HERMES_HOME_DEFAULT = Path("/var/lib/hal0/.hermes")
HERMES_REQUIREMENTS = (
    REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "requirements.txt"
)


# ── Vetted Hermes production refs (compatibility allowlist) ──────────────────
#
# Production bundles pin hermes-agent to a REVIEWED upstream commit/tag — not a
# moving branch or an open version range (design doc §"Compatibility posture" +
# docs/rework/hermes-official-integration-research.md). A git ref carries no PEP
# 440 version, so the broken-build floor guard (#1247: hermes-agent 0.15.2's
# wheel imports ``hermes_cli.dashboard_auth``, a module it never ships) cannot
# reason about a pin numerically. A pinned ref is therefore acceptable ONLY when
# it is in this reviewed allowlist — an unreviewed VCS pin is rejected exactly
# like a broken published version. Lifting the pin to a new upstream ref means
# adding it here AND refreshing the contract fixtures under
# ``tests/fixtures/hermes/contracts/`` +
# ``tests/agents/hermes/test_contract_compatibility.py``.
VETTED_HERMES_REFS: frozenset[str] = frozenset({"9de9c25f620ff7f1ce0fd5457d596052d5159596"})

# Broken-build floor (#1247): every hermes-agent below this is off-limits to any
# resolver, including old-Python wheel fallbacks that would otherwise land on the
# broken 0.15.2 build.
HERMES_MIN_VERSION: tuple[int, int, int] = (0, 16, 0)

_HERMES_FLOOR_RE = re.compile(r">=\s*(\d+)\.(\d+)\.(\d+)")
_HERMES_GIT_REF_RE = re.compile(r"git\+https?://\S+?@([0-9A-Za-z._+-]+)")
_HERMES_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _hermes_requirement_line(text: str) -> str:
    """The single active ``hermes-agent`` requirement line (comments skipped)."""
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and line.startswith("hermes-agent"):
            return line
    raise ValueError("no hermes-agent requirement line found")


def hermes_requirement_floor(line: str) -> tuple[int, int, int] | None:
    """The ``>=X.Y.Z`` version floor of a requirement line, or ``None``.

    ``None`` when the line carries no floor — e.g. a bare git-commit pin, which
    has no PEP 440 version to compare against :data:`HERMES_MIN_VERSION`.
    """
    m = _HERMES_FLOOR_RE.search(line)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def hermes_pinned_ref(line: str) -> str | None:
    """The git commit/tag a requirement pins to, or ``None`` for a version spec."""
    m = _HERMES_GIT_REF_RE.search(line)
    return m.group(1) if m else None


def hermes_requirement_is_vetted(text: str) -> bool:
    """Whether the shipped requirement forecloses the broken 0.15.2 build.

    Acceptable via EITHER reviewed means (never an arbitrary/unreviewed pin):

      * a version floor ``>= HERMES_MIN_VERSION`` — no resolver, including
        old-Python wheel fallbacks, can select the broken build; OR
      * a pin to a commit/tag in :data:`VETTED_HERMES_REFS` — the ref was built
        from vetted upstream source that passed the contract compatibility test.
    """
    line = _hermes_requirement_line(text)
    floor = hermes_requirement_floor(line)
    if floor is not None and floor >= HERMES_MIN_VERSION:
        return True
    ref = hermes_pinned_ref(line)
    return ref is not None and ref in VETTED_HERMES_REFS


def upgrade_hermes_runtime(
    *,
    venv: Path = HERMES_VENV_DEFAULT,
    requirements: Path = HERMES_REQUIREMENTS,
    hermes_home: Path = HERMES_HOME_DEFAULT,
    version: str | None = None,
    runner: Any = subprocess,
) -> tuple[bool, str]:
    """Pull the latest matching ``hermes-agent`` into the venv + reconcile config.

    This is the runtime half of ``hal0 agent upgrade hermes`` — the package move
    that the old hard pin (issue #240) used to forbid:

      1. ``pip install -U`` the requirements (floor/cap from requirements.txt),
         or an exact ``--to=<version>`` when the operator pins one.
      2. ``hermes config migrate`` so the on-disk config.yaml schema matches the
         newly-installed hermes build — hermes owns + migrates its own config,
         hal0 only layers its keys on top, so a minor bump no longer strands us.

    Non-fatal on the migrate step: a migrate hiccup is surfaced but doesn't fail
    the upgrade (the subsequent reprovision re-renders + re-chowns to hal0). The
    caller runs ``bootstrap hermes --repair`` afterwards to converge the rest.

    Returns ``(ok, message)``. ``ok`` is False only when the pip upgrade itself
    fails (no venv, network/resolver error) — that's a real, actionable stop.
    """
    pip = _venv_python(venv)
    if not pip.exists():
        return False, f"hermes venv missing at {venv} — run `hal0 agent install hermes` first"

    spec = f"hermes-agent[web]=={version}" if version else None
    pip_argv = [str(pip), "-m", "pip", "install", "--upgrade"]
    pip_argv += [spec] if spec else ["-r", str(requirements)]
    try:
        runner.run(pip_argv, check=True)  # nosec B603 — argv from local config
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"pip upgrade failed: {exc}"

    # Reconcile the config schema to the freshly-installed hermes. hermes owns
    # the file; this adds/migrates ITS keys without touching hal0's overlay.
    hermes_bin = pip.parent / "hermes"
    migrated = False
    try:
        env = _hal0_subprocess_env(HERMES_HOME=str(hermes_home))  # HOME-sanitized (O15)
        runner.run([str(hermes_bin), "config", "migrate"], check=True, env=env)  # nosec B603
        migrated = True
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("hermes_provision.config_migrate_failed", error=str(exc))

    target = version or "latest matching requirements"
    suffix = " + config migrated" if migrated else " (config migrate skipped — see logs)"
    return True, f"hermes-agent upgraded → {target}{suffix}"


def _copy_wrapper(wrapper_src: Path, wrapper_dst: Path) -> None:
    """Copy + chmod the wrapper into ``wrapper_dst`` — euid-aware (§7.4).

    ``/usr/local/bin/hermes`` is root-only install infra. On a system install
    ``install.sh``'s root prelude lays it down BEFORE dropping the provisioner to
    the hal0 user, so root (euid==0) installs it here directly; a non-root (hal0)
    caller finds it already present (prelude-installed) and skips — it cannot
    write ``/usr/local/bin``. A plain overwrite (no foreign backup — the
    adopt/capture path is retired).
    """
    if os.geteuid() != 0:
        if not wrapper_dst.exists():
            log.warning("hermes_provision.wrapper_absent_nonroot", dst=str(wrapper_dst))
        return
    wrapper_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wrapper_src, wrapper_dst)
    wrapper_dst.chmod(0o755)


def _install_cli_wrapper(wrapper_src: Path) -> dict[str, Any]:
    """Install the root-only ``/usr/local/bin/hermes`` wrapper (§7.4 split).

    The canonical entry point lives in root-owned ``/usr/local/bin``. When the
    provisioner runs as hal0 (§7.4 drop-to-hal0), install.sh's root prelude has
    already laid it down, so ``_copy_wrapper`` detects non-root and skips.

    Raises ``OSError`` on a genuine root-context write failure so the caller can
    surface a sudo hint.
    """
    _copy_wrapper(wrapper_src, HERMES_CLI_INSTALL_PATH)
    return {"hermes_cli": str(HERMES_CLI_INSTALL_PATH)}


def _copy_plugin_tree(src: Path, dst: Path) -> bool:
    """Mirror a plugin directory; convergent (skips when already identical).

    Returns True iff ``dst`` was (re)written. A byte-identical existing tree is
    left untouched so a converged re-run reports no change.
    """
    if dst.exists() and _dirs_identical(src, dst):
        return False
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return True


def _dirs_identical(a: Path, b: Path) -> bool:
    """Shallow-recursive content compare of two directory trees."""
    import filecmp

    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    return all(_dirs_identical(a / sub, b / sub) for sub in cmp.common_dirs)


def _phase_install(ctx: _StepCtx) -> PhaseResult:
    """Provision the managed Hermes venv + ``/usr/local/bin/hermes`` + plugins.

    The plugin package at ``installer/agents/hermes/plugins/hal0-memory/`` (the
    canonical, shipped ``MemoryProvider`` source — see
    ``tests/agents/test_hal0_memory_client.py`` for its contract tests) is
    copied into ``$HERMES_HOME/plugins/hal0-memory/``. A concurrent lane ships
    the ``hal0-provider`` model-provider tree, dir-dropped alongside it into
    ``$HERMES_HOME/plugins/model-providers/hal0/`` — the copy tolerates a missing
    source dir (skipped-step report line) until that lane lands.

    Converges: skips the venv build when ``bin/hermes`` already exists (unless
    ``--repair``), and each plugin tree copy hash-skips when already identical.
    ``details["changed"]`` is the convergence signal.
    """
    state = ctx.state
    details: dict[str, Any] = {}
    changed = False
    venv = Path(state.venv)
    requirements = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "requirements.txt"
    hermes_wrapper_src = REPO_ROOT_FOR_INSTALLER / "installer" / "wrappers" / "hermes"
    plugin_src_root = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "plugins"
    hermes_home = Path(state.hermes_home)

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

    # ── root-only CLI infra (§7.4 privilege split) ──────────────────────────
    # /usr/local/bin/hermes lives in root-owned /usr/local/bin and is euid-guarded:
    # root installs it, a hal0-run provisioner finds it prelude-installed + skips.
    try:
        details.update(_install_cli_wrapper(hermes_wrapper_src))
    except OSError as exc:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"wrapper install to {HERMES_CLI_INSTALL_PATH} failed: {exc}",
            details=details,
        )

    # ── hal0-owned artifacts (born hal0:hal0 when the provisioner runs as hal0) ─
    hermes_bin = _venv_python(venv).parent / "hermes"
    if not hermes_bin.exists() or ctx.repair:
        try:
            ctx.io.install_venv(venv, requirements)
            changed = True
        except (subprocess.SubprocessError, RuntimeError, OSError) as exc:
            return PhaseResult(
                status=PhaseStatus.FAIL,
                reason=f"venv install failed: {exc}",
                details=details,
            )
    details["venv"] = str(venv)
    details["hermes_bin"] = str(hermes_bin)

    # Plugin dir-drops into $HERMES_HOME/plugins. hal0-memory is the shipped
    # MemoryProvider tree; hal0-provider is the model-provider tree a concurrent
    # lane ships (its source may be absent in this worktree — a missing source
    # is a skipped-step report line, not a failure).
    plugin_targets = {
        "hal0-memory": hermes_home / "plugins" / "hal0-memory",
        "hal0-provider": hermes_home / "plugins" / "model-providers" / "hal0",
    }
    # The shipped hal0-memory tree is required; hal0-provider (concurrent lane)
    # is optional and its absent source is a skipped-step report line.
    optional_plugins = {"hal0-provider"}
    plugins_written: list[str] = []
    plugins_skipped: list[str] = []
    for src_name, dst in plugin_targets.items():
        src = plugin_src_root / src_name
        if not src.exists():
            if src_name not in optional_plugins:
                return PhaseResult(
                    status=PhaseStatus.FAIL,
                    reason=f"plugin source missing at {src}",
                )
            plugins_skipped.append(f"{src_name}: source dir {src} absent")
            continue
        try:
            if _copy_plugin_tree(src, dst):
                changed = True
            plugins_written.append(str(dst))
        except OSError as exc:
            return PhaseResult(
                status=PhaseStatus.FAIL,
                reason=f"plugin copy {src} -> {dst} failed: {exc}",
            )
    details["plugins"] = plugins_written
    if plugins_skipped:
        details["plugins_skipped"] = plugins_skipped
    details["changed"] = changed

    # No chown-back (§7.4 F.7): provisioning runs as hal0, so the venv under
    # /var/lib/hal0/venvs is born hal0:hal0.
    return PhaseResult(status=PhaseStatus.OK, details=details)


# ── Phase D: home_init ──────────────────────────────────────────────────────


#: The ``.hal0-managed`` stamp. Its foreign-home-DETECTION role died with the
#: adopt/capture path, but one contract is still live: the agent manager's
#: uninstall gate (``manager._safe_to_remove_data_dir``) refuses to ``rmtree``
#: a converged home that lacks this marker, so a user's pre-existing
#: ``~/.hermes``-style tree is never nuked. Every hal0 write path into
#: HERMES_HOME must therefore keep stamping it, or uninstall goes inert.
_HAL0_MANAGED_MARKER = ".hal0-managed"


def mark_home_managed_if_owned(hermes_home: Path) -> bool:
    """Ensure ``hermes_home`` exists and stamp it hal0-managed.

    The adopt/capture path is gone (hal0 owns HERMES_HOME by construction), so
    this no longer distinguishes a "foreign" tree — it makes the directory and
    stamps :data:`_HAL0_MANAGED_MARKER` so the manager's uninstall gate keeps
    recognising the home as hal0's to remove. Kept as an importable seam for
    the hal0-api lifespan, which calls it before seeding default personas.
    """
    hermes_home.mkdir(parents=True, exist_ok=True)
    marker = hermes_home / _HAL0_MANAGED_MARKER
    if not marker.exists():
        marker.touch()
    return True


def _phase_home_init(ctx: _StepCtx) -> PhaseResult:
    """Make the ``$HERMES_HOME`` layout canonical — ``mkdir`` the standard tree.

    Convergent: ``mkdir(exist_ok=True)`` for each subdir; ``details["changed"]``
    is True only when a directory was actually created.
    """
    hermes_home = Path(ctx.state.hermes_home)

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
        # scratch/: terminal.cwd target (RATIFIED 2026-07-18). Hermes' local
        # terminal backend spawns shells here instead of the read-only,
        # secret-bearing /etc/hal0. Born hal0:hal0 under the setgid state root.
        "scratch",
    )
    created = False
    for sub in standard_subdirs:
        d = hermes_home / sub
        if not d.exists():
            created = True
        d.mkdir(parents=True, exist_ok=True)

    # Stamp the managed marker (uninstall-gate contract — see
    # _HAL0_MANAGED_MARKER). Convergent: only counts as a change once.
    marker = hermes_home / _HAL0_MANAGED_MARKER
    if not marker.exists():
        marker.touch()
        created = True

    # No chown-back (§7.4 F.7): the tree is created as hal0 (the CLI drops
    # provisioning to hal0 before this pipeline) under the setgid /var/lib/hal0,
    # so the whole $HERMES_HOME layout is born hal0:hal0.
    return PhaseResult(
        status=PhaseStatus.OK,
        details={"hermes_home": str(hermes_home), "changed": created},
    )


# ── Phase D2: kanban_db_init ─────────────────────────────────────────────────
#
# O20 (docs/rework/r4-stage-validation.md): the Hermes gateway's kanban-board
# WATCHER opens ``$HERMES_HOME/kanban.db`` via a raw sqlite path — never
# through ``hermes_cli.kanban_db.connect()``. ``connect()``'s first-call
# auto-init is the ONLY thing that has ever created the schema, and today that
# only fires when hal0's HP-executor registers (``HERMES_DASHBOARD_BASE_URL``
# set — see ``board/hermes_executor.py``). A box whose executor never
# registers therefore has a watcher that hits ``no such table: tasks`` /
# ``kanban_notify_subs`` every tick. Operator-validated fix (both live boxes):
# calling ``init_db(<kanban.db>)`` creates all the tables and the watcher
# errors go to zero. The watcher is PINNED UPSTREAM Hermes
# (:data:`VETTED_HERMES_REFS`) and cannot be patched here — the fix is
# hal0-side: guarantee the schema exists before any watcher tick, independent
# of executor registration.
#
# We invoke the HERMES VENV's own ``hermes_cli.kanban_db.init_db`` via
# ``python -c`` rather than replicating ``SCHEMA_SQL`` in hal0 — duplicating
# the schema here would drift against the pin the moment upstream adds a
# column. The convergent pre-check reads ``sqlite_master`` with hal0's own
# stdlib ``sqlite3`` (read-only — safe, no schema knowledge required); only
# the CREATE path goes through hermes's code.

KANBAN_DB_NAME = "kanban.db"

# The table set hermes's own SCHEMA_SQL creates (hermes_cli/kanban_db.py,
# pinned ref 9de9c25f620ff7f1ce0fd5457d596052d5159596 / PyPI 0.18.2 parity).
# Used ONLY as a cheap existence probe for the convergent skip — never as a
# CREATE statement source, so a future upstream migration can add columns or
# tables without hal0 drifting out of sync.
KANBAN_DB_EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "tasks",
        "task_links",
        "task_comments",
        "task_events",
        "task_runs",
        "task_attachments",
        "kanban_notify_subs",
    }
)


def _kanban_db_tables(db_path: Path) -> set[str]:
    """Read ``sqlite_master`` for the table names present at ``db_path``.

    Read-only, hal0's own stdlib ``sqlite3`` — the CHECK may read the file;
    only the CREATE must come from hermes's ``init_db`` (no schema
    duplication against the pin). A missing file, an empty/zero-byte file, or
    any read error is treated as "no tables" so the step falls through to
    invoking hermes's init_db rather than raising out of a provision run.
    """
    if not db_path.exists():
        return set()
    try:
        with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row[0] for row in rows}
    except sqlite3.Error:
        return set()


def _phase_kanban_db_init(ctx: _StepCtx) -> PhaseResult:
    """Guarantee the kanban board DB schema exists, independent of executor
    registration (O20).

    Convergent: when every expected table is already present the step is a
    no-op (``details["changed"]`` False) — the common case once a box has
    either registered the HP-executor or had this step run before. Tolerates
    a hermes-venv-absent fresh/partial install with an honest ``skip`` — this
    step must never fail the bootstrap just because ``install`` hasn't run
    yet (e.g. a preflight-only dry pass, or install itself failing upstream).
    """
    state = ctx.state
    hermes_home = Path(state.hermes_home)
    db_path = hermes_home / KANBAN_DB_NAME
    details: dict[str, Any] = {"db_path": str(db_path)}

    hermes_python = _venv_python(Path(state.venv))
    if not hermes_python.exists():
        details["hermes_python"] = str(hermes_python)
        return PhaseResult(
            status=PhaseStatus.SKIP,
            details=details,
            reason=(
                f"hermes venv absent at {hermes_python} — kanban DB init deferred to the next run"
            ),
        )

    existing = _kanban_db_tables(db_path)
    missing = sorted(KANBAN_DB_EXPECTED_TABLES - existing)
    details["tables_before"] = sorted(existing)
    if not missing:
        details["changed"] = False
        return PhaseResult(status=PhaseStatus.OK, details=details)

    # HERMES_HOME must exist before sqlite creates the file under it —
    # home_init runs earlier in the pipeline, but this step is also callable
    # standalone (tests, --repair re-entry), so make it self-sufficient.
    hermes_home.mkdir(parents=True, exist_ok=True)
    details["missing_before"] = missing

    # HOME-sanitized (O15): the venv python runs as hal0; a leaked HOME=/root
    # would send its own dot-dirs into root's unwritable home. HERMES_HOME is
    # set explicitly, matching every other hermes-venv subprocess call here.
    env = _hal0_subprocess_env(HERMES_HOME=str(hermes_home))
    script = f"from pathlib import Path as _P; from hermes_cli.kanban_db import init_db; init_db(_P({str(db_path)!r}))"
    try:
        ctx.io.run(
            [str(hermes_python), "-c", script],
            check=True,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )  # nosec B603 — argv from local config, no untrusted input
    except (subprocess.SubprocessError, OSError) as exc:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            details=details,
            reason=f"kanban init_db failed: {exc}",
        )

    details["changed"] = True
    return PhaseResult(status=PhaseStatus.OK, details=details)


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


ENV_SNAPSHOT_NAME = "env.json"


def _phase_env_probe(ctx: _StepCtx) -> PhaseResult:
    """Capture a host-environment snapshot for context_link to render from.

    Writes a STABLE ``$HERMES_HOME/env.json`` (content-gated: a byte-identical
    snapshot is left untouched) so a converged re-run reports no change — the
    old ``env-<ts>.json`` timestamped writes mutated the tree on every run.
    """
    snapshot = ctx.io.read_env_probe()
    hermes_home = Path(ctx.state.hermes_home)
    hermes_home.mkdir(parents=True, exist_ok=True)
    snapshot_path = hermes_home / ENV_SNAPSHOT_NAME
    body = json.dumps(snapshot, indent=2, sort_keys=True)
    changed = True
    if snapshot_path.exists():
        try:
            changed = snapshot_path.read_text(encoding="utf-8") != body
        except OSError:
            changed = True
    if changed:
        snapshot_path.write_text(body, encoding="utf-8")
    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "snapshot_path": str(snapshot_path),
            "changed": changed,
            "strix_halo": snapshot["env_report"].get("cpu", {}).get("strix_halo"),
            "gfx": snapshot["gpu_target_version"].get("gfx"),
            "npu_present": snapshot["npu_status"].get("present"),
        },
    )


# ── Phase E: config_write ───────────────────────────────────────────────────


def _resolve_primary_slot(
    *,
    slots_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Pick the live primary chat slot from the local hal0 daemon.

    Reads ``/api/slots`` (the canonical source post-embed migration) and selects
    the entry named ``primary`` (or the first ready ``type=='llm'``
    slot when no name matches). Returns the keys the config template
    needs. Falls back to a safe-but-unwired placeholder when no slot
    is loaded — self_report surfaces that in the bootstrap summary.

    Until v0.2 this read the inference daemon's ``/v1/health`` and looked
    for ``loaded``/``slots`` keys, which post-embed migration are absent
    (the payload uses ``all_models_loaded``). The result was a silent
    fall-through to a placeholder URL on port 8000 — a daemon-less
    address that never wired Hermes to anything real.
    """
    # ``placeholder`` marks the safe-but-unwired fallback so consumers can
    # record it in details["fallbacks"] (#702 fallback observability)
    # instead of inferring it from the model name.
    fallback = {
        "model": "primary",
        "base_url": _DEFAULT_PRIMARY_BACKEND_URL,
        "context_length": 32768,
        "placeholder": True,
    }
    fetch = slots_fetcher or _fetch_slots
    slots = fetch() or []

    def _chat(s: dict[str, Any]) -> bool:
        # `type` is the canonical key post-embed migration (llm/embedding/...);
        # `kind` survives from the pre-migration schema.
        kind = str(s.get("type") or s.get("kind") or "").lower()
        return kind in {"llm", "chat"}

    candidates = [s for s in slots if isinstance(s, dict) and _chat(s)]
    # ADR-0023: the canonical primary is the `agent` slot; accept legacy
    # `chat`/`primary` names for boxes not yet reseeded.
    primary = next((s for s in candidates if s.get("name") in ("agent", "chat", "primary")), None)
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
    # the legacy daemon's native prefix was dropped at the wrapper layer).
    if not base_url or "127.0.0.1:8001" in base_url:
        base_url = f"{HAL0_API_URL}/v1"
    ctx = primary.get("context_length") or primary.get("ctx_size") or fallback["context_length"]
    try:
        ctx = int(ctx)
    except (TypeError, ValueError):
        ctx = fallback["context_length"]
    return {"model": model, "base_url": base_url, "context_length": ctx, "placeholder": False}


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
            # memory_reflect is an agentic LLM loop on the engine side — on the
            # reference box a single reflect legitimately runs 5-9 minutes, so a
            # short per-server timeout guarantees a spurious client-side failure
            # while the engine finishes the job anyway (matches the client-side
            # HAL0_MEMORY_REFLECT_TIMEOUT_S=900 default in memory/hindsight_client).
            "timeout": 900,
            "usage_hint": (
                "read/write persistent context across sessions. Use when the operator "
                "references prior conversations or asks you to remember a fact."
            ),
        },
    ]


def _live_resolve_enabled() -> bool:
    """Single source of truth for live-resolve mode.

    Live-resolve is the hal0 default: ``model.default`` → the virtual
    ``hal0/agent`` and ``providers.custom`` → the hal0-api gateway
    (:8080/v1) with ``discover_models`` + an api_key, so Hermes' model picker
    live-discovers every loaded slot (responsive, auto-updating, context-aware)
    instead of pinning one physical backend. hal0-api always serves
    ``/v1/models`` with the ``hal0/*`` virtuals + ``context_length``, so this is
    correct even before a model is pulled. Set ``HAL0_HERMES_LIVE_RESOLVE=0`` to
    opt back into single-backend pinning.

    BOTH config_write (Phase 5) and model_automap (Phase 9) must read this so
    their renders stay byte-identical (#245 idempotency) — otherwise the
    automap re-render silently clobbers config_write's live-resolve output.
    """
    return os.environ.get("HAL0_HERMES_LIVE_RESOLVE", "1") == "1"


# ── HAL0 config overlay (config-set + targeted YAML merge) ───────────────────
#
# hermes 0.17 owns + migrates its own config.yaml; hal0 layers ONLY its
# integration keys on top instead of rendering the whole file (the old
# Jinja whole-file ownership that forced the ``hermes-agent==0.14.0`` pin —
# issues #240 / #934). Two appliers, split by what hermes's CLI can express:
#
#   * scalars + nested-scalars → hermes's OWN ``config set`` (~0.13s each,
#     idempotent, exercises hermes's schema validation). Even ``mcp_servers``
#     goes here: ``hermes mcp add`` is interactive (prompts "Save anyway?"
#     on a connect probe and hangs with no TTY), so the nested-scalar
#     ``mcp_servers.<name>.{type,url,headers.*,timeout}`` form is the
#     headless-safe path.
#   * the two irreducible LIST values config set can't express (it stores a
#     list as the literal string ``'["a","b"]'``) → a targeted PyYAML
#     deep-merge that preserves every hermes-owned key.

# Static list-valued hal0 keys layered by :func:`_merge_config_yaml_layers`
# (config set stringifies lists, so these can't go through the CLI).
SKILLS_EXTERNAL_DIRS: list[str] = ["/etc/hal0/agent-skills", "/var/lib/hal0/skills"]
SESSION_START_HOOK: dict[str, Any] = {
    "command": "/usr/lib/hal0/hermes-hooks/inject-system-state.sh",
    "timeout": 2,
}
HAL0_CONFIG_LIST_KEYS: dict[str, Any] = {
    "skills": {"external_dirs": SKILLS_EXTERNAL_DIRS},
    "hooks": {"on_session_start": [SESSION_START_HOOK]},
}


def _hermes_bin(venv: Path) -> Path:
    """Path to the ``hermes`` console script in the managed venv."""
    return _venv_python(venv).parent / "hermes"


def _fmt_config_value(value: Any) -> str:
    """Render a value as the positional argv for ``hermes config set``.

    hermes coerces ``true``/``false`` → bool and bare integers → int on the
    way in (verified on 0.17), so we only need the lowercased bool spelling;
    everything else is its ``str()``. NB: lists are intentionally never
    passed here — config set would store them as the literal string.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _build_config_overlay(
    *,
    primary: dict[str, Any],
    chat_slots: list[dict[str, Any]],
    delegation: dict[str, Any] | None,
    auxiliary_tasks: dict[str, dict[str, Any]],
    mcp_servers: list[dict[str, Any]],
    agent_id: str,
    system_prompt: str,
    personality_name: str,
    live_resolve_enabled: bool,
    hermes_home: Path | str = HERMES_HOME_DEFAULT,
) -> list[tuple[str, Any]]:
    """Ordered ``(dotted_key, value)`` overlay applied via ``hermes config set``.

    Mirrors the (now-deleted) ``config.yaml.j2`` — but ONLY the scalar /
    nested-scalar keys. List-valued keys (``skills.external_dirs``,
    ``hooks.on_session_start``) are layered by :func:`_merge_config_yaml_layers`;
    ``model.context_length`` is deliberately omitted (hermes treats it as a
    GLOBAL override that bleeds onto cloud models — per-model context comes
    from live ``/v1/models`` discovery instead).

    Under live-resolve (the hal0 default) ``model.default`` is the virtual
    ``hal0/agent`` against the gateway with ``discover_models`` on, so the
    picker live-discovers every loaded slot. ``HAL0_HERMES_LIVE_RESOLVE=0``
    pins the single physical backend instead.

    ``memory.provider`` is always hermes's built-in ``hal0-memory`` plugin
    (Hindsight is the lone memory engine — §17.7).
    """
    base_url = "http://127.0.0.1:8080/v1" if live_resolve_enabled else primary["backend_url"]
    pairs: list[tuple[str, Any]] = []

    # hooks_auto_accept — the provisioner is also the thing that installs
    # ``hooks.on_session_start`` (SESSION_START_HOOK, below via
    # HAL0_CONFIG_LIST_KEYS): a headless box has no TTY to answer the
    # first-run "approve this hook?" prompt, so without this the hook is
    # silently skipped every session (agent.shell_hooks: "not allowlisted")
    # and system-state injection never happens (#1795 item 2). Same
    # mechanism hermes itself documents as the non-interactive path
    # (--accept-hooks / HERMES_ACCEPT_HOOKS=1 / hooks_auto_accept: true).
    pairs.append(("hooks_auto_accept", True))

    # model.* — the OpenAI-compatible-LAN wiring. ``provider: custom`` is
    # hermes's built-in bucket for Ollama/vLLM/llama.cpp endpoints; hal0 is
    # that endpoint. max_tokens guards the thinking-model silent-TUI (#635).
    # ADR-0023: the canonical default virtual is hal0/agent (was hal0/chat).
    pairs.append(("model.default", "hal0/agent" if live_resolve_enabled else primary["model_id"]))
    pairs += [
        ("model.provider", "custom"),
        ("model.base_url", base_url),
        ("model.max_tokens", 8192),
        ("providers.custom.name", "hal0"),
        ("providers.custom.base_url", base_url),
        ("providers.custom.request_timeout_seconds", 300),
        ("providers.custom.stale_timeout_seconds", 900),
    ]
    if live_resolve_enabled:
        # hermes's picker only runs /v1/models discovery when an api_key is
        # present; the gateway ignores the value (it's unauthenticated).
        #
        # extra_headers is forwarded by hermes onto the discovery GET, so we
        # tag it with X-hal0-Model-Filter: hal0 (#1148) — the gateway then
        # returns ONLY owned_by==hal0 rows (local slots + hal0 virtuals),
        # keeping the ~340 openrouter / minimax passthroughs out of the
        # picker. Parity with pi's client-side owned_by filter; dispatch by
        # explicit remote id is unaffected. Same nested-scalar `config set`
        # mechanism as mcp_servers.<name>.headers.X-hal0-Agent below.
        pairs += [
            ("providers.custom.api_key", "hal0-local"),
            ("providers.custom.discover_models", True),
            ("providers.custom.extra_headers.X-hal0-Model-Filter", "hal0"),
        ]

    for slot in chat_slots:
        alias = slot["alias"]
        pairs += [
            (f"model_aliases.{alias}.model", slot["model_id"]),
            (f"model_aliases.{alias}.provider", "custom"),
            (f"model_aliases.{alias}.base_url", slot["backend_url"]),
        ]

    if delegation:
        # feat/hermes-role-slots (#661): subagents run on the `agent` slot.
        pairs += [
            ("delegation.model", delegation["model"]),
            ("delegation.provider", delegation.get("provider", "custom")),
            ("delegation.base_url", delegation["base_url"]),
        ]

    # memory.graph.* configured hal0's OWN Hindsight
    # graph-extraction engine — hermes never reads it, so forwarding it into
    # hermes's config.yaml was dead config; it is not forwarded.
    pairs += [
        ("memory.provider", "hal0-memory"),
        ("memory.memory_enabled", True),
        ("memory.user_profile_enabled", True),
        ("memory.nudge_interval", 10),
    ]

    # mcp_servers via config set (NOT `hermes mcp add` — interactive/hangs).
    # Agent identity flows via X-hal0-Agent; the `/mcp` mount is ADMIN-classed
    # (security/exposure.py), so once auth is on the box the request also
    # needs a bearer — the SAME box service identity the CLI/steward
    # self-calls use (hal0.service_identity, env → api.env). Resolved once
    # per overlay build so a re-provision picks up a rotated key (rotation
    # itself is NOT live-propagated into this static config.yaml — an
    # operator/`--repair` re-run of provisioning is what re-reads the
    # current key and rewrites the overlay).
    from hal0.service_identity import service_key

    bearer = service_key(prefer="admin")
    for srv in mcp_servers:
        name = srv["name"]
        pairs += [
            (f"mcp_servers.{name}.type", srv.get("type", "http")),
            (f"mcp_servers.{name}.url", srv["url"]),
            (f"mcp_servers.{name}.headers.X-hal0-Agent", agent_id),
            (f"mcp_servers.{name}.timeout", srv.get("timeout", 60)),
        ]
        if bearer:
            pairs.append((f"mcp_servers.{name}.headers.Authorization", f"Bearer {bearer}"))
        if srv.get("private"):
            pairs.append((f"mcp_servers.{name}.headers.X-hal0-Private", "1"))

    pairs.append(("skills.creation_nudge_interval", 15))
    # terminal.cwd is the working directory Hermes' built-in `local` terminal
    # backend spawns shells in (RATIFIED 2026-07-18: moved off /etc/hal0). Under
    # the User=hal0 ProtectSystem=strict unit /etc/hal0 is a read-only, secret-
    # bearing tree — dropping an interactive shell there both fails writes and
    # sits the operator on top of tokens.toml/auth.toml. Point it at a scratch
    # dir under HERMES_HOME (a ReadWritePath, born hal0:hal0, created by
    # home_init) so shells land in a writable, secret-free sandbox. The backend
    # stays `local` (decided) — only the cwd moves.
    terminal_scratch = str(Path(hermes_home) / "scratch")
    pairs += [("terminal.backend", "local"), ("terminal.cwd", terminal_scratch)]
    pairs += [("agent.max_turns", 60), ("agent.reasoning_effort", "medium")]
    if system_prompt:
        pairs.append(("agent.system_prompt_prelude", system_prompt))

    # Reasoning visibility: both flags are required together (#635).
    pairs += [
        ("display.bell_on_complete", False),
        ("display.streaming", True),
        ("display.show_reasoning", True),
    ]
    if personality_name:
        pairs.append(("display.personality", personality_name))

    # auxiliary.<task> side-job routing (feat/hermes-role-slots): utility-slot
    # tasks render provider:"custom"+base_url; vision/web_extract stay "main".
    for task, cfg in auxiliary_tasks.items():
        pairs += [
            (f"auxiliary.{task}.provider", cfg.get("provider", "main")),
            (f"auxiliary.{task}.model", cfg.get("model", "")),
        ]
        if cfg.get("base_url"):
            pairs.append((f"auxiliary.{task}.base_url", cfg["base_url"]))

    return pairs


def _ensure_hermes_config(hermes_bin: Path, hermes_home: Path, run: Callable[..., Any]) -> bool:
    """Run ``hermes config migrate`` so hermes owns + schema-migrates the file.

    Non-fatal: a migrate hiccup is logged but the subsequent ``config set``
    calls still create/populate the file. Returns whether migrate succeeded.
    """
    # HOME-sanitized (O15): hermes-cli runs as hal0; a leaked HOME=/root would
    # send its own dot-dirs into root's unwritable home. HERMES_HOME is set
    # explicitly so config lands in the managed tree regardless.
    env = _hal0_subprocess_env(HERMES_HOME=str(hermes_home))
    try:
        run(
            [str(hermes_bin), "config", "migrate"],
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )  # nosec B603 — argv from local config
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("hermes_provision.config_migrate_failed", error=str(exc))
        return False


def _apply_config_set(
    pairs: list[tuple[str, Any]],
    *,
    hermes_bin: Path,
    hermes_home: Path,
    run: Callable[..., Any],
) -> tuple[int, list[str]]:
    """Apply each ``(key, value)`` via ``hermes config set``.

    Returns ``(applied_count, errors)``. Each set is idempotent (writes the
    same value on a re-run), so the whole overlay is safe under ``--repair``.
    """
    env = _hal0_subprocess_env(HERMES_HOME=str(hermes_home))  # HOME-sanitized (O15)
    applied = 0
    errors: list[str] = []
    for key, value in pairs:
        try:
            run(
                [str(hermes_bin), "config", "set", key, _fmt_config_value(value)],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )  # nosec B603 — argv from local config
            applied += 1
        except (subprocess.SubprocessError, OSError) as exc:
            errors.append(f"{key}: {exc}")
    return applied, errors


def _merge_config_yaml_layers(
    config_path: Path,
    *,
    list_keys: dict[str, Any],
    overrides_path: Path,
) -> bool:
    """Deep-merge the irreducible list keys + operator overrides onto config.yaml.

    ``config set`` can't express YAML sequences (it stringifies them), so the
    list-valued hal0 keys (``skills.external_dirs``, ``hooks.on_session_start``)
    are layered here. The operator escape hatch (``overrides.yaml``) deep-merges
    on top LAST so a hand override still wins. Every hermes-owned key is
    preserved (merge, never clobber). Returns True iff the file changed.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return False  # PyYAML absent — list keys can't be layered; ship as-is.
    base: dict[str, Any] = {}
    if config_path.exists():
        base = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    merged = _deep_merge(base, list_keys)
    if overrides_path.exists():
        overlay = yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}
        merged = _deep_merge(merged, overlay)
    out = yaml.safe_dump(merged, sort_keys=False, default_flow_style=False)
    if config_path.exists() and config_path.read_text(encoding="utf-8") == out:
        return False
    _atomic_write(config_path, out)
    return True


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge — overlay wins; nested dicts merge."""
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


OVERRIDES_PATH = Path("/etc/hal0/agents/hermes/overrides.yaml")


# ── Legacy Honcho cleanup ───────────────────────────────────────────────────
#
# Honcho was removed as a memory provider (§17.7 — Hindsight is the lone
# memory engine). _disable_honcho_hermes_host survives as cleanup: a box
# provisioned while Honcho routing existed may still carry an enabled
# $HERMES_HOME/honcho.json that hermes's BUILT-IN honcho provider would
# silently autoenable. Every provision run disables it in place (never
# deletes — an operator's hand-made file is preserved, just disabled).


def _load_hal0_config() -> Any:
    """Load hal0.toml (late import keeps this module importable standalone)."""
    from hal0.config.loader import load_hal0_config

    return load_hal0_config()


def _disable_honcho_hermes_host(hermes_home: Path) -> bool:
    """Flip ``hosts.hermes.enabled`` to False in an existing ``honcho.json``.

    Called when the agent is routed to hindsight so a stale ``honcho.json``
    (left over from a prior honcho run, or an operator's hand-made
    Honcho-cloud setup) doesn't silently autoenable hermes's built-in honcho
    provider alongside hal0-memory. Never CREATES the file — a no-op when
    honcho.json is absent — and preserves every other key untouched.
    """
    path = hermes_home / "honcho.json"
    if not path.is_file():
        return False
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(loaded, dict):
        return False
    hosts = loaded.get("hosts")
    if not isinstance(hosts, dict) or not isinstance(hosts.get("hermes"), dict):
        return False
    if hosts["hermes"].get("enabled") is False:
        return False
    hosts["hermes"]["enabled"] = False
    out = json.dumps(loaded, indent=2, sort_keys=False) + "\n"
    _atomic_write(path, out)
    return True


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


def _phase_config_write(ctx: _StepCtx) -> PhaseResult:
    """Apply hal0's integration overlay onto the hermes-owned ``config.yaml``.

    Config-set redesign (replaces the old whole-file Jinja render that forced
    the ``hermes-agent==0.14.0`` pin): hermes owns + migrates ``config.yaml``;
    hal0 layers ONLY its keys on top.

      1. ``hermes config migrate`` — hermes creates/schema-migrates its file.
      2. Scalar/nested-scalar overlay (model wiring, providers.custom, memory,
         model_aliases, delegation, mcp_servers, persona prelude, auxiliary,
         …) applied via ``hermes config set`` — idempotent, headless-safe.
      3. The two irreducible LIST keys (``skills.external_dirs``,
         ``hooks.on_session_start``) + operator ``overrides.yaml`` deep-merged
         in via PyYAML, preserving every hermes-owned key.

    Idempotent under ``--repair``: every ``config set`` re-writes the same
    value and the YAML merge is a no-op when the file already matches.
    """
    state = ctx.state
    hermes_home = Path(state.hermes_home)
    config_path = hermes_home / "config.yaml"
    hermes_bin = _hermes_bin(Path(state.venv))
    run = ctx.io.run

    hermes_home.mkdir(parents=True, exist_ok=True)
    # Content snapshot BEFORE any mutation, so we can report whether the overlay
    # actually changed the file (the convergence signal). Also rolled to a single
    # ``config.yaml.bak`` so a repair-revert of hand-edits is recoverable.
    config_before = None
    if config_path.is_file():
        with contextlib.suppress(OSError):
            config_before = config_path.read_text(encoding="utf-8")
        with contextlib.suppress(OSError):
            shutil.copy2(config_path, config_path.with_name("config.yaml.bak"))
    migrated = _ensure_hermes_config(hermes_bin, hermes_home, run)

    primary_raw = _resolve_primary_slot(slots_fetcher=ctx.io.fetch_slots)
    # _resolve_primary_slot returns ``model``/``base_url``; the overlay builder
    # wants ``model_id``/``backend_url``. Translate at the seam.
    primary = {
        "model_id": primary_raw["model"],
        "backend_url": primary_raw["base_url"],
        "context_length": primary_raw["context_length"],
    }
    slots_all = ctx.io.fetch_slots()
    chat_slots = _collect_chat_slots(slots_all, contexts=ctx.io.fetch_model_contexts())
    # feat/hermes-role-slots: delegation ← `agent` slot; auxiliary ← `utility`
    # slot. Both hit hal0's /v1 endpoint, so a missing slot degrades safely
    # (delegation omitted / aux → "main").
    hal0_v1_base = primary["backend_url"]
    delegation = _resolve_delegation(slots_all, hal0_base_url=hal0_v1_base)
    auxiliary_tasks = _resolve_auxiliary_tasks(slots_all, hal0_base_url=hal0_v1_base)
    # Probe-driven mcp_servers: mcp_wire runs immediately BEFORE us in the linear
    # pipeline, so its live probe result is available via output_of. Fall back to
    # the builtin inventory when the probe found nothing (degraded MCP layer).
    probed_servers = ctx.output_of("mcp_wire").get("rendered_servers")
    have_probed = isinstance(probed_servers, list) and bool(probed_servers)
    mcp_servers = probed_servers if have_probed else _default_mcp_servers()
    # Silent fallbacks stay observable.
    fallbacks: list[dict[str, str]] = []
    if primary_raw.get("placeholder"):
        fallbacks.append(
            {
                "site": "primary_slot",
                "detail": (
                    "no ready llm slot — overlay points model.default at the "
                    "hal0/agent virtual against the gateway"
                ),
            }
        )
    if not have_probed:
        fallbacks.append(
            {
                "site": "mcp_servers",
                "detail": (
                    "no probed rendered_servers checkpoint from mcp_wire — "
                    "applied the default builtin MCP inventory"
                ),
            }
        )
    system_prompt, personality_name = _active_persona_render(state, mcp_servers=mcp_servers)
    live_resolve_enabled = _live_resolve_enabled()

    pairs = _build_config_overlay(
        primary=primary,
        chat_slots=chat_slots,
        delegation=delegation,
        auxiliary_tasks=auxiliary_tasks,
        mcp_servers=mcp_servers,
        agent_id=state.agent_id,
        system_prompt=system_prompt,
        personality_name=personality_name,
        live_resolve_enabled=live_resolve_enabled,
        hermes_home=hermes_home,
    )
    applied, errors = _apply_config_set(
        pairs, hermes_bin=hermes_bin, hermes_home=hermes_home, run=run
    )
    list_merge_changed = _merge_config_yaml_layers(
        config_path, list_keys=HAL0_CONFIG_LIST_KEYS, overrides_path=OVERRIDES_PATH
    )

    # Legacy honcho.json cleanup — see _disable_honcho_hermes_host.
    honcho_json_changed = _disable_honcho_hermes_host(hermes_home)

    config_after = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    new_hash = content_hash(config_after) if config_after is not None else None
    # Convergence signal: the overlay changed the file content, a list key was
    # merged, or a stale pre-removal honcho.json was disabled. A no-drift
    # re-run is byte-identical.
    changed = bool((config_before != config_after) or list_merge_changed or honcho_json_changed)
    # Non-fatal posture (run-all): a partial set still leaves a usable config.
    # FAIL only when the overlay couldn't apply at all (hermes_bin broken) —
    # that's a real, actionable stop the operator must see.
    status = PhaseStatus.OK if (applied or not pairs) else PhaseStatus.FAIL
    return PhaseResult(
        status=status,
        hash=new_hash,
        reason=("; ".join(errors[:3]) if status == PhaseStatus.FAIL else None),
        details={
            "config_path": str(config_path),
            "changed": changed,
            "keys_applied": applied,
            "keys_total": len(pairs),
            "list_merge_changed": list_merge_changed,
            "config_migrated": migrated,
            "primary_model": primary["model_id"],
            "chat_slot_count": len(chat_slots),
            "persona": personality_name or None,
            "mcp_server_count": len(mcp_servers),
            "delegation_model": (delegation or {}).get("model"),
            "auxiliary_utility_model": _utility_aux_model(auxiliary_tasks),
            "memory_provider": "hal0-memory",
            "honcho_json_changed": honcho_json_changed,
            "config_set_errors": errors,
            "fallbacks": fallbacks,
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
# phases (#243 namespace_register, #245 model_automap). Honors the
# per-agent allow-list at /etc/hal0/agents/hermes.toml, which gates which
# servers the bootstrap will attempt to connect to.


AGENT_ALLOWLIST_PATH = Path("/etc/hal0/agents/hermes.toml")


def _load_agent_allowlist(
    path: Path | None = None,
) -> dict[str, dict[str, Any]] | None:
    """Read ``[mcp.servers.*]`` blocks from the per-agent allow-list.

    Returns ``None`` when the file is missing (the agent installer
    drops it during install; absence means "allow everything that's
    builtin" per the installer-managed convention). Returns
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

    The ``/mcp`` mount is ADMIN-classed (security/exposure.py), so once
    auth is on the box this probe needs a bearer too, same as the
    provisioned config it's validating — resolved fresh on every call from
    the box service identity (:func:`hal0.service_identity.service_key`),
    so a mid-install key rotation is picked up on the very next probe.
    """
    import contextlib
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    from hal0.service_identity import service_key

    transport_url = url.rstrip("/") + "/mcp"
    base_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-hal0-Agent": agent_id,
    }
    bearer = service_key(prefer="admin")
    if bearer:
        base_headers["Authorization"] = f"Bearer {bearer}"
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


def _phase_mcp_wire(ctx: _StepCtx) -> PhaseResult:
    """Verify the two hal0-bundled MCP servers respond + record their tool list.

    When an allow-list exists at
    ``/etc/hal0/agents/hermes.toml``, the bootstrap only attempts
    connection for servers listed under ``[mcp.servers.*]``. A
    missing entry (or a missing allow-list file entirely) is a
    warning, NOT a hard fail — bootstrap continues so the operator
    can wire the missing piece by hand after install.
    """
    state = ctx.state
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
        # _probe_mcp_server's contract wants the MOUNT ROOT — it appends
        # "/mcp" itself (see its docstring + _smoke_admin_tools_list, which
        # both pass ".../mcp/admin"). entry["url"] is instead the FULL
        # transport URL (".../mcp/admin/mcp" — correct as-is for
        # config.yaml's mcp_servers.<name>.url), so passing it through
        # unstripped double-appends "/mcp" and 404s every probe,
        # unconditionally, on every bootstrap run. Strip the one trailing
        # "/mcp" _default_mcp_servers() always adds before probing.
        probe = ctx.io.probe_mcp_server(
            entry["url"].removesuffix("/mcp"), agent_id=state.agent_id, private=entry["private"]
        )
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
    # bootstrap blocker (per the plan §9 contract).
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
# RELOCATE(brain-lane) — LANDED: no longer in ``_INSTALL_STEPS``. The
# hal0-api boot lifespan's ``_boot_seeds`` phase now covers this (it already
# called ``seed_default_personas`` directly, before this function existed as
# an install step — this function is kept for ``--repair``-style direct
# calls and its existing unit coverage).
#
# Seeds the operator-visible personas hal0 manages on top of Hermes's own
# personality slot. Two personas land on first install — ``hermes``
# (default, helpful) and ``hal0-brain`` (the dashboard agent chat's
# platform steward). Operator edits survive re-runs; ``--repair``
# re-writes the seeds back to their canonical content. The active pointer
# flips to ``hermes`` only when missing or dangling — an operator-chosen
# active persona survives re-seed.


def _phase_persona_seed(ctx: _StepCtx) -> PhaseResult:
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

    state = ctx.state
    root = _personas_root_for(state)
    # Honor ``--repair`` by forcing seed overwrite. Operator edits stand
    # in the steady-state case; repair explicitly resets to known-good.
    overwrite = ctx.repair
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


CONTEXT_TEMPLATE_DIR = Path(__file__).resolve().parent / "hermes_templates"
HAL0_BUNDLED_SKILLS = Path("/usr/share/hal0/skills")
ETC_HAL0_DIR = Path("/etc/hal0")
ETC_HAL0_AGENT_SKILLS = ETC_HAL0_DIR / "agent-skills"

# STATE.md AND HERMES.md — the live files rewritten on every restart / model
# swap — live under the hal0-owned /var/lib/hal0 rather than the root-owned
# /etc/hal0 (#473): the hermes unit runs User=hal0 with ProtectSystem=strict,
# AND the runtime re-render is spawned detached from hal0-api (whose sandbox
# also makes /etc/hal0 read-only), so the writer can only touch ReadWritePaths
# — /var/lib/hal0 is in them, /etc/hal0 is not. HERMES.md is cwd-injected from
# /etc/hal0, so /etc/hal0/HERMES.md is kept as a symlink to the /var/lib copy
# (provision, running as root, maintains the link). The rest of the config
# (AGENTS.md, MCP-CLIENTS.md, seeds) stays under ETC_HAL0_DIR, written once at
# provision time as root.
RUNTIME_SNAPSHOT_DIR = Path("/var/lib/hal0")


def _latest_env_snapshot(hermes_home: Path) -> dict[str, Any]:
    """Load the env snapshot env_probe wrote (stable ``env.json``, or a legacy
    ``env-<ts>.json`` from an older install).

    Falls back to empty dict when no snapshot exists — templates use
    Jinja2 ``default`` filters so partial data is OK.
    """
    stable = hermes_home / ENV_SNAPSHOT_NAME
    candidates = [stable] if stable.exists() else sorted(hermes_home.glob("env-*.json"))
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


def _atomic_write_if_changed(path: Path, content: str) -> tuple[str, bool]:
    """Atomic write that skips a byte-identical file. Returns ``(sha256, wrote)``.

    The convergence signal for the rendered context files: a re-render that
    produces the same bytes leaves the file (and its mtime) untouched.
    """
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == content:
                return content_hash(content), False
        except OSError:
            pass
    return _atomic_write(path, content), True


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


def _relink_managed(target: Path, link: Path) -> bool:
    """Force ``link`` to be a symlink -> ``target``, atomically replacing a
    stale regular file or a wrong symlink. Returns True when a (re)link
    happened, False when ``link`` already points at ``target``.

    Unlike :func:`_safe_symlink`, this DOES clobber a pre-existing regular
    file — it's only used for hal0-MANAGED paths (HERMES.md) where the file
    is ours. It exists to migrate the pre-relocation layout, where HERMES.md
    was a real file under /etc/hal0, to the new symlink -> /var/lib/hal0 form
    in place (so the read path /etc/hal0/HERMES.md is unchanged for consumers).
    """
    if link.is_symlink():
        try:
            if os.readlink(link) == str(target):
                return False
        except OSError:
            pass
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_suffix(link.suffix + ".lnktmp")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(str(target), str(tmp))
    os.replace(tmp, link)  # atomic over an existing file OR symlink
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


def _phase_context_link(ctx: _StepCtx) -> PhaseResult:
    """Render persona + context files; mirror bundled skills.

    Files rendered (atomically):
      - $HERMES_HOME/SOUL.md
      - /var/lib/hal0/HERMES.md (read via the /etc/hal0/HERMES.md symlink)
      - /etc/hal0/AGENTS.md
      - $HERMES_HOME/memories/HOST.md (symlink -> /etc/hal0/HERMES.md)

    SOUL.md render failure falls back to a minimal hal0-themed default
    (we can't import upstream's DEFAULT_SOUL_MD from inside hal0; the
    fallback is short + accurate). HERMES.md + AGENTS.md render
    failures log + skip per #244 sharpening.
    """
    state = ctx.state
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
    slots_all = ctx.io.fetch_slots()
    chat_slots = _collect_chat_slots(slots_all, contexts=ctx.io.fetch_model_contexts())
    primary_raw = _resolve_primary_slot(slots_fetcher=lambda: slots_all)
    primary_for_template: dict[str, Any] | None = None
    primary_alias = "agent"  # ADR-0023 canonical default anchor
    primary_slot = next(
        (
            s
            for s in slots_all
            if isinstance(s, dict) and s.get("name") in ("agent", "chat", "primary")
        ),
        None,
    )
    if primary_slot:
        primary_alias = _slot_alias(primary_slot)
    # primary_raw["model"] is a real model_id when a slot is live, or
    # the placeholder string (slot name) when nothing is loaded — treat
    # the placeholder as "no primary" for template purposes.
    if primary_raw["model"] and primary_raw["model"] not in ("agent", "utility", "chat", "primary"):
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
        "dashboard_url": os.environ.get(
            "HAL0_DASHBOARD_URL",
            os.environ.get("HAL0_API_URL", "http://hal0.local:8080").rstrip("/"),
        ),
    }

    rendered: dict[str, str] = {}
    warnings: list[str] = []
    fallbacks: list[dict[str, str]] = []
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
        # #702: the inline-default fallback is observable, not silent.
        fallbacks.append(
            {
                "site": "soul_md",
                "detail": f"SOUL.md.j2 render failed ({exc}) — wrote the inline default SOUL.md",
            }
        )
        rendered["SOUL.md"] = fallback_soul

    for tpl_name, _out_name in (
        ("AGENTS.md.j2", "AGENTS.md"),
        ("MCP-CLIENTS.md.j2", "MCP-CLIENTS.md"),
    ):
        try:
            rendered[_out_name] = _render_template(tpl_name, **vars_)
        except Exception as exc:
            warnings.append(f"{tpl_name} render: {exc}; skipping")

    details: dict[str, Any] = {
        "warnings": warnings,
        "rendered": {},
        "links": [],
        "fallbacks": fallbacks,
    }

    changed = False
    soul_path = hermes_home / "SOUL.md"
    h, wrote = _atomic_write_if_changed(soul_path, rendered["SOUL.md"])
    changed = changed or wrote
    details["rendered"]["SOUL.md"] = {"path": str(soul_path), "sha256": h}

    # STATE.md + HERMES.md are the live files — render via the one shared
    # path used by the per-restart / per-swap writers. Best-effort: failure
    # here must not fail bootstrap (SOUL/AGENTS already written).
    try:
        # NB: render_live_context re-fetches /api/slots + /v1/models itself
        # (separate from the vars_ fetch above). Acceptable at bootstrap
        # frequency; keeps it usable standalone from the restart/swap writers.
        live = render_live_context(
            hermes_home=hermes_home,
            slots_fetcher=ctx.io.fetch_slots,
            contexts_fetcher=ctx.io.fetch_model_contexts,
            health_probe=ctx.io.http_get,
        )
        changed = changed or bool(live.get("state_written") or live.get("hermes_written"))
        details["rendered"]["STATE.md"] = {"path": live["state_path"]}
        # render_live_context writes the REAL HERMES.md under the hal0-owned
        # RUNTIME_SNAPSHOT_DIR (so the User=hal0 runtime re-render isn't blocked
        # by /etc/hal0 being read-only in the hal0-api spawn sandbox). Provision
        # runs as root, so it (re)establishes /etc/hal0/HERMES.md as a symlink to
        # that file — keeping the stable read path consumers (Hermes cwd-inject,
        # the HOST.md mirror, the smoke test) already use. Migrates an older
        # install where /etc/hal0/HERMES.md was a real file.
        etc_hermes = ETC_HAL0_DIR / "HERMES.md"
        runtime_hermes = Path(live.get("hermes_path") or (RUNTIME_SNAPSHOT_DIR / "HERMES.md"))
        details["rendered"]["HERMES.md"] = {
            "path": str(etc_hermes),
            "written": live["hermes_written"],
        }
        if live["degraded"]:
            warnings.append("STATE.md rendered with daemon degraded")
        if live.get("hermes_error"):
            warnings.append(f"HERMES.md render: {live['hermes_error']}")
        try:
            # Skip when the two resolve to the same file (e.g. tests that point
            # both dirs at one tmp_path) — can't symlink a file onto itself.
            same_file = runtime_hermes.exists() and etc_hermes.resolve() == runtime_hermes.resolve()
            if (
                not same_file
                and runtime_hermes.exists()
                and _relink_managed(runtime_hermes, etc_hermes)
            ):
                details["links"].append(f"{etc_hermes} -> {runtime_hermes}")
        except OSError as exc:
            warnings.append(f"HERMES.md /etc symlink: {exc}")
        # The memory tier reads $HERMES_HOME/memories/HOST.md; mirror it to the
        # stable /etc/hal0/HERMES.md read path (which now follows the symlink).
        if etc_hermes.exists():
            host_md = hermes_home / "memories" / "HOST.md"
            if _safe_symlink(etc_hermes, host_md):
                details["links"].append(str(host_md))
    except Exception as exc:  # best-effort
        warnings.append(f"render_live_context: {exc}")

    if "AGENTS.md" in rendered:
        try:
            ETC_HAL0_DIR.mkdir(parents=True, exist_ok=True)
            apath = ETC_HAL0_DIR / "AGENTS.md"
            h, wrote = _atomic_write_if_changed(apath, rendered["AGENTS.md"])
            changed = changed or wrote
            details["rendered"]["AGENTS.md"] = {"path": str(apath), "sha256": h}
        except OSError as exc:
            warnings.append(f"AGENTS.md write to /etc/hal0: {exc}")

    if "MCP-CLIENTS.md" in rendered:
        try:
            ETC_HAL0_DIR.mkdir(parents=True, exist_ok=True)
            mcppath = ETC_HAL0_DIR / "MCP-CLIENTS.md"
            h, wrote = _atomic_write_if_changed(mcppath, rendered["MCP-CLIENTS.md"])
            changed = changed or wrote
            details["rendered"]["MCP-CLIENTS.md"] = {"path": str(mcppath), "sha256": h}
        except OSError as exc:
            warnings.append(f"MCP-CLIENTS.md write to /etc/hal0: {exc}")

    # Mirror bundled skills last so a failure here doesn't block context files.
    linked, skill_warnings = _mirror_bundled_skills(HAL0_BUNDLED_SKILLS, ETC_HAL0_AGENT_SKILLS)
    details["bundled_skills_linked"] = linked
    warnings.extend(skill_warnings)
    details["warnings"] = warnings
    details["changed"] = changed or bool(details["links"]) or bool(linked)

    return PhaseResult(status=PhaseStatus.OK, details=details)


# ── Phase H: namespace_register ─────────────────────────────────────────────
#
# Writes the Hermes-Agent identity card to the `agents` memory dataset.
# Card is immutable post-write — re-bootstrap deletes the
# existing card and writes a fresh one to refresh metadata (the only
# legitimate post-install write). On hal0-memory failure, log + continue
# (per #243 sharpening); the card is nice-to-have and
# bootstrap MUST NOT fail because the peer registry is down.


AGENT_IDENTITY_TAG = "agent-identity"
AGENTS_DATASET = "agents"


def _hermes_version_pin() -> str:
    """Best-effort human identifier for the pinned hermes-agent build.

    Surfaced in the identity card + self-report. The requirement may be an exact
    ``==X.Y.Z`` spec, a floored/capped range, or a pin to a reviewed upstream git
    commit/tag (the production posture) — none of the latter is a PEP 440
    version, so this returns whatever pin identifier is present rather than
    crashing on a commit ref. A 40-char SHA is shortened for display; a tag ref
    (e.g. ``v2026.7.7.2``) is returned verbatim. Falls back to ``"unknown"`` when
    the file is unreadable or carries no recognizable hermes-agent line.
    """
    req = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "requirements.txt"
    try:
        text = req.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    try:
        line = _hermes_requirement_line(text)
    except ValueError:
        return "unknown"
    exact = re.search(r"==\s*([0-9][^\s#,]*)", line)
    if exact:
        return exact.group(1)
    ref = hermes_pinned_ref(line)
    if ref is not None:
        return ref[:12] if _HERMES_SHA_RE.fullmatch(ref) else ref
    floor = hermes_requirement_floor(line)
    if floor is not None:
        return ">=" + ".".join(str(p) for p in floor)
    return "unknown"


def _hal0_version_string() -> str:
    try:
        from hal0 import __version__

        return __version__
    except (ImportError, AttributeError):
        return "unknown"


def _build_identity_card(state: BootstrapState) -> dict[str, Any]:
    """Schema v1. Text + structured metadata."""
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
                "accepts_tasks_from": ["claude-code", "user"],
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

    The ``/api/memory`` prefix is ADMIN-classed (security/exposure.py), so
    once auth is on the box this call also needs a bearer — same box
    service identity as the ``/mcp`` sites (:func:`hal0.service_identity.
    service_key`), resolved fresh on every call so a mid-install key
    rotation is picked up on the very next call.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    from hal0.service_identity import service_key

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
    bearer = service_key(prefer="admin")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
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


def _phase_namespace_register(ctx: _StepCtx) -> PhaseResult:
    """Write the Hermes identity card to the `agents` memory dataset.

    RELOCATE(brain-lane) — LANDED: no longer in ``_INSTALL_STEPS``. Called
    from the hal0-api boot lifespan's terminal ``_boot_brain_lane`` phase
    instead, with ``ctx.io.mcp_memory_call`` swapped for an in-process
    boot-safe adapter (the HTTP-loopback ``_mcp_memory_call`` default only
    works once uvicorn's socket is bound, which is never true during
    lifespan startup). This function's body is unchanged and reused as-is.

    Idempotency: search for an existing card by ``agent_id`` first;
    if present, delete it before writing the fresh one (cards are
    immutable, but bootstrap rewrites refresh the
    snapshot of hal0_version + hermes_version).

    Failure mode: any MCP transport error logs + returns OK with a
    warning. Bootstrap MUST NOT block on registry unavailability.
    """
    state = ctx.state
    card = _build_identity_card(state)
    warnings: list[str] = []
    # #702: every memory-layer warn-as-OK degradation is recorded here so
    # the fallback posture is observable in provision.json, not silent.
    fallbacks: list[dict[str, str]] = []

    # Look up existing card so re-bootstrap doesn't accumulate duplicates.
    search = ctx.io.mcp_memory_call(
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
        fallbacks.append(
            {
                "site": "memory_layer",
                "detail": f"memory_search failed ({search['error']}) — continuing without dedupe",
            }
        )

    if existing_ids:
        deleted = ctx.io.mcp_memory_call(
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
            fallbacks.append(
                {
                    "site": "memory_layer",
                    "detail": f"memory_delete failed ({deleted['error']}) — card not rewritten",
                }
            )
            return PhaseResult(
                status=PhaseStatus.OK,
                details={
                    "registered": False,
                    "refreshed_existing": False,
                    "warnings": warnings,
                    "card": card,
                    "fallbacks": fallbacks,
                },
                reason="memory_delete failed; not rewriting to avoid duplicate accumulation",
            )
        removed = (deleted.get("result") or {}).get("deleted", 0)
        if removed != len(existing_ids):
            warnings.append(
                f"memory_delete: requested {len(existing_ids)}, removed {removed} "
                "— not rewriting to avoid duplicate accumulation"
            )
            fallbacks.append(
                {
                    "site": "memory_layer",
                    "detail": (
                        f"memory_delete removed {removed}/{len(existing_ids)} — "
                        "card not rewritten to avoid duplicate accumulation"
                    ),
                }
            )
            return PhaseResult(
                status=PhaseStatus.OK,
                details={
                    "registered": False,
                    "refreshed_existing": False,
                    "warnings": warnings,
                    "card": card,
                    "fallbacks": fallbacks,
                },
                reason="memory_delete count mismatch; not rewriting to avoid duplicate accumulation",
            )

    add = ctx.io.mcp_memory_call(
        "tools/call",
        {"name": "memory_add", "arguments": card},
        agent_id=state.agent_id,
    )
    if not add["ok"]:
        # Bootstrap continues — the card is nice-to-have, not a blocker.
        warnings.append(f"memory_add: {add['error']}")
        fallbacks.append(
            {
                "site": "memory_layer",
                "detail": f"memory_add failed ({add['error']}) — identity card not registered",
            }
        )
        return PhaseResult(
            status=PhaseStatus.OK,
            details={
                "registered": False,
                "warnings": warnings,
                "card": card,
                "fallbacks": fallbacks,
            },
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
            "fallbacks": fallbacks,
        },
    )


# ── Phase H.6: brain_profile_seed ───────────────────────────────────────────
#
# RELOCATE(brain-lane) — LANDED: no longer in ``_INSTALL_STEPS``. Called
# from the hal0-api boot lifespan's terminal ``_boot_brain_lane`` phase
# (same in-process ``mcp_memory_call`` substitution as namespace_register).
#
# ``hal0-brain`` ships both as a hal0 persona (personas.py) AND as a
# first-class memory *profile* agent-id (``hermes__hal0-brain``). Prior to
# this phase it was only a persona: its bank was never provisioned and it was
# never registered as an agent identity, so it was a second-class citizen next
# to the default ``hermes`` agent. This phase registers its identity card in
# the ``agents`` dataset — the same treatment ``_phase_namespace_register``
# gives the default agent — so the steward is a real profile, discoverable and
# with a provisioned private bank (``private:hermes__hal0-brain``). Warn-as-OK:
# memory-layer unavailability never blocks bootstrap.


def _build_brain_identity_card() -> dict[str, Any]:
    """Identity card for the hal0-brain profile (schema v1).

    Mirrors :func:`_build_identity_card` (the default agent's card) so the
    dashboard steward registers as a first-class agent identity rather than a
    mere persona overlay. Its memory rides ``private:hermes__hal0-brain``.
    """
    from hal0.agents.personas import BRAIN_PROFILE_AGENT_ID

    return {
        "text": (
            "I am hal0-brain, the resident platform steward of this hal0 home AI box, "
            "embedded in the dashboard's agent chat. I administer the instance itself: "
            "inference slots, the model library, benchmarks, hardware headroom, the "
            "Operator Board, and orchestration settings."
        ),
        "tags": [AGENT_IDENTITY_TAG, "hal0-brain"],
        "dataset": AGENTS_DATASET,
        "metadata": {
            "agent_id": BRAIN_PROFILE_AGENT_ID,
            "display_name": "hal0 Brain (platform steward)",
            "namespace": f"private:{BRAIN_PROFILE_AGENT_ID}",
            "roles": ["platform-steward", "dashboard-agent-chat"],
            "hal0_state": {
                "registered_at": _utcnow(),
                "bootstrap_version": 1,
                "hal0_version": _hal0_version_string(),
                "hermes_version": _hermes_version_pin(),
            },
        },
    }


def _phase_brain_profile_seed(ctx: _StepCtx) -> PhaseResult:
    """Register the hal0-brain profile as a first-class agent identity.

    Writes the brain identity card to the ``agents`` dataset (search → delete
    stale → add), keyed on the ``hermes__hal0-brain`` agent-id so its private
    bank is provisioned rather than left to lazy first-write. Idempotent;
    warn-as-OK so bootstrap never blocks on the memory layer.
    """
    from hal0.agents.personas import BRAIN_PROFILE_AGENT_ID

    card = _build_brain_identity_card()
    warnings: list[str] = []
    fallbacks: list[dict[str, str]] = []

    search = ctx.io.mcp_memory_call(
        "tools/call",
        {
            "name": "memory_search",
            "arguments": {
                "query": BRAIN_PROFILE_AGENT_ID,
                "tags": [AGENT_IDENTITY_TAG],
                "dataset": AGENTS_DATASET,
                "limit": 50,
            },
        },
        agent_id=BRAIN_PROFILE_AGENT_ID,
    )
    existing_ids: list[str] = []
    if search["ok"] and isinstance(search["result"], dict):
        items = search["result"].get("items") or []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            md = item.get("metadata") or {}
            if md.get("agent_id") == BRAIN_PROFILE_AGENT_ID and item.get("id"):
                existing_ids.append(item["id"])
    elif not search["ok"]:
        warnings.append(f"memory_search: {search['error']}")
        fallbacks.append(
            {
                "site": "memory_layer",
                "detail": f"memory_search failed ({search['error']}) — continuing without dedupe",
            }
        )

    if existing_ids:
        deleted = ctx.io.mcp_memory_call(
            "tools/call",
            {"name": "memory_delete", "arguments": {"ids": existing_ids}},
            agent_id=BRAIN_PROFILE_AGENT_ID,
        )
        removed = (deleted.get("result") or {}).get("deleted", 0)
        # Same guard as namespace_register: a 200 that pruned fewer ids than
        # asked (the #446 custom-dataset skip) must not trigger a rewrite, or
        # the Peer view floods with duplicate cards.
        if not deleted["ok"] or removed != len(existing_ids):
            detail = (
                deleted["error"] if not deleted["ok"] else f"removed {removed}/{len(existing_ids)}"
            )
            warnings.append(f"memory_delete: {detail} — brain card not rewritten")
            fallbacks.append(
                {
                    "site": "memory_layer",
                    "detail": f"memory_delete {detail} — brain card not rewritten to avoid duplicates",
                }
            )
            return PhaseResult(
                status=PhaseStatus.OK,
                details={
                    "registered": False,
                    "refreshed_existing": False,
                    "card": card,
                    "warnings": warnings,
                    "fallbacks": fallbacks,
                },
                reason="memory_delete failed/short; not rewriting brain card",
            )

    add = ctx.io.mcp_memory_call(
        "tools/call",
        {"name": "memory_add", "arguments": card},
        agent_id=BRAIN_PROFILE_AGENT_ID,
    )
    if not add["ok"]:
        warnings.append(f"memory_add: {add['error']}")
        fallbacks.append(
            {
                "site": "memory_layer",
                "detail": f"memory_add failed ({add['error']}) — brain identity card not registered",
            }
        )
        return PhaseResult(
            status=PhaseStatus.OK,
            details={
                "registered": False,
                "card": card,
                "warnings": warnings,
                "fallbacks": fallbacks,
            },
            reason="hal0-memory unreachable; brain identity card not registered (continuing)",
        )

    memory_id = add["result"].get("id") if isinstance(add["result"], dict) else None
    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "registered": True,
            "agent_id": BRAIN_PROFILE_AGENT_ID,
            "memory_id": memory_id,
            "card": card,
            "refreshed_existing": bool(existing_ids),
            "warnings": warnings,
            "fallbacks": fallbacks,
        },
    )


# ── Phase H.7: brain_profile_mcp_wire ───────────────────────────────────────
#
# RELOCATE(brain-lane) — LANDED: no longer in ``_INSTALL_STEPS``. Folded into
# the hal0-api boot lifespan's ``_boot_seeds`` phase (local FS only, no
# memory call, so it belongs beside the persona/slot seeds rather than the
# memory-dependent trio in ``_boot_brain_lane``).
#
# The hal0-brain profile (``~/.hermes/profiles/hal0-brain/``, created by the
# upstream hermes binary) needs the two hal0-owned MCP servers — hal0-admin
# (platform control) + hal0-memory (its private 3-tier bank) — so the steward
# can actually interact with + control the box. On a hand-configured host these
# were wired out-of-band; this phase makes them reproducible. ``hermes config
# set`` has no per-profile target, so — exactly like honcho.json and the YAML
# list-keys — hal0 deep-merges the block into the profile's config.yaml,
# preserving every upstream/operator key. Only rewrites when the wiring is
# actually missing/wrong (a correct box is left byte-untouched, so the file's
# comments/formatting survive). Warn-as-OK; skips when the profile isn't there.

_BRAIN_PROFILE_NAME = "hal0-brain"  # on-disk hermes profile directory name


def _brain_profile_config_path(state: BootstrapState) -> Path:
    return Path(state.hermes_home) / "profiles" / _BRAIN_PROFILE_NAME / "config.yaml"


def _build_brain_profile_mcp_servers() -> dict[str, Any]:
    """The hal0-owned MCP servers the hal0-brain profile is wired to.

    Mirrors the top-level ``_default_mcp_servers`` admin + memory pair but scoped
    to the brain identity (``X-hal0-Agent: hermes__hal0-brain``); memory is
    private (its own 3-tier bank). Deliberately just these two — the steward
    gets platform control + memory only, not any other MCP surface.

    The ``/mcp`` mount is ADMIN-classed (security/exposure.py), so — same as
    the top-level overlay — a bearer is attached from the box service identity
    (:func:`hal0.service_identity.service_key`) whenever one is discoverable.
    Resolved fresh on every call: :func:`_phase_brain_profile_mcp_wire` re-runs
    this (and re-diffs the merge) on every provision/``--repair`` pass, so a
    rotated key is re-propagated the next time provisioning runs — the static
    config.yaml itself does not update live on rotation.
    """
    from hal0.agents.personas import BRAIN_PROFILE_AGENT_ID
    from hal0.service_identity import service_key

    bearer = service_key(prefer="admin")
    admin_headers: dict[str, Any] = {"X-hal0-Agent": BRAIN_PROFILE_AGENT_ID}
    memory_headers: dict[str, Any] = {"X-hal0-Agent": BRAIN_PROFILE_AGENT_ID, "X-hal0-Private": 1}
    if bearer:
        admin_headers["Authorization"] = f"Bearer {bearer}"
        memory_headers["Authorization"] = f"Bearer {bearer}"

    return {
        "hal0-admin": {
            "type": "http",
            "url": "http://127.0.0.1:8080/mcp/admin/mcp",
            "headers": admin_headers,
            "timeout": 60,
        },
        "hal0-memory": {
            "type": "http",
            "url": "http://127.0.0.1:8080/mcp/memory/mcp",
            "headers": memory_headers,
            "timeout": 30,
        },
    }


def _phase_brain_profile_mcp_wire(ctx: _StepCtx) -> PhaseResult:
    """Reproducibly write hal0's brain-profile keys: MCP servers + memory provider.

    Deep-merges the hal0-owned keys (hal0-admin + hal0-memory MCP servers, and
    ``memory.provider = hal0-memory``) into the profile config.yaml (merge,
    never clobber; scalars/dicts only — no list keys), only writing when
    something actually changed so a correctly configured box (and its comments)
    is left untouched. Skips when the profile config is absent — the upstream
    hermes binary owns profile creation. Any I/O / PyYAML gap degrades
    warn-as-OK; bootstrap never blocks on it.
    """
    state = ctx.state
    path = _brain_profile_config_path(state)
    # hal0-owned profile keys: the two MCP servers + the memory provider. Only
    # scalar/dict keys are set — never a list (``_deep_merge`` replaces lists,
    # which would clobber an operator's ``plugins.enabled`` / other entries).
    desired = {
        "mcp_servers": _build_brain_profile_mcp_servers(),
        "memory": {"provider": "hal0-memory"},
    }

    if not path.exists():
        return PhaseResult(
            status=PhaseStatus.OK,
            details={"wired": False, "reason": "profile config absent", "path": str(path)},
            reason="hal0-brain profile not present (upstream owns creation); MCP wiring skipped",
        )

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return PhaseResult(
            status=PhaseStatus.OK,
            details={"wired": False, "reason": "PyYAML unavailable", "path": str(path)},
            reason="PyYAML absent; brain profile MCP not wired (continuing)",
        )

    try:
        current = path.read_text(encoding="utf-8")
        base = yaml.safe_load(current) or {}
        merged = _deep_merge(base, desired)
        out = yaml.safe_dump(merged, sort_keys=False, default_flow_style=False)
        changed = out != current
        if changed:
            path.write_text(out, encoding="utf-8")
    except (OSError, yaml.YAMLError) as exc:  # type: ignore[attr-defined]
        return PhaseResult(
            status=PhaseStatus.OK,
            details={"wired": False, "error": str(exc), "path": str(path)},
            reason=f"brain profile MCP wire failed ({exc}); continuing",
        )

    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            "wired": True,
            "changed": changed,
            "path": str(path),
            "servers": sorted(desired["mcp_servers"].keys()),
        },
    )


# ── Live slot/model resolution ──────────────────────────────────────────────
#
# The slot fetchers + resolvers config_write uses to build the [model_aliases],
# delegation, and auxiliary blocks. (The old post-bootstrap model_automap phase
# is gone — config_write already sets model.* + model_aliases.*, and the runtime
# slot refresh is render_live_context's job, not an install step.)


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


def _fetch_model_route_ready(model_id: str) -> bool:
    """Ask ``GET /v1/models/{id}`` whether ``model_id`` actually routes.

    Unlike :func:`_fetch_model_contexts` (backed by the **list** route,
    ``GET /v1/models``), this hits the **by-id** route, which resolves hal0
    canonical virtual names (``hal0/agent``, ``hal0/utility``, ``hal0/npu``)
    via the LiveSlotResolver (see ``_resolve_virtual_model_entry`` in
    ``hal0.api.routes.v1``). The list route deliberately never advertises
    those virtuals — a physical model id also isn't always listed there,
    since a chat slot's alias entry supersedes its raw id (#1153) — so
    checking list *membership* against the shipped ``model.default:
    hal0/agent`` can never succeed even when the model is fully routable
    (#1831). 404/other client errors and network failures both mean
    "not ready"; only a clean response counts.
    """
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    req = Request(
        f"{HAL0_API_URL}/v1/models/{quote(model_id, safe='/')}",
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(req, timeout=3.0) as resp:
            return 200 <= resp.status < 300
    except HTTPError:
        return False
    except (URLError, OSError, TimeoutError):
        return False


def _slot_kind(slot: dict[str, Any]) -> str:
    """Best-effort capability classifier — handles a few schema variants.

    NOTE: still used by _phase_model_automap (~line 2743) for the embed/rerank/img
    skip list — do NOT remove. The "kind"-before-"type" priority is a latent bug
    for those slot types (tracked separately); voice_wire uses _find_slot instead,
    which checks slot["type"] directly.
    """
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
    return "agent"  # ADR-0023 canonical default anchor


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
    contexts_fetcher: Callable[[], dict[str, int]] | None = None,
    health_probe: Callable[..., int] | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Re-probe live slot/capability state; (re)write HERMES.md + STATE.md.

    STATE.md is content-hash gated: rewritten (and its ``_as_of`` line
    bumped) only when the substantive body changes. HERMES.md is written
    atomically (identical content => identical bytes => prompt-cache safe).
    Both land under the hal0-writable RUNTIME_SNAPSHOT_DIR (/var/lib/hal0);
    /etc/hal0/HERMES.md is a provision-maintained symlink to the latter, so
    this writer never touches the read-only /etc/hal0 in its sandbox.
    Never raises on a daemon-unreachable read — leaves last-good files and
    reports ``degraded=True``.

    Returns: {"state_written": bool, "hermes_written": bool,
              "degraded": bool, "state_path": str, "hermes_path": str}.
    """
    fetch = slots_fetcher or _fetch_slots
    slots_all = fetch() or []
    # Reachability is independent of slot count. A reachable daemon with
    # zero configured slots is NOT degraded (we render "no chat model
    # loaded" + reachable). degraded == the daemon couldn't be reached at
    # all — in which case we must NOT clobber a last-good snapshot. A
    # non-empty fetch implies the daemon answered, so we only probe health
    # when the slot list came back empty.
    probe = health_probe or _http_get
    reachable = True if slots_all else probe(DAEMON_HEALTH_URL) == 200
    degraded = not reachable

    contexts = (contexts_fetcher or _fetch_model_contexts)()
    chat_slots = _collect_chat_slots(slots_all, contexts=contexts)
    primary_raw = _resolve_primary_slot(slots_fetcher=lambda: slots_all)

    primary_slot = next(
        (
            s
            for s in slots_all
            if isinstance(s, dict) and s.get("name") in ("agent", "chat", "primary")
        ),
        None,
    )
    primary_for_template: dict[str, Any] | None = None
    if primary_raw["model"] and primary_raw["model"] not in ("agent", "utility", "chat", "primary"):
        primary_for_template = {
            "alias": _slot_alias(primary_slot) if primary_slot else "agent",
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
        "dashboard_url": os.environ.get(
            "HAL0_DASHBOARD_URL",
            os.environ.get("HAL0_API_URL", "http://hal0.local:8080").rstrip("/"),
        ),
        "inference_base": os.environ.get("HAL0_INFERENCE_BASE", "http://127.0.0.1:8080"),
        "daemon": "degraded" if degraded else "reachable",
        "as_of": now,
    }
    new_state = _render_template("STATE.md.j2", **state_vars)

    out: dict[str, Any] = {
        "state_written": False,
        "hermes_written": False,
        "degraded": degraded,
        "state_path": str(RUNTIME_SNAPSHOT_DIR / "STATE.md"),
        "hermes_path": str(RUNTIME_SNAPSHOT_DIR / "HERMES.md"),
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
    #
    # Written under the hal0-owned RUNTIME_SNAPSHOT_DIR (like STATE.md, #473),
    # NOT /etc/hal0: the runtime re-render is spawned detached from hal0-api
    # (hermes_refresh) on a slot/model swap and inherits *that* unit's
    # ProtectSystem=strict sandbox, where /etc/hal0 is read-only — writing it
    # there raised the non-fatal "Read-only file system: /etc/hal0/HERMES.md.tmp"
    # warning and silently froze HERMES.md on slot changes. /etc/hal0/HERMES.md
    # stays the stable read path via a symlink that provision (root) maintains.
    try:
        hermes_md = _render_template(
            "HERMES.md.j2",
            env=env_report,
            hal0_version=_hal0_version_string(),
            hermes_version=_hermes_version_pin(),
            primary=primary_for_template,
            chat_slots=chat_slots,
            peer_agents=[],
            dashboard_url=os.environ.get(
                "HAL0_DASHBOARD_URL",
                os.environ.get("HAL0_API_URL", "http://hal0.local:8080").rstrip("/"),
            ),
        )
        hpath = RUNTIME_SNAPSHOT_DIR / "HERMES.md"
        out["hermes_path"] = str(hpath)
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

    Both warm and cold slots are advertised — the gateway cold-loads on
    demand when a request addresses a cold slot by alias, so restricting
    to only ready slots needlessly hid models the gateway would happily
    serve.

    Each alias's ``backend_url`` is the STABLE hal0 gateway (`:8080/v1`),
    NOT the slot's raw ``backend_url``. The per-slot upstream port
    (`:8001/:8002/…`) can change on every model reload, so a baked-in
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
# Runtime auxiliary policy is resolved from typed live slot candidates, not
# hardcoded model ids, so model swaps flow through on the next `--repair`.
# ADR-0023 makes `agent` the canonical default anchor (`chat`/`primary` are
# compatibility aliases); delegation also targets the ready `agent` slot. Utility
# roles use the platform role hint when supplied, allowing labels to change
# without changing opaque slot identity or semantic assignment.
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

# Delegation retains its existing overlay adapter; auxiliary role policy lives
# in role_slots.py and is shared with runtime consumers.
_DELEGATION_SLOT_NAME = "agent"


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
    """Build the ``delegation`` template dict from the ``agent`` slot.

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

    The typed adapter preserves stable IDs and explicit role hints, then the
    shared resolver applies utility preference, NPU virtual addressing, and
    main fallback. This function only translates resolved roles back into the
    existing Hermes auxiliary overlay keys.
    """
    candidates = [candidate_from_slot_mapping(slot) for slot in slots if isinstance(slot, dict)]

    role_map = resolve_role_slots("hermes", candidates)
    by_role = {entry.role: entry for entry in role_map.entries}
    task_roles = {
        "vision": "vision",
        "web_extract": "vision",
        "compression": "compression",
        "session_search": "session_search",
        "title_generation": "compression",
        "skills_hub": "skills_hub",
        "mcp": "mcp",
    }
    tasks: dict[str, dict[str, Any]] = {}
    for task in (*_MAIN_AUX_TASKS, *_UTILITY_AUX_TASKS):
        entry = by_role[task_roles[task]]
        custom = entry.basis in {"utility", "npu_virtual"}
        tasks[task] = {
            "provider": "custom" if custom else "main",
            "model": entry.model if custom else "",
            "base_url": hal0_base_url if custom else "",
        }
    return tasks


# ── Phase J: voice_wire ─────────────────────────────────────────────────────
#
# Conditional. Emits STT/TTS provider config + writes
# /var/lib/hal0/secrets/agents/hermes.env. Per the #246
# correction, that secrets file is OUTBOUND credentials only
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
        "#\n"
        "# The `-` prefix makes the vault OPTIONAL: on a fresh install with no\n"
        "# platform tokens provisioned yet, the file doesn't exist — without the\n"
        "# `-`, systemd hard-fails the unit with `Failed to load environment\n"
        "# files` and crash-loops it. Optional lets the gateway come up (idle,\n"
        "# no platform) until tokens are added.\n"
        "[Service]\n"
        f"EnvironmentFile=-{HERMES_SECRETS_ENV}\n"
    )


@dataclass
class GatewayDropinResult:
    """Outcome of :func:`write_gateway_secrets_dropin`.

    ``outcome`` is one of ``"written"`` (drop-in (re)written), ``"unchanged"``
    (hash-skip), ``"skipped"`` (non-root or pytest-sandbox guard), or
    ``"failed"`` (filesystem write error). ``reason`` carries the human note for
    skip/fail (and a non-fatal daemon-reload warning on ``"written"``).
    """

    outcome: str
    dropin_path: str
    reason: str | None = None
    content_hash: str | None = None
    daemon_reload: bool = False


def write_gateway_secrets_dropin(*, run: Callable[..., Any] | None = None) -> GatewayDropinResult:
    """Idempotently write the gateway secrets drop-in + ``daemon-reload`` (#437).

    Owns ONLY the drop-in ``10-hal0-secrets.conf`` under
    ``/etc/systemd/system/hermes-gateway.service.d/`` — NOT the main
    ``hermes-gateway.service`` unit (generated separately by ``hermes gateway
    install --system``). The drop-in survives every main-unit regeneration
    because ``refresh_systemd_unit_if_needed`` rewrites the ``.service`` but
    never the ``.d/`` tree.

    Extracted from :func:`_phase_gateway_secrets_wire` so the CLI/installer
    gateway path can lay the drop-in down BEFORE ``hermes gateway install
    --system`` ``--now``-starts the unit. That ordering matters: hal0 flags a
    system gateway "foreign" iff it is active with no drop-in present
    (:func:`_detect_foreign_gateways`). If the vanilla unit starts before the
    drop-in exists, hal0 mistakes its OWN just-started unit for a foreign
    poller and refuses to manage it — blocking the Telegram/Discord bridge on
    fresh installs. Writing the drop-in first closes that window.

    Posture mirrors :func:`_merge_env_file` / config_write:

    * Hash-skip — an on-disk drop-in matching the rendered body skips both the
      write AND the ``systemctl daemon-reload`` (no needless systemd churn).
    * Atomic write — tmpfile + ``os.replace``; mode 0644 (systemd unit
      fragments must be world-readable; the *secrets* live in the 0600 vault
      the drop-in references, not in the drop-in itself).
    * daemon-reload only fires when the file actually changed.

    Privilege-aware (§7.4 drop-to-hal0): root writes ``/etc/systemd/system``
    directly; a non-root (hal0) caller routes the write + ``daemon-reload``
    through the ``hal0-systemctl`` seam (``sudo -n``). The drop-in is 0644
    world-readable, so the hash-skip read works for either caller.

    Guard (``outcome="skipped"``, never a crash): under pytest with an
    un-sandboxed (real ``/etc``) drop-in path we refuse to touch the host tree.
    Best-effort throughout — a filesystem/systemctl failure is reported in the
    result, never raised, so a gateway hiccup never aborts the caller.
    """
    run = run if run is not None else subprocess.run
    path = str(GATEWAY_SYSTEMD_DROPIN_FILE)

    # Defense-in-depth (regression: 2026-06-04 outage). The euid!=0 guard below
    # normally keeps the test suite off the host's real systemd tree — but it is
    # DEFEATED when pytest runs as root (or where /etc/systemd is ACL-writable).
    # A fixture that monkeypatches HERMES_SECRETS_ENV but forgets
    # GATEWAY_SYSTEMD_DROPIN_FILE would then write the live drop-in with a
    # pytest-tmp EnvironmentFile path, restart-looping the gateway once the tmp
    # dir is reaped. So: under pytest, refuse to touch the real /etc tree. A test
    # that genuinely exercises the write monkeypatches the dir to tmp_path.
    if os.environ.get("PYTEST_CURRENT_TEST") and str(GATEWAY_SYSTEMD_DROPIN_DIR).startswith(
        "/etc/"
    ):
        return GatewayDropinResult(
            outcome="skipped",
            dropin_path=path,
            reason=(
                "running under pytest with an un-sandboxed system drop-in path "
                "— refusing to write the real /etc/systemd tree; monkeypatch "
                "GATEWAY_SYSTEMD_DROPIN_DIR/FILE to tmp in the test fixture"
            ),
        )

    body = _gateway_dropin_body()
    content_sha = content_hash(body)

    # Privilege-aware write. Root writes /etc/systemd/system directly; the
    # unprivileged hal0 provisioner (post §7.4 drop-to-hal0) cannot, so it routes
    # the write + daemon-reload through the hal0-systemctl seam. The drop-in is
    # 0644 world-readable, so the hash-skip read below works either way.
    via_seam = os.geteuid() != 0

    # Hash-skip: an unchanged drop-in needs neither a rewrite nor a
    # daemon-reload (#437 idempotency criterion, mirroring config_write).
    if GATEWAY_SYSTEMD_DROPIN_FILE.exists():
        try:
            current = GATEWAY_SYSTEMD_DROPIN_FILE.read_text(encoding="utf-8")
        except OSError:
            current = None
        if current is not None and content_hash(current) == content_sha:
            return GatewayDropinResult(
                outcome="unchanged",
                dropin_path=path,
                content_hash=content_sha,
                daemon_reload=False,
            )

    try:
        if via_seam:
            # Root:root dir — delegate the write to the seam (fixed path, body
            # on stdin). The seam mkdir's the .d dir and pins 0644 root:root.
            _privileged_systemctl("write-gateway-dropin", body)
        else:
            GATEWAY_SYSTEMD_DROPIN_DIR.mkdir(parents=True, exist_ok=True)
            GATEWAY_SYSTEMD_DROPIN_DIR.chmod(0o755)
            tmp = GATEWAY_SYSTEMD_DROPIN_FILE.with_suffix(".conf.tmp")
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, GATEWAY_SYSTEMD_DROPIN_FILE)
            GATEWAY_SYSTEMD_DROPIN_FILE.chmod(0o644)
    except (OSError, subprocess.SubprocessError) as exc:
        return GatewayDropinResult(
            outcome="failed",
            dropin_path=path,
            reason=f"gateway drop-in write to {path} failed: {exc}",
            content_hash=content_sha,
        )

    try:
        if via_seam:
            _privileged_systemctl("daemon-reload")
        else:
            run(["systemctl", "daemon-reload"], check=True)  # nosec B603 B607
    except (subprocess.SubprocessError, OSError) as exc:
        # The drop-in is on disk; the operator can daemon-reload by hand.
        # Surface as a non-fatal warning rather than failing — the wiring lands
        # on the next `systemctl daemon-reload`.
        return GatewayDropinResult(
            outcome="written",
            dropin_path=path,
            reason=f"drop-in written but `systemctl daemon-reload` failed: {exc}",
            content_hash=content_sha,
            daemon_reload=False,
        )

    return GatewayDropinResult(
        outcome="written",
        dropin_path=path,
        content_hash=content_sha,
        daemon_reload=True,
    )


def _phase_gateway_secrets_wire(ctx: _StepCtx) -> PhaseResult:
    """Idempotently write the gateway secrets drop-in + daemon-reload (#437).

    Thin phase adapter over :func:`write_gateway_secrets_dropin` (which owns
    the write + guards + idempotency). Keeping unit generation out of this phase
    avoids the hermes_cli generator's custom-HERMES_HOME trap; the orchestrator
    runs ``hermes gateway install --system`` separately to lay the main unit.
    Non-root (hal0) callers route the write through the hal0-systemctl seam; a
    pytest-sandboxed caller SKIPs with a clear reason rather than failing the
    whole bootstrap.
    """
    result = write_gateway_secrets_dropin(run=ctx.io.run)
    details: dict[str, Any] = {"dropin_path": result.dropin_path}
    if result.content_hash is not None:
        details["content_hash"] = result.content_hash

    # RATIFIED 2026-07-18: ensure the gateway's API_SERVER_KEY is a strong
    # random secret in the vault the drop-in above references. Hermes' gateway
    # api_server refuses to start without it; a placeholder would be a shared
    # secret. Idempotent — a rerun keeps an existing strong key. Log-safe: we
    # record only the outcome + key length, never the value.
    key_generated = False
    try:
        key_result = ensure_gateway_api_server_key()
        key_generated = key_result.outcome == "generated"
        details["api_server_key"] = {
            "outcome": key_result.outcome,
            "key_len": key_result.key_len,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        details["api_server_key"] = {"outcome": "failed", "error": str(exc)}

    if result.outcome == "skipped":
        return PhaseResult(status=PhaseStatus.SKIP, reason=result.reason, details=details)
    if result.outcome == "failed":
        return PhaseResult(status=PhaseStatus.FAIL, reason=result.reason, details=details)

    # "written" | "unchanged" → OK. Convergence signal: the drop-in was
    # (re)written or a fresh API key was generated. A converged box is "unchanged"
    # + an already-present key → changed False.
    details["daemon_reload"] = result.daemon_reload
    details["changed"] = (result.outcome == "written") or key_generated
    if result.outcome == "unchanged":
        details["unchanged"] = True
    return PhaseResult(
        status=PhaseStatus.OK,
        hash=result.content_hash,
        reason=result.reason,
        details=details,
    )


# /api/slots uses "transcription" as the type for STT slots; accept both so
# _find_slot(slots, "stt") matches regardless of which label the server uses.
_STT_SLOT_TYPES: frozenset[str] = frozenset({"stt", "transcription"})


def _find_slot(slots: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    # Check the functional type field directly — _slot_kind checks "kind" before
    # "type", but "kind" carries the local/remote deployment shape ("local"),
    # not the capability.  Matching on "type" (then "capability") avoids the
    # short-circuit that made voice_wire always skip local tts/transcription slots.
    accept = _STT_SLOT_TYPES if kind == "stt" else frozenset({kind})
    for s in slots:
        slot_type = (s.get("type") or s.get("capability") or "").lower()
        if slot_type in accept and _is_ready(s):
            return s
    # STT special case: the NPU-trio transcription facade (type=transcription,
    # served_by=<anchor>) always reports state=offline — it has no unit of its
    # own and routes through the npu anchor's FLM child. container_enrichment
    # stamps served_by on these shadows. Accept the facade when its anchor is
    # ready so voice_wire auto-provisions STT_OPENAI_BASE_URL.
    if kind == "stt":
        ready_names = {str(s.get("name")) for s in slots if _is_ready(s) and s.get("name")}
        for s in slots:
            slot_type = (s.get("type") or s.get("capability") or "").lower()
            anchor = s.get("served_by")
            if slot_type in accept and isinstance(anchor, str) and anchor in ready_names:
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


#: Privileged seam for writing the root:root agent .env files when hal0-api runs
#: unprivileged (D hardened-perms). Mirrors the hal0-slotctl seam: a non-root
#: provisioner cannot write the secrets vault
#: (/var/lib/hal0/secrets/agents/<agent>.env, 0600 root:root) or the driver env
#: (/etc/hal0/agents/<agent>.env, 0644 root:root) directly, so it delegates the
#: write to this root helper over `sudo -n`. Env-overridable for tests.
_HAL0_AGENTENV = os.environ.get("HAL0_AGENTENV", "/usr/lib/hal0/bin/hal0-agentenv")

#: The agent this provisioner manages; the seam re-validates it server-side.
_HERMES_AGENT_NAME = "hermes"

#: Privileged seam for the genuinely-root systemd ops the provisioner needs when
#: it runs unprivileged (as the hal0 user): writing the fixed hermes-gateway
#: secrets drop-in under /etc/systemd/system and `daemon-reload`. Mirrors the
#: hal0-agentenv seam — a non-root provisioner cannot write /etc/systemd/system
#: or reload systemd, so it delegates to this root helper over `sudo -n`. The
#: helper builds the (literal) drop-in path itself and takes the body on stdin.
#: Env-overridable for tests.
_HAL0_SYSTEMCTL = os.environ.get("HAL0_SYSTEMCTL", "/usr/lib/hal0/bin/hal0-systemctl")


# ── Stale agent drop-in cleanup (RATIFIED 2026-07-18, deliverable 5) ─────────
#
# systemd merges EVERY *.conf under hal0-agent@<id>.service.d/, so a drop-in an
# OLD installer left behind still applies even after the current template
# overwrites override.conf. halo150 O3: a stale `ConfigurationDirectory=`
# drop-in brick-loops the unit with status=241/CONFIGURATION_DIRECTORY (the
# current template ships no such directive). Convergent cleanup — mirrors the
# stale static slot-unit removal already in install.sh: scan the drop-in dirs,
# remove any NON-shipped fragment carrying a directive the template doesn't
# ship, report what was removed, no-op otherwise.

SYSTEMD_SYSTEM_DIR = Path("/etc/systemd/system")

#: Drop-in filenames the CURRENT template legitimately ships (never removed).
_SHIPPED_AGENT_DROPINS: frozenset[str] = frozenset({"override.conf"})

#: Directives the current hal0-agent@ template does NOT set; a drop-in that
#: carries one is stale debris from an old install and is removed. The 241
#: brick-loop class is ConfigurationDirectory= (halo150 O3).
_STALE_AGENT_DIRECTIVES: tuple[str, ...] = ("ConfigurationDirectory=",)


@dataclass
class StaleDropinCleanupResult:
    """Outcome of :func:`cleanup_stale_agent_dropins`."""

    removed: list[str] = field(default_factory=list)
    daemon_reloaded: bool = False


def cleanup_stale_agent_dropins(
    *,
    systemd_dir: Path = SYSTEMD_SYSTEM_DIR,
    shipped: frozenset[str] = _SHIPPED_AGENT_DROPINS,
    stale_directives: tuple[str, ...] = _STALE_AGENT_DIRECTIVES,
    unlink: Callable[[Path], None] | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> StaleDropinCleanupResult:
    """Remove stale hal0-agent@ drop-in fragments the template doesn't ship.

    Scans every ``hal0-agent@*.service.d/`` under ``systemd_dir`` and deletes
    any ``*.conf`` that is (a) NOT a shipped drop-in name and (b) carries a
    ``stale_directives`` directive (the 241/CONFIGURATION_DIRECTORY class).
    Runs ``daemon-reload`` only if something was removed. Idempotent: a clean
    box removes nothing and never reloads. Every seam is injectable for tests.
    """
    # Host-safety guard (mirrors write_gateway_secrets_dropin): under pytest with
    # the real /etc/systemd path, refuse to touch the host tree — a test that
    # genuinely exercises the cleanup passes systemd_dir=tmp_path.
    if os.environ.get("PYTEST_CURRENT_TEST") and str(systemd_dir).startswith("/etc/"):
        return StaleDropinCleanupResult()

    do_unlink = unlink if unlink is not None else (lambda p: p.unlink())
    removed: list[str] = []
    for dropin_dir in sorted(systemd_dir.glob("hal0-agent@*.service.d")):
        if not dropin_dir.is_dir():
            continue
        for conf in sorted(dropin_dir.glob("*.conf")):
            if conf.name in shipped:
                continue
            try:
                text = conf.read_text(encoding="utf-8")
            except OSError:
                continue
            if any(directive in text for directive in stale_directives):
                try:
                    do_unlink(conf)
                except OSError as exc:
                    log.warning(
                        "hermes_provision.stale_dropin_unlink_failed",
                        path=str(conf),
                        error=str(exc),
                    )
                    continue
                removed.append(str(conf))
                log.info("hermes_provision.removed_stale_agent_dropin", path=str(conf))

    reloaded = False
    if removed:
        try:
            run(["systemctl", "daemon-reload"], check=True)  # nosec B603 B607
            reloaded = True
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("hermes_provision.stale_dropin_daemon_reload_failed", error=str(exc))
    return StaleDropinCleanupResult(removed=removed, daemon_reloaded=reloaded)


def _privileged_systemctl(verb: str, body: str | None = None) -> None:
    """Run one hal0-systemctl seam verb as root via ``sudo -n``.

    Thin wrapper over :func:`hal0.system.seam.privileged_systemctl` — the ONE
    place the ``sudo -n <seam-bin> <verb>`` invocation is spelled, now shared
    with the agent-unit teardown path (``HermesDriver._stop_services``). Kept as
    a module-local name so ``_HAL0_SYSTEMCTL`` stays the env-overridable knob
    this module's tests monkeypatch.

    ``body`` (when given) is piped on stdin — used for ``write-gateway-dropin``.
    Raises ``subprocess.CalledProcessError`` on a non-zero seam exit so the
    caller surfaces the failure instead of masquerading a broken gateway as up.
    """
    _seam.privileged_systemctl(verb, body=body, seam_bin=_HAL0_SYSTEMCTL, check=True)


def _privileged_env_write(verb: str, body: str) -> None:
    """Pipe ``body`` to the hal0-agentenv seam as root via ``sudo -n``.

    The unprivileged path for an env write that lands in a root:root dir. Raises
    ``subprocess.CalledProcessError`` on a non-zero seam exit (no sudo grant,
    bad verb, write failure) so the caller surfaces it loudly — the optional
    ``EnvironmentFile=-`` would otherwise let a swallowed failure masquerade as
    "gateway up, no tokens".
    """
    subprocess.run(  # nosec B603 — fixed argv; agent name re-validated by the seam
        ["sudo", "-n", _HAL0_AGENTENV, verb, _HERMES_AGENT_NAME],
        input=body,
        text=True,
        check=True,
    )


def _write_secrets_env(updates: dict[str, str]) -> None:
    """Merge ``updates`` into the root:root secrets vault, privilege-aware.

    Root (install-time) writes directly via :func:`_merge_env_file` and re-pins
    the file root:root. Unprivileged (the flipped hal0-api at runtime) routes
    the merge through the hal0-agentenv seam, which read-merges AS ROOT so the
    0600 vault never has to be readable by the ``hal0`` user.
    """
    if os.geteuid() == 0:
        _merge_env_file(HERMES_SECRETS_ENV, updates)
        with contextlib.suppress(OSError):
            os.chown(HERMES_SECRETS_ENV, 0, 0)
    else:
        body = "".join(f"{k}={v}\n" for k, v in updates.items())
        _privileged_env_write("merge-secrets", body)


# ── Gateway API_SERVER_KEY (RATIFIED 2026-07-18, security) ───────────────────
#
# Hermes' gateway HTTP API (``gateway/platforms/api_server.py``) REFUSES to
# start without ``API_SERVER_KEY`` and rejects placeholder / <16-char keys
# (contract: tests/fixtures/hermes/contracts/api_surface.py). The installer
# must therefore provision a STRONG random key into the secrets vault — never a
# hardcoded placeholder (which would be a shared secret across every hal0 box).
# Idempotent: an already-strong key is left untouched so reruns don't rotate it.

#: Minimum length for a provisioned gateway API key. We generate 43-char
#: (token_urlsafe(32) = 256-bit) keys; the floor is 32 (double Hermes' own 16).
API_SERVER_KEY_MIN_LENGTH = 32

#: Values we must NEVER accept as a real key — the placeholder/weak set. A key
#: matching any of these (case-insensitive) is treated as absent → regenerated.
_WEAK_API_SERVER_KEYS: frozenset[str] = frozenset(
    {"", "changeme", "change-me", "placeholder", "dummy", "hal0-local", "secret", "test", "none"}
)


def _is_strong_api_server_key(value: str | None) -> bool:
    """True iff ``value`` is a real, cryptographically-strong gateway key."""
    if not value:
        return False
    if value.strip().lower() in _WEAK_API_SERVER_KEYS:
        return False
    return len(value) >= API_SERVER_KEY_MIN_LENGTH


def _generate_api_server_key() -> str:
    """Return a fresh 256-bit URL-safe key (43 chars). Never a placeholder."""
    import secrets as _secrets

    return _secrets.token_urlsafe(32)


def _read_secrets_env(path: Path | None = None) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from the secrets vault (best-effort read)."""
    target = path if path is not None else HERMES_SECRETS_ENV
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@dataclass
class ApiServerKeyResult:
    """Outcome of :func:`ensure_gateway_api_server_key`.

    ``outcome`` is ``"generated"`` (a new strong key was written),
    ``"present"`` (an existing strong key was kept — the idempotent rerun
    path), or ``"unreadable"`` (a non-root caller could not read the 0600
    vault, so the install-time root key is assumed and left alone). ``key_len``
    never carries the key VALUE — only its length, so results are log-safe.
    """

    outcome: str
    key_len: int = 0


def ensure_gateway_api_server_key(
    *,
    existing: dict[str, str] | None = None,
    generate: Callable[[], str] = _generate_api_server_key,
    write: Callable[[dict[str, str]], None] = _write_secrets_env,
) -> ApiServerKeyResult:
    """Idempotently ensure a strong ``API_SERVER_KEY`` in the gateway vault.

    Reads the current vault (``existing`` overrides for tests); if it already
    holds a strong key, returns ``present`` and writes nothing (so reruns never
    rotate the key). Otherwise generates a 256-bit random key and merges it into
    the root:root secrets vault. There is deliberately NO hardcoded fallback: a
    placeholder key would let Hermes' gateway start with a shared, guessable
    secret across every hal0 box.
    """
    if existing is not None:
        env = existing
    else:
        vault_exists = HERMES_SECRETS_ENV.exists()
        env = _read_secrets_env()
        # A vault that exists but reads empty for a non-root caller is the
        # 0600-root-owned case: install-time root already provisioned the key.
        # Don't clobber it (we couldn't verify it, but we also can't read it —
        # regenerating every runtime rerun would rotate a working key).
        if vault_exists and not env and os.geteuid() != 0:
            return ApiServerKeyResult(outcome="unreadable")

    current = env.get("API_SERVER_KEY")
    if _is_strong_api_server_key(current):
        return ApiServerKeyResult(outcome="present", key_len=len(current or ""))

    new_key = generate()
    write({"API_SERVER_KEY": new_key})
    return ApiServerKeyResult(outcome="generated", key_len=len(new_key))


# ── Repair-only ownership reconcile (RATIFIED 2026-07-18, deliverable 6) ──────
#
# The always-run ownership_reconcile phase is DEAD (§7.4 F.7): a normal install
# drops to hal0 so files are born hal0:hal0 with nothing to reconcile. But a box
# provisioned by an OLD root-clobbering installer can still have HERMES_HOME /
# venv / config owned root:root — `hal0 doctor perms` flags this as "Hermes
# ownership drift" (halo150 O3). `hal0 agent bootstrap hermes --repair` must
# ACTUALLY FIX what that audit reports, not just re-run the converging writes.
# So repair (root only) reconciles the exact perms.py rows the audit checks.


@dataclass
class OwnershipReconcileResult:
    """Outcome of :func:`reconcile_ownership_on_repair`."""

    reconciled: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


#: perms.py roles the Hermes provision lane owns — the drift `doctor perms`
#: reports and `--repair` must fix. Kept in lockstep with perms.ownership_table.
_HERMES_OWNERSHIP_ROLES: frozenset[str] = frozenset(
    {"HERMES_HOME", "agents/ (per-agent sub-homes)"}
)


def reconcile_ownership_on_repair(
    *,
    enabled: bool,
    venv: Path | str = HERMES_VENV_DEFAULT,
    observe_fn: Callable[..., Any] | None = None,
    chown: Callable[[str, int, int], None] = os.chown,
    chmod: Callable[[str, int], None] = os.chmod,
    walk: Callable[[str], Any] = os.walk,
) -> OwnershipReconcileResult:
    """Reconcile HERMES_HOME/agents + the venv to hal0:hal0 (repair path only).

    ``enabled`` is ``repair and euid == 0`` — the caller computes it so this is
    a pure, fakes-testable function. When disabled, returns a no-op result with
    a reason. When enabled, it drives :mod:`hal0.install.perms` (the single
    declarative ownership truth) for the Hermes rows the audit checks, then
    recursively chowns the venv tree (which perms has no per-file row for).
    Every seam (observe/chown/chmod/walk) is injectable so tests exercise the
    drift→fix path without a real filesystem.
    """
    if not enabled:
        return OwnershipReconcileResult(
            skipped_reason="repair reconcile is root-only (`--repair` as root)"
        )

    from hal0.install import perms as _perms

    reconciled: list[str] = []

    # 1. Declarative rows (HERMES_HOME + agents/): plan against disk, commit the
    #    drift. perms.commit resolves hal0:hal0 and applies chown+chmod.
    rows = [r for r in _perms.ownership_table() if r.role in _HERMES_OWNERSHIP_ROLES]
    plan_kwargs: dict[str, Any] = {}
    if observe_fn is not None:
        plan_kwargs["observe_fn"] = observe_fn
    plan_ = _perms.plan(rows, **plan_kwargs)
    changed = _perms.commit(plan_, chown=chown, chmod=chmod)
    reconciled.extend(str(p) for p in changed)

    # 2. The venv tree has no per-file perms row — recursively chown it to hal0
    #    so a root-installed venv the User=hal0 unit can't exec is repaired.
    venv_path = Path(venv)
    if venv_path.exists():
        uid = pwd.getpwnam("hal0").pw_uid
        gid = grp.getgrnam("hal0").gr_gid
        chown(str(venv_path), uid, gid)
        for root, dirs, files in walk(str(venv_path)):
            for name in list(dirs) + list(files):
                with contextlib.suppress(OSError):
                    chown(str(Path(root) / name), uid, gid)
        reconciled.append(str(venv_path))

    return OwnershipReconcileResult(reconciled=reconciled)


def _phase_voice_wire(ctx: _StepCtx) -> PhaseResult:
    """Emit STT/TTS provider config + secrets env when both slots are ready.

    Skip semantics: when neither STT nor TTS is configured + ready,
    return SKIP with a clear reason — same posture as voice_wire in
    the plan §13.
    """
    state = ctx.state
    slots = ctx.io.fetch_slots()
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
        _write_secrets_env(updates)
    except (OSError, subprocess.SubprocessError) as exc:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"secrets env write to {HERMES_SECRETS_ENV} failed: {exc}",
            details=details,
        )

    # Wire the stt:/tts: blocks via `hermes config set` (nested scalars).
    # The backend URL lives in the secrets env above (STT_/TTS_OPENAI_BASE_URL);
    # config carries only provider + per-engine model, matching the old render.
    hermes_home = Path(state.hermes_home)
    config_path = hermes_home / "config.yaml"
    hermes_bin = _hermes_bin(Path(state.venv))
    pairs: list[tuple[str, Any]] = []
    if details["stt"]:
        pairs.append(("stt.provider", "openai"))
        if details["stt"]["model"]:
            pairs.append(("stt.openai.model", details["stt"]["model"]))
    if details["tts"]:
        pairs.append(("tts.provider", "openai"))
        if details["tts"]["model"]:
            pairs.append(("tts.openai.model", details["tts"]["model"]))
    applied, errors = _apply_config_set(
        pairs, hermes_bin=hermes_bin, hermes_home=hermes_home, run=ctx.io.run
    )

    return PhaseResult(
        status=PhaseStatus.OK,
        details={
            **details,
            "secrets_env": str(HERMES_SECRETS_ENV),
            "config_path": str(config_path),
            "keys_applied": applied,
            "config_set_errors": errors,
            # A wired voice slot writes the secrets vault + config keys. Boxes
            # without stt/tts SKIP above (never reach here) — so this only fires
            # when there is genuinely voice wiring to apply.
            "changed": bool(updates),
        },
    )


# ── Phase K: smoke_tests ────────────────────────────────────────────────────
#
# Six non-fatal probes per plan §14 + #246. Each surface check writes a
# `passed: bool` row into PhaseResult.details["results"]; failures
# also carry a remediation hint operators can paste at the user.
#
# The phase never FAILS the install over a smoke miss — smoke_tests are
# diagnostic, not gating — but a phase carrying real failures must not
# report itself ``ok`` either (#1793): the phase status is ``warn`` when
# any probe fails, so `hal0 agent status` / the API surface it instead of
# burying it in the Detail column. self_report surfaces the same rollup in
# the bootstrap-completion memory item.
#
# chat_completions needs a routable chat model to mean anything; at install
# time — before any model has ever been loaded — that's structurally
# impossible, so a preflight (`_chat_model_ready`) decides ONCE whether the
# route is live and the probe reports `skipped: no chat model loaded`
# instead of burning its timeout on a doomed request that then gets
# recorded as an opaque failure. memory_roundtrip has no chat dependency
# (it drives the MCP memory_add/memory_search tools directly) and always
# runs (#1831).


def _wrapper_bin() -> Path:
    # The canonical entry point (the hal0-hermes back-compat symlink is retired).
    return HERMES_CLI_INSTALL_PATH


def _smoke_chat_completions(state: BootstrapState, _io: InstallIO) -> tuple[bool, str]:
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


def _smoke_memory_roundtrip(state: BootstrapState, io: InstallIO) -> tuple[bool, str]:
    add = io.mcp_memory_call(
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
    search = io.mcp_memory_call(
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


def _smoke_admin_tools_list(state: BootstrapState, io: InstallIO) -> tuple[bool, str]:
    probe = io.probe_mcp_server(
        "http://127.0.0.1:8080/mcp/admin",
        agent_id=state.agent_id,
        private=False,
    )
    if not probe["ok"]:
        return (False, probe["error"] or "unreachable")
    n = len(probe["tools"])
    return (n >= 5, f"{n} tools advertised")


def _smoke_hermes_md_contains_primary(state: BootstrapState, _io: InstallIO) -> tuple[bool, str]:
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


def _smoke_wrapper_ready(_state: BootstrapState, io: InstallIO) -> tuple[bool, str]:
    wrapper = _wrapper_bin()
    if not wrapper.exists():
        return (False, f"wrapper missing at {wrapper}")
    try:
        result = io.run(  # nosec B603 — known-safe argv
            [str(wrapper), "--hal0-ready"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, f"wrapper exec: {exc}")
    return (result.returncode == 0, f"--hal0-ready rc={result.returncode}")


def _smoke_hermes_doctor(_state: BootstrapState, io: InstallIO) -> tuple[bool, str]:
    venv_hermes = _venv_python(Path(_state.venv)).parent / "hermes"
    if not venv_hermes.exists():
        return (False, f"hermes binary missing at {venv_hermes}")
    try:
        result = io.run(  # nosec B603 — known-safe argv
            [str(venv_hermes), "doctor"],
            check=False,
            capture_output=True,
            timeout=30,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, f"hermes doctor: {exc}")
    return (result.returncode == 0, f"rc={result.returncode}")


#: Probes that only mean something once a chat-capable model is actually
#: routable. Gated behind :func:`_chat_model_ready` so an install-time smoke
#: run (no model has ever been loaded yet) reports `skipped`, not a timeout.
#: memory_roundtrip does NOT belong here — it exercises the MCP
#: memory_add/memory_search tools directly and has no chat dependency
#: whatsoever (#1831); gating it behind the chat anchor over-skipped it even
#: when memory itself was fully working.
_MODEL_DEPENDENT_PROBES = frozenset({"chat_completions"})


def _chat_model_ready(state: BootstrapState, io: InstallIO) -> tuple[bool, str]:
    """Cheap preflight: is a chat-capable model actually routable right now?

    Reads ``model.default`` from config.yaml — the exact field
    :func:`_smoke_chat_completions` targets — and cross-checks it against the
    live gateway via ``io.fetch_model_route_ready`` (``GET
    /v1/models/{id}``), instead of paying a full chat-completion round trip
    just to find out nothing is loaded.

    This resolves the anchor through the **by-id** route rather than
    checking membership in the **list** route's payload (``GET
    /v1/models``): the list route deliberately never advertises hal0's
    canonical virtual names (``hal0/agent`` et al. — see
    ``_aggregate_models`` in ``hal0.api.routes.v1``) and also folds a chat
    slot's raw model id into its alias entry, so list membership can never
    hold for the shipped ``model.default: hal0/agent`` even when the model
    is fully routable. The by-id route resolves virtuals via the
    LiveSlotResolver (``_resolve_virtual_model_entry``), matching exactly
    what :func:`_smoke_chat_completions` itself dispatches against (#1831).

    This is what lets smoke_tests tell "the route is genuinely broken" (a
    real failure) apart from "no chat model exists yet" (#1793) — never
    raises; any config/parse/network problem reports not-ready with the
    reason.
    """
    import yaml  # type: ignore[import-untyped]

    config_path = Path(state.hermes_home) / "config.yaml"
    if not config_path.exists():
        return False, "config.yaml missing"
    try:
        cfg = yaml.safe_load(config_path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return False, f"config parse: {exc}"
    model_name = (cfg.get("model") or {}).get("default", "")
    if not model_name:
        return False, "model.default unset in config.yaml"
    try:
        routable = io.fetch_model_route_ready(model_name)
    except Exception as exc:  # preflight must never raise
        return False, f"gateway probe failed: {exc}"
    if not routable:
        return False, f"'{model_name}' not loaded on the gateway"
    return True, "ready"


def _phase_smoke_tests(ctx: _StepCtx) -> PhaseResult:
    """Run six diagnostic probes; collect results into the checkpoint.

    ``status`` is ``warn`` (not ``ok``) when any probe genuinely fails — a
    phase that recorded failures must say so, not report a clean ``ok`` with
    the failures buried in ``details`` (#1793). It never becomes ``fail``:
    smoke_tests stays diagnostic, so the overall install still succeeds.
    """
    state = ctx.state
    chat_ready, chat_reason = _chat_model_ready(state, ctx.io)
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
    skipped: list[str] = []
    for name, fn in probes:
        if name in _MODEL_DEPENDENT_PROBES and not chat_ready:
            detail = f"skipped: no chat model loaded ({chat_reason})"
            results[name] = {"passed": None, "skipped": True, "detail": detail}
            skipped.append(f"{name}: {detail}")
            continue
        try:
            passed, detail = fn(state, ctx.io)
        except Exception as exc:
            passed, detail = (False, f"{type(exc).__name__}: {exc}")
        results[name] = {"passed": passed, "detail": detail}
        if not passed:
            failures.append(f"{name}: {detail}")
    status = PhaseStatus.WARN if failures else PhaseStatus.OK
    return PhaseResult(
        status=status,
        details={"results": results, "failures": failures, "skipped": skipped},
    )


# ── Phase L: self_report ────────────────────────────────────────────────────
#
# Final summary memory item under private:<agent_id> — first thing
# the agent recalls on next session start. Includes the smoke-test
# rollup so a degraded install surfaces in chat.


def _phase_self_report(ctx: _StepCtx) -> PhaseResult:
    """Write a bootstrap-completion summary into the agent's private namespace.

    RELOCATE(brain-lane) — LANDED: no longer in ``_INSTALL_STEPS``. Called
    LAST from the hal0-api boot lifespan's terminal ``_boot_brain_lane``
    phase. ``ctx.output_of("smoke_tests")`` has no lifespan analogue — the
    boot phase substitutes a ``{"failures": [...]}`` shape derived from
    ``app.state.boot_report`` (boot-phase failures, not real smoke-test
    results; see ``_boot_brain_lane``'s docstring).

    Failure of the memory write is non-fatal — same posture as
    namespace_register (#243): the memory layer being unavailable
    shouldn't fail bootstrap.
    """
    state = ctx.state
    smoke = ctx.output_of("smoke_tests")
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
    add = ctx.io.mcp_memory_call(
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
            # Track-latest by design. No version pin.
            "version_pin": False,
        },
        "data_dir": str(Path("/var/lib/hal0/agents/hermes")),
    }


# Builtin MCP servers every hermes install must be able to reach — the
# [mcp.servers.<name>] seed-TOML counterpart of _default_mcp_servers()'s two
# entries (that one shapes config.yaml; this one shapes AgentMCPClient's
# policy file). `url` is deliberately omitted: MCPServerConfig.url is
# required only for non-builtin servers (schema.py url_required_for_external)
# — hal0 already knows where its own /mcp/admin + /mcp/memory mounts live.
_BUILTIN_MCP_SERVER_NAMES: tuple[str, ...] = ("hal0-admin", "hal0-memory")


def _builtin_mcp_seed_servers() -> dict[str, dict[str, Any]]:
    """The ``[mcp.servers.hal0-admin]`` / ``[mcp.servers.hal0-memory]`` blocks
    the seed TOML needs so two things work:

    * :meth:`hal0.agents.mcp_client.AgentMCPClient.token_for` can resolve a
      bearer for in-platform callers — it reads ``auth.kind``/``auth.env``
      straight off this file.
    * :func:`_phase_mcp_wire`'s allow-list gate (ADR-0013) doesn't filter the
      builtins out. ``_load_agent_allowlist`` treats a present-but-empty
      ``[mcp.servers]`` table as "nothing is allowed" (not "no restriction" —
      that reading only applies when the whole seed file is absent), and the
      seed TOML always exists once install_artifacts has run once. Without
      this, every mcp_wire probe silently reports both builtins
      ``skipped_by_allowlist`` forever, so the live handshake that would
      catch a broken/missing bearer never runs — the exact silent-401 class
      the ``doctor`` MCP check now guards against.

    ``auth`` is attached only when a box service key is currently resolvable
    (mirrors ``_build_config_overlay``'s bearer resolution): on a keyless/dev
    box the entries still register (so mcp_wire can probe them tokenless) but
    carry no auth block, matching ``AgentAuthConfig``'s ``kind="none"``
    default — no failure, just tokenless, same as every other auth-optional
    site in this module.
    """
    from hal0.service_identity import service_key

    bearer = service_key(prefer="admin")
    block: dict[str, Any] = {"builtin": True, "enabled": True}
    if bearer:
        block = {**block, "auth": {"kind": "bearer-from-env", "env": "HAL0_MCP_TOKEN"}}
    return {name: dict(block) for name in _BUILTIN_MCP_SERVER_NAMES}


def _write_seed_toml(state: BootstrapState, *, repair: bool) -> tuple[Path, bool]:
    """Write/merge the manager seed at :data:`INSTALL_SEED_PATH`.

    The seed file doubles as the MCP allow-list (``[mcp.servers.*]``), so we
    deep-merge: refresh ``[agent]`` + ``data_dir`` + the two builtin
    ``[mcp.servers.*]`` blocks (:func:`_builtin_mcp_seed_servers`) while
    preserving any operator-added server blocks. Returns ``(path, wrote)`` —
    ``wrote`` is ``False`` when an existing ``[agent]`` block already carried
    an ``installed_at`` and ``repair`` is off (idempotent no-op on re-run).
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

    # Refresh (never remove) the two builtin [mcp.servers.*] blocks — merge at
    # the per-server level so any operator-added server (e.g.
    # [mcp.servers.custom]) survives untouched.
    mcp_block = dict(existing.get("mcp") or {})
    servers_block = dict(mcp_block.get("servers") or {})
    servers_block.update(_builtin_mcp_seed_servers())
    mcp_block["servers"] = servers_block
    merged["mcp"] = mcp_block

    body = tomli_w.dumps(merged)
    if os.geteuid() == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".toml.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
        with contextlib.suppress(OSError):
            os.chown(path, 0, 0)
    else:
        # Seed TOML lives in root:root /etc/hal0/agents — delegate the write to
        # the seam (it builds the path from the validated agent name). The
        # read-merge above works unprivileged: the file is 0644 world-readable.
        _privileged_env_write("write-seed-toml", body)
    return path, True


def _write_driver_env(state: BootstrapState) -> tuple[Path, bool]:
    """Write the driver env file at :data:`DRIVER_ENV_PATH`.

    Mirrors ``HermesDriver._write_env_file``: the systemd unit's
    ``EnvironmentFile=-/etc/hal0/agents/%i.env`` sources this into the
    ``hal0-agent@hermes`` process env on every start — the hal0 API URL, the
    MCP endpoint URLs, and (when a box service key is resolvable)
    ``HAL0_MCP_TOKEN``, the bearer :class:`hal0.agents.mcp_client.AgentMCPClient`
    resolves for in-platform MCP callers via ``auth.env`` in the seed TOML
    (:func:`_builtin_mcp_seed_servers`). Content is deterministic, so a
    hash-equal file is left untouched — a rotated key naturally changes the
    content and is picked up on the next bootstrap/``--repair`` run, same
    posture as the config.yaml bearer (:func:`_build_config_overlay`).

    Once this file can carry a live secret, its mode always lands ``0600``
    (root:root — read by pid1 as the unit's EnvironmentFile, never by the
    unprivileged hal0 user directly; see :func:`_build_hermes_env` in
    ``cli/agent_shim.py``, which forwards ``os.environ`` rather than
    re-reading the file). Auth OFF (no resolvable key) still writes the file
    — just without the token line — so a keyless/dev box never fails here.
    Returns ``(path, wrote)``.
    """
    from hal0.service_identity import service_key

    api_base = HAL0_API_URL.rstrip("/")
    lines = [
        "# hal0 — Hermes-Agent env (managed by hal0; safe to edit)",
        f"HAL0_API_URL={api_base}",
        f"HAL0_MCP_ADMIN_URL={api_base}/mcp/admin",
        f"HAL0_MCP_MEMORY_URL={api_base}/mcp/memory",
    ]
    token = service_key(prefer="admin")
    if token:
        lines.append(f"HAL0_MCP_TOKEN={token}")
    body = "\n".join(lines) + "\n"
    path = DRIVER_ENV_PATH
    if path.exists():
        try:
            unchanged = path.read_text(encoding="utf-8") == body
        except OSError:
            unchanged = False
        if unchanged:
            # Re-tighten perms even on a no-op content match — self-heals a
            # file written 0644 by an older build now that it may carry a
            # secret. geteuid()==0 only; the seam path re-asserts 0600 on
            # every write regardless (see installer/wrappers/hal0-agentenv).
            if os.geteuid() == 0:
                with contextlib.suppress(OSError):
                    path.chmod(0o600)
            return path, False
    if os.geteuid() == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".env.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
        with contextlib.suppress(OSError):
            os.chown(path, 0, 0)
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    else:
        # Driver env lives in root:root /etc/hal0/agents — delegate the write
        # to the seam (it builds the path from the validated agent name and
        # pins 0600 — see installer/wrappers/hal0-agentenv `write-driver-env`).
        _privileged_env_write("write-driver-env", body)
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
    # Born-hal0 even on a root-run bootstrap (O16a): normally the CLI drops to
    # hal0 before this pipeline, so runtime.json is born hal0:hal0. But a
    # bootstrap invoked directly as root (no drop) would strand a root:root 0600
    # runtime.json inside the hal0-owned HERMES_HOME — which the User=hal0
    # chat-proxy then can't READ (embed token lost) AND uninstall can't rmtree
    # (→ the O16 partial-removal). chown it to hal0 so the tree stays uniformly
    # service-owned. Best-effort: no hal0 account (dev) → left as-is.
    if os.geteuid() == 0:
        with contextlib.suppress(OSError, KeyError):
            os.chown(path, pwd.getpwnam("hal0").pw_uid, grp.getgrnam("hal0").gr_gid)
    return path, True


def _phase_install_artifacts(ctx: _StepCtx) -> PhaseResult:
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
    state = ctx.state
    repair = ctx.repair

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

    # ── root:root artifacts via the hal0-agentenv seam (§7.4 privilege split) ─
    # seed TOML + driver env live in root-owned /etc/hal0/agents. Both writers
    # are euid-aware: root writes directly (re-pinning root:root), a hal0-run
    # provisioner delegates the write to `sudo -n hal0-agentenv`.
    seed_path, seed_wrote = _write_seed_toml(state, repair=repair)
    env_path, env_wrote = _write_driver_env(state)
    # ── hal0-owned artifact — runtime.json (0600) is born hal0:hal0 (§7.4 F.7:
    #    provisioning runs as hal0, so the chat proxy can read it directly) ─────
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


# ── Linear convergent installer ──────────────────────────────────────────────
#
# The resumable-checkpoint pipeline (PHASES / Phase / PhaseContext / run /
# provision.json) was replaced by ONE linear pass: :func:`install_hermes`. Each
# step converges its slice of host state and reports whether it changed anything,
# so re-running is cheap and a second run over an already-provisioned box mutates
# nothing (the convergence contract). Idempotency comes from every write being a
# converging write (``hermes config set`` re-applies the same value; file writes
# hash-skip), not from stored checkpoints.
#
# RELOCATE(brain-lane) — LANDED: the brain/persona/memory-identity steps
# (persona_seed, namespace_register, brain_profile_seed, brain_profile_mcp_wire,
# self_report) no longer run as part of this linear pipeline. They moved into
# the hal0-api boot lifespan (src/hal0/api/__init__.py: _boot_seeds folds in
# persona_seed + brain_profile_mcp_wire; the new terminal _boot_brain_lane phase
# runs namespace_register, brain_profile_seed, self_report) so they execute on
# every API boot (fresh/update/dev) instead of once at install time. The step
# FUNCTIONS below stay put and reusable — the lifespan phases call them
# directly via the same InstallIO/_StepCtx injection seam the tests use, only
# swapping ``mcp_memory_call`` for a boot-safe in-process adapter (the HTTP
# loopback ``_mcp_memory_call`` below only works once uvicorn's socket is
# bound, which is never true during lifespan startup).


@dataclass(frozen=True)
class InstallIO:
    """Injectable IO seams :func:`install_hermes` touches.

    Defaults bind the real implementations, so ``InstallIO()`` is production
    behaviour; tests construct ``InstallIO(fetch_slots=fake, run=recorder, …)``
    to run hermetically and record the mutating calls a step makes.
    """

    http_get: Callable[..., int] = _http_get
    fetch_slots: Callable[[], list[dict[str, Any]]] = _fetch_slots
    fetch_model_contexts: Callable[[], dict[str, int]] = _fetch_model_contexts
    fetch_model_route_ready: Callable[[str], bool] = _fetch_model_route_ready
    probe_mcp_server: Callable[..., dict[str, Any]] = _probe_mcp_server
    mcp_memory_call: Callable[..., dict[str, Any]] = _mcp_memory_call
    install_venv: Callable[..., None] = _install_venv
    read_env_probe: Callable[[], dict[str, Any]] = _read_env_probe
    load_config: Callable[[], Any] = _load_hal0_config
    run: Callable[..., Any] = subprocess.run


@dataclass(frozen=True)
class _StepCtx:
    """What one install step body sees: the resolved target, the IO seams, the
    ``--repair`` flag, and read access to the details of already-run steps.

    Replaces the old ``PhaseContext``/``PhaseIO`` pair. ``output_of(name)``
    returns an earlier step's ``details`` (empty when it hasn't run) — no
    needs-graph enforcement, because linear order makes the reads self-evident.
    """

    state: BootstrapState
    io: InstallIO = field(default_factory=InstallIO)
    repair: bool = False
    _prior: dict[str, dict[str, Any]] = field(default_factory=dict)

    def output_of(self, name: str) -> dict[str, Any]:
        got = self._prior.get(name) or {}
        return got if isinstance(got, dict) else {}


@dataclass
class InstallStep:
    """One step's outcome in an :class:`InstallReport`.

    ``changed`` is the convergence signal: ``True`` iff the step altered
    persistent host state on this run. A converged re-run leaves every
    host-mutating step ``changed=False`` — that is what the double-run
    convergence test asserts.
    """

    name: str
    status: str  # "ok" | "skip" | "fail"
    changed: bool = False
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class InstallReport:
    """Aggregate outcome of one :func:`install_hermes` pass."""

    hermes_home: str
    venv: str
    agent_id: str
    repair: bool = False
    steps: list[InstallStep] = field(default_factory=list)

    def step(self, name: str) -> InstallStep | None:
        return next((s for s in self.steps if s.name == name), None)

    @property
    def failed(self) -> list[str]:
        return [s.name for s in self.steps if s.status == "fail"]

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def mutated(self) -> list[str]:
        """Names of the host-mutating steps that changed state this run.

        The brain-lane memory/persona publishes (RELOCATE(brain-lane)) no
        longer run as install steps at all — they moved to the hal0-api boot
        lifespan — so they never appear here either way; the convergence
        contract is about the on-disk install surface.
        """
        return [s.name for s in self.steps if s.changed]

    @property
    def converged(self) -> bool:
        """True when no host-mutating step changed anything (a no-op re-run)."""
        return not self.mutated


# RELOCATE(brain-lane) — LANDED: the brain-lane steps (persona/memory
# publishes) no longer appear in ``_INSTALL_STEPS`` at all (see the module
# docstring above ``InstallIO``), so the convergence contract never sees
# their names — the ``_BRAIN_LANE_STEPS`` exemption this function used to
# consult is retired along with them.
def _step_changed(name: str, result: PhaseResult) -> bool:
    """Derive a step's convergence signal from its :class:`PhaseResult`."""
    if result.status != PhaseStatus.OK:
        return False
    d = result.details or {}
    if name == "install_artifacts":
        return bool(d.get("seed_wrote") or d.get("env_wrote") or d.get("token_wrote"))
    return bool(d.get("changed"))


# The ordered pipeline. ``mcp_wire`` runs BEFORE ``config_write`` (was a
# cross-run checkpoint edge under the old machinery) so the render sees the live
# probe result directly. ``model_automap`` is gone — config_write already sets
# ``model.*`` + ``model_aliases.*`` (the post-bootstrap slot refresh is a runtime
# concern of ``render_live_context``). ``ownership_reconcile`` is gone (§7.4 F.7).
# RELOCATE(brain-lane) — LANDED: persona_seed, namespace_register,
# brain_profile_seed, brain_profile_mcp_wire, and self_report used to run
# here (each marked ``# RELOCATE(brain-lane):``); they now run in the
# hal0-api boot lifespan instead (see the module docstring above
# ``InstallIO``). The functions are unchanged and still directly callable
# (tests + the lifespan phases both use them) — only their membership in
# this pipeline moved.
_INSTALL_STEPS: tuple[tuple[str, Callable[[_StepCtx], PhaseResult]], ...] = (
    ("preflight", _phase_preflight),
    ("install", _phase_install),
    ("env_probe", _phase_env_probe),
    ("home_init", _phase_home_init),
    ("kanban_db_init", _phase_kanban_db_init),
    ("install_artifacts", _phase_install_artifacts),
    ("mcp_wire", _phase_mcp_wire),
    ("config_write", _phase_config_write),
    ("context_link", _phase_context_link),
    ("voice_wire", _phase_voice_wire),
    ("gateway_secrets_wire", _phase_gateway_secrets_wire),
    ("smoke_tests", _phase_smoke_tests),
)


def install_hermes(
    *,
    repair: bool = False,
    hermes_home: Path | str = HERMES_HOME_DEFAULT,
    venv: Path | str = HERMES_VENV_DEFAULT,
    agent_id: str = "hermes",
    io: InstallIO | None = None,
    state_root: Path | None = None,
    write_report: bool = True,
    verbose: bool = False,
) -> InstallReport:
    """Provision Hermes in one linear, convergent pass.

    resolve python → pinned SDK venv → plugin trees → apply the hal0 config keys
    (``hermes config set`` + targeted deep-merge, never a wholesale config.yaml
    rewrite) → render context files → install artifacts → wire the gateway
    secrets drop-in + API key → smoke test. Each step converges its slice and
    reports whether it changed anything; a second run over an already-provisioned
    box mutates nothing (``report.converged``).

    ``repair`` re-runs every step and, as root, first reconciles ownership drift
    (:func:`reconcile_ownership_on_repair`) so a root-clobbered tree is fixed
    before the converging writes rather than re-written on top of ``root:root``.

    The injectable knobs (``hermes_home``/``venv``/``agent_id``/``io``/
    ``state_root``) carry production defaults, so ``install_hermes(repair=...)``
    is the whole public contract; tests point them at a tmp tree.
    """
    io = io if io is not None else InstallIO()
    state = BootstrapState(hermes_home=str(hermes_home), venv=str(venv), agent_id=agent_id)

    # RATIFIED 2026-07-18 (inc-1 deliverable 6): `--repair` reconciles ownership
    # drift BEFORE the converging writes, so a root-clobbered HERMES_HOME/venv is
    # actually fixed — not re-written on top of root:root. No-op unless root.
    if repair:
        try:
            recon = reconcile_ownership_on_repair(enabled=os.geteuid() == 0, venv=Path(state.venv))
            if recon.reconciled and verbose:
                print(f"[repair] reconciled ownership: {', '.join(recon.reconciled)}")
        except (OSError, KeyError, subprocess.SubprocessError) as exc:
            log.warning("hermes_provision.repair_reconcile_failed", error=str(exc))

    # RATIFIED 2026-07-18 (inc-1 deliverable 5): scrub stale hal0-agent@ drop-in
    # debris (the 241/CONFIGURATION_DIRECTORY brick-loop class) BEFORE the unit is
    # (re)enabled, on every install. Root-only, convergent, best-effort.
    if os.geteuid() == 0:
        try:
            cleanup = cleanup_stale_agent_dropins()
            if cleanup.removed and verbose:
                print(f"[cleanup] removed stale agent drop-ins: {', '.join(cleanup.removed)}")
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("hermes_provision.stale_dropin_cleanup_failed", error=str(exc))

    report = InstallReport(
        hermes_home=state.hermes_home, venv=state.venv, agent_id=agent_id, repair=repair
    )
    prior: dict[str, dict[str, Any]] = {}
    for name, fn in _INSTALL_STEPS:
        if verbose:
            print(f"[run ] {name}")
        ctx = _StepCtx(state=state, io=io, repair=repair, _prior=prior)
        result = fn(ctx)
        prior[name] = result.details
        step = InstallStep(
            name=name,
            status=result.status.value,
            changed=_step_changed(name, result),
            reason=result.reason,
            details=result.details,
        )
        report.steps.append(step)
        if verbose and result.reason:
            print(f"       {result.status.value}: {result.reason}")

    if write_report:
        try:
            _write_run_report(report, state_root)
        except OSError as exc:
            log.warning("hermes_provision.run_report_write_failed", error=str(exc))
    return report


def _write_run_report(report: InstallReport, state_root: Path | None) -> None:
    """Persist a flat last-run report for ``hal0 agent status`` to render.

    This is NOT a resumable checkpoint (the linear installer has none) — just a
    snapshot of the most recent run keyed by step name, so ``agent status`` /
    ``agent log`` keep working without the retired provision.json machinery.
    """
    root = state_root if state_root is not None else _DEFAULT_STATE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    now = _utcnow()

    def _phase_entry(s: InstallStep) -> dict[str, Any]:
        # `failure_count` is derived generically from `details["failures"]`
        # (a list[str]) so any phase — not just smoke_tests — that records
        # non-fatal failures surfaces a count `hal0 agent status` can render
        # as its own column instead of forcing operators to parse the
        # (possibly truncated) Detail JSON blob (#1793).
        failures = s.details.get("failures") if isinstance(s.details, dict) else None
        skipped = s.details.get("skipped") if isinstance(s.details, dict) else None
        entry: dict[str, Any] = {
            "status": s.status,
            "at": now,
            "changed": s.changed,
        }
        if isinstance(failures, list) and failures:
            entry["failure_count"] = len(failures)
        if isinstance(skipped, list) and skipped:
            entry["skipped_count"] = len(skipped)
        if s.reason:
            entry["reason"] = s.reason
        if s.details:
            entry["details"] = s.details
        return entry

    data = {
        "hal0_version": _hal0_version_string(),
        "hermes_version": _hermes_version_pin(),
        "completed_at": now if report.ok else None,
        "repair": report.repair,
        "phases": {s.name: _phase_entry(s) for s in report.steps},
    }
    target = root / _STATE_FILE_NAME
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)


# ── CLI surface ──────────────────────────────────────────────────────────────


def bootstrap_cli(
    *,
    repair: bool,
    dry_run: bool = False,
    skip_phases: tuple[str, ...] = (),
    verbose: bool = False,
    state_root: Path | None = None,
) -> int:
    """CLI entry point — thin delegator to :func:`install_hermes`.

    Returns a POSIX exit code (0 = success, 1 = any step failed). The
    ``--adopt`` capture flag is retired (spec-retired, O14): the single-managed
    HERMES_HOME model means hal0 owns the tree by construction, so there is no
    foreign install to capture. ``skip_phases`` is a retired no-op kept in the
    signature so the existing ``hal0 agent bootstrap`` flags keep parsing;
    ``dry_run`` suppresses the last-run report write.
    """
    report = install_hermes(
        repair=repair,
        state_root=state_root,
        write_report=not dry_run,
        verbose=verbose,
    )
    if verbose:
        target = (state_root or _DEFAULT_STATE_ROOT) / _STATE_FILE_NAME
        print(f"state: {target}")
    if report.failed:
        print(f"bootstrap failed in steps: {', '.join(report.failed)}")
        return 1
    return 0


def _detect_foreign_gateways(**_kwargs: Any) -> list[dict[str, str]]:
    """Retired foreign-gateway scan — now a no-op shim.

    hal0's single-managed-gateway model dropped the capture/adopt path; the
    detector stays as an empty-returning shim only so the CLI gateway-install
    site (which best-effort-imports it) keeps working without an edit.
    """
    return []
