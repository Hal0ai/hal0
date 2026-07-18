"""Hermes-Agent bootstrap state machine (issue #238 scaffold).

Fifteen named phases run in a strict deterministic sequence. Each phase
is a function ``(PhaseContext) -> PhaseResult`` (#702): the context
carries a read-only :class:`BootstrapState` view, the ``--repair`` flag,
a :class:`PhaseIO` bundle of injectable IO seams, and ``output_of()``
for declared cross-phase checkpoint reads. Each phase writes a
checkpoint into ``provision.json``. On re-run the orchestrator loads
the checkpoint and skips any phase already marked ``ok`` unless
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

Born-owned contract (§7.4 F.7): every ``$HERMES_HOME`` write here is born
``hal0:hal0`` because the CLI drops provisioning to the hal0 service user before
running this pipeline (``cli/agent_commands._provision_hermes``: a root-only
prelude installs the ``/usr/local/bin`` wrapper + ensures the setgid hal0-owned
skeleton, then re-execs ``hal0 agent bootstrap hermes`` as hal0). Root:root
artifacts (seed TOML, driver env, gateway drop-in) go through the
``hal0-agentenv`` / ``hal0-systemctl`` sudo seams. Consequently the former
chown-back layers — ``_chown_tree_to_hal0`` and the ``ownership_reconcile``
phase, plus the CLI's ``_chown_hermes_trees_to_agent_user`` — are removed:
nothing is chowned after the fact.
"""

from __future__ import annotations

import contextlib
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 — needed to spawn python -m venv + pip
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

from hal0.agents import role_resolution

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
    # A fatal FAIL aborts the run: the orchestrator stops executing
    # subsequent phases and records them as skipped. Used by the capture
    # guards (an unclaimed foreign HERMES_HOME, a live foreign gateway) —
    # a normal FAIL stays run-all (fallbacks keep phases independent).
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status.value}
        if self.hash is not None:
            out["hash"] = self.hash
        if self.reason is not None:
            out["reason"] = self.reason
        if self.fatal:
            out["fatal"] = True
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
    agent_id: str = "hermes"
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
# Each phase signature: (ctx: PhaseContext) -> PhaseResult (#702).
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


def _stub(name: str) -> Callable[[PhaseContext], PhaseResult]:
    def _phase(ctx: PhaseContext) -> PhaseResult:
        return PhaseResult(status=PhaseStatus.OK, details={"stub": True})

    _phase.__name__ = f"_phase_{name}"
    _phase.__doc__ = f"Stub for {name!r} phase — real impl pending in a follow-up slice."
    return _phase


# Pinned constants — keep these in sync with installer/agents/hermes/
# requirements.txt and the wrapper script. The constants are exposed
# at module scope so tests can monkey-patch them onto a tmp path.
PYTHON_MIN = (3, 11)
# Exclusive cap for the hermes venv interpreter, mirroring hermes-agent's
# wheel metadata: every release since 0.16.0 pins `requires-python
# >=3.11,<3.14`. On a >=3.14 interpreter pip filters those wheels out and
# falls back to 0.15.2 — whose wheel is broken (imports
# hermes_cli.dashboard_auth, ships without the subpackage) — so 3.14 must be
# rejected, not merely deprioritized (#1248). Single source of truth for the
# resolver, the preflight message, and the stale-venv rebuild; relax it here
# when upstream ships 3.14 support (#1249).
PYTHON_MAX_EXCLUSIVE = (3, 14)
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


# ── Foreign-gateway detection (capture safety) ───────────────────────────────
#
# hal0 installs a SINGLE SYSTEM-scope hermes-gateway.service. A second poller
# on the same Telegram bot token — a hand-installed system unit, or (the real
# incident) a `systemctl --user` hermes-gateway under another user — means two
# long-polls on one token → Telegram HTTP 409 flapping. Preflight scans for
# such foreign pollers so a capture aborts (or, under --adopt, warns loudly)
# instead of silently starting a second one.

GATEWAY_UNIT_NAME = "hermes-gateway.service"
# User-scope systemd dirs to scan for a hermes-gateway unit file. hal0 never
# installs user-scope units, so any hit here is foreign by construction.
_USER_SYSTEMD_SCAN_GLOBS: tuple[str, ...] = (
    "/root/.config/systemd/user",
    "/home/*/.config/systemd/user",
)


def _user_from_systemd_dir(path: Path) -> str:
    """Best-effort owner name from a ``…/.config/systemd/user`` path."""
    parts = path.parts
    if "home" in parts:
        i = parts.index("home")
        if i + 1 < len(parts):
            return parts[i + 1]
    if "root" in parts:
        return "root"
    return ""


def _systemctl_is_active(unit: str, *, run: Callable[..., Any]) -> bool:
    """``systemctl is-active <unit>`` — True iff active. Best-effort (False on error)."""
    try:
        proc = run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
        )  # nosec B603 B607 — fixed argv
    except (OSError, subprocess.SubprocessError):
        return False
    rc = getattr(proc, "returncode", 1)
    out = (getattr(proc, "stdout", "") or "").strip()
    return rc == 0 or out == "active"


def _pgrep_hermes_gateway(*, run: Callable[..., Any]) -> list[str]:
    """``pgrep -af 'hermes.*gateway'`` matched command lines. Best-effort ([] on error)."""
    try:
        proc = run(
            ["pgrep", "-af", "hermes.*gateway"],
            capture_output=True,
            text=True,
            check=False,
        )  # nosec B603 B607 — fixed argv
    except (OSError, subprocess.SubprocessError):
        return []
    out = getattr(proc, "stdout", "") or ""
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _detect_foreign_gateways(
    *,
    run: Callable[..., Any] | None = None,
    scan_globs: tuple[str, ...] | None = None,
    dropin_file: Path | None = None,
) -> list[dict[str, str]]:
    """Scan for hermes-gateway pollers hal0 didn't install.

    Findings (each a dict with ``scope`` / ``detail`` / ``stop_cmd``):

      * ``scope="user"`` — a ``hermes-gateway.service`` file under any scanned
        user systemd dir. hal0 only ever installs SYSTEM units, so these are
        foreign by construction. ``stop_cmd`` is a ``systemctl --user`` line;
        hal0 does NOT auto-stop another user's unit.
      * ``scope="system"`` — the SYSTEM unit is active but hal0's own secrets
        drop-in is absent, i.e. it isn't the unit hal0 manages here.

    ``pgrep`` output is attached as corroborating evidence to the first
    finding (never a finding on its own, so hal0's own running gateway isn't
    mistaken for foreign). All probes are best-effort — a detection error
    never raises. ``run`` / ``scan_globs`` / ``dropin_file`` are injectable;
    each defaults are resolved at CALL time (not def time) so a test's
    monkeypatch of the module-level constants + the subprocess seam takes hold.
    """
    run = run if run is not None else subprocess.run
    globs = scan_globs if scan_globs is not None else _USER_SYSTEMD_SCAN_GLOBS
    findings: list[dict[str, str]] = []

    for pattern in globs:
        for d in glob.glob(pattern):
            unit = Path(d) / GATEWAY_UNIT_NAME
            if not unit.exists():
                continue
            user = _user_from_systemd_dir(Path(d))
            stop = f"systemctl --user disable --now {GATEWAY_UNIT_NAME}"
            if user:
                stop = (
                    f"sudo -u {user} XDG_RUNTIME_DIR=/run/user/$(id -u {user}) "
                    f"systemctl --user disable --now {GATEWAY_UNIT_NAME}"
                )
            findings.append(
                {
                    "scope": "user",
                    "unit": str(unit),
                    "detail": f"user-scope {GATEWAY_UNIT_NAME} at {unit}"
                    + (f" (user {user})" if user else ""),
                    "stop_cmd": stop,
                }
            )

    dropin = dropin_file if dropin_file is not None else GATEWAY_SYSTEMD_DROPIN_FILE
    if not dropin.exists() and _systemctl_is_active(GATEWAY_UNIT_NAME, run=run):
        findings.append(
            {
                "scope": "system",
                "unit": GATEWAY_UNIT_NAME,
                "detail": (
                    f"system-scope {GATEWAY_UNIT_NAME} is active but hal0's secrets "
                    f"drop-in ({dropin}) is absent — not the unit hal0 manages"
                ),
                "stop_cmd": f"systemctl disable --now {GATEWAY_UNIT_NAME}",
            }
        )

    if findings:
        procs = _pgrep_hermes_gateway(run=run)
        if procs:
            findings[0]["processes"] = "; ".join(procs)
    return findings


def _phase_preflight(ctx: PhaseContext) -> PhaseResult:
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

    # Foreign-gateway scan (capture safety). Best-effort — a detection error
    # must never crash preflight. A live foreign poller is a FATAL abort
    # without --adopt (two pollers on one Telegram token → 409 flapping);
    # with --adopt it's a loud warning (hal0 won't auto-stop another user's
    # unit) recorded for the CLI to surface.
    try:
        foreign = _detect_foreign_gateways(run=ctx.io.run)
    except Exception as exc:  # detection is strictly best-effort
        foreign = []
        details["foreign_gateway_probe_error"] = str(exc)
    details["foreign_gateways"] = foreign
    fatal = False
    if foreign:
        what = "; ".join(f["detail"] for f in foreign)
        cmds = " && ".join(f["stop_cmd"] for f in foreign)
        if ctx.adopt:
            details["foreign_gateway_warning"] = (
                f"foreign hermes gateway(s) detected: {what}. hal0 will NOT auto-stop "
                f"another user's unit — stop it yourself before the bot starts: {cmds}"
            )
        else:
            fatal = True
            failures.append(
                f"foreign hermes gateway(s) detected: {what} — a second poller on the "
                "same Telegram token means HTTP 409 flapping. Stop it: "
                f"{cmds} — then re-run, or re-run with --adopt (backs up the existing "
                f"install + imports its tokens). Operator overrides: {OVERRIDES_PATH}"
            )

    if failures:
        return PhaseResult(
            status=PhaseStatus.FAIL,
            details=details,
            reason="; ".join(failures),
            fatal=fatal,
        )
    return PhaseResult(status=PhaseStatus.OK, details=details)


# ── Phase B: install ────────────────────────────────────────────────────────


def _python_range_error() -> str:
    """Actionable failure text for hosts with no hermes-compatible Python."""
    lo = ".".join(str(p) for p in PYTHON_MIN)
    hi = f"{PYTHON_MAX_EXCLUSIVE[0]}.{PYTHON_MAX_EXCLUSIVE[1] - 1}"
    cap = ".".join(str(p) for p in PYTHON_MAX_EXCLUSIVE)
    return (
        f"no Python {lo}-{hi} interpreter found — hermes-agent wheels pin "
        f"`requires-python <{cap}`, so the hermes venv needs {lo}-{hi}. "
        f"Install one and re-run, e.g. `apt install python{hi} python{hi}-venv`; "
        f"on distros that ship only {cap}+ (Ubuntu 26.04) use the deadsnakes "
        f"PPA — or install uv (https://astral.sh/uv), and hal0 will provision "
        f"Python {UV_PYTHON_FALLBACK} itself"
    )


#: Minor version uv provisions when no system interpreter qualifies (#1250).
#: Newest supported release — keep inside [PYTHON_MIN, PYTHON_MAX_EXCLUSIVE).
UV_PYTHON_FALLBACK = "3.13"

#: Where uv-managed interpreters land. uv's default (~/.local/share/uv) is
#: under /root with mode 0700 when provisioning runs as root — but the hermes
#: venv executes as the ``hal0`` user via a symlinked base interpreter, which
#: would then be unreachable. A world-readable tree under /var/lib/hal0 keeps
#: the interpreter usable by the service and survives root-homedir cleanups.
UV_PYTHON_INSTALL_DIR = Path("/var/lib/hal0/python")


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
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(UV_PYTHON_INSTALL_DIR)}
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
    """Resolve a venv interpreter: system Python first, uv-managed as fallback.

    A qualifying system interpreter always wins — the uv download only fires
    when PATH and the running interpreter both fail the range check, so hosts
    with a packaged 3.11-3.13 never pull a managed build (#1250).
    """
    found = _resolve_supported_python(prober, running=running)
    if found is not None:
        return found
    return _provision_python_via_uv(prober, runner)


def _resolve_supported_python(
    prober: Callable[[str], str | None] = shutil.which,
    *,
    running: tuple[int, int] | None = None,
) -> str | None:
    """Find an interpreter in hermes-agent's supported range, newest first.

    Probes explicit ``python3.13`` → ``python3.11`` binaries on PATH so the
    venv pins its minor version regardless of what ``sys.executable`` is.
    Falls back to the running interpreter only when it is itself inside the
    range — NOT merely ``>= 3.11``: on a Python-3.14-only host (Ubuntu 26.04)
    the old fallback built the venv on 3.14, where pip filters out every
    hermes-agent 0.16+ wheel (``requires-python <3.14``) and resolution
    lands on the broken 0.15.2 build (#1248).
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

    Two-step: ``python3.x -m venv`` then ``pip install -r``. The venv itself
    is stdlib-built — uv enters only inside the resolver, as the last-resort
    interpreter fetch on hosts with no packaged 3.11-3.13 (#1250).
    """
    py = python_resolver()
    if py is None:
        raise RuntimeError(_python_range_error())
    venv.parent.mkdir(parents=True, exist_ok=True)
    existing_minor = _venv_python_minor(venv) if venv.exists() else None
    if existing_minor is not None and not (PYTHON_MIN <= existing_minor < PYTHON_MAX_EXCLUSIVE):
        # A previous run built this venv on an unsupported interpreter (the
        # pre-guard fallback happily used 3.14). pip-installing into it can
        # never converge — every supported hermes-agent wheel is filtered out
        # by requires-python — so rebuild on the resolved interpreter. Venv
        # holds packages only; $HERMES_HOME state is untouched.
        shutil.rmtree(venv)
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
        env = {**os.environ, "HERMES_HOME": str(hermes_home)}
        runner.run([str(hermes_bin), "config", "migrate"], check=True, env=env)  # nosec B603
        migrated = True
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("hermes_provision.config_migrate_failed", error=str(exc))

    target = version or "latest matching requirements"
    suffix = " + config migrated" if migrated else " (config migrate skipped — see logs)"
    return True, f"hermes-agent upgraded → {target}{suffix}"


# Content marker that identifies the hal0-managed wrapper. The managed
# wrapper (installer/wrappers/hermes) always injects HAL0_AGENT_ID; a
# hand-installed upstream ``hermes`` (a real binary, or a different shim)
# never does. Used to decide whether a pre-existing /usr/local/bin/hermes
# is ours (overwrite freely) or foreign (back it up before clobbering).
_MANAGED_WRAPPER_MARKER = "HAL0_AGENT_ID"


def _is_hal0_managed_wrapper(path: Path) -> bool:
    """Whether ``path`` is a hal0-managed wrapper (contains the marker).

    Reads with ``errors="ignore"`` so a real ELF ``hermes`` binary doesn't
    raise — it simply won't contain the marker and is treated as foreign.
    """
    try:
        return _MANAGED_WRAPPER_MARKER in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _copy_wrapper(wrapper_src: Path, wrapper_dst: Path) -> None:
    """Copy + chmod the wrapper into ``wrapper_dst`` — euid-aware (§7.4).

    ``/usr/local/bin/hermes`` is root-only install infra. On a system install
    ``install.sh``'s root prelude lays it down BEFORE dropping the provisioner to
    the hal0 user, so:

    * root (euid==0) installs it here directly (with capture backup, below);
    * a non-root (hal0) caller finds it already present (prelude-installed) and
      skips — it cannot write ``/usr/local/bin``. If it is somehow absent we log
      and continue rather than aborting the whole bootstrap: the wrapper is
      root-owned infra the prelude owns, not a hal0-writable artifact.

    Capture safety (root only): when a pre-existing ``wrapper_dst`` is NOT a
    hal0-managed wrapper (a hand-installed upstream ``hermes`` on PATH), copy it
    aside to ``<dst>.pre-hal0`` before overwriting so the foreign entry point is
    recoverable. Overwriting our own wrapper (the steady-state re-run) skips the
    backup.
    """
    if os.geteuid() != 0:
        if not wrapper_dst.exists():
            log.warning("hermes_provision.wrapper_absent_nonroot", dst=str(wrapper_dst))
        return
    wrapper_dst.parent.mkdir(parents=True, exist_ok=True)
    if wrapper_dst.exists() and not _is_hal0_managed_wrapper(wrapper_dst):
        backup = wrapper_dst.with_name(wrapper_dst.name + ".pre-hal0")
        with contextlib.suppress(OSError):
            shutil.copy2(wrapper_dst, backup)
    shutil.copy2(wrapper_src, wrapper_dst)
    wrapper_dst.chmod(0o755)


def _install_backcompat_symlink(target: Path, link: Path) -> None:
    """Point ``link`` -> ``target`` (idempotent), replacing any prior file.

    Used to make the legacy ``hal0-hermes`` entry point a symlink to the
    canonical ``hermes`` wrapper. A pre-existing regular file (an older
    install's copied wrapper) is replaced so the two never drift.

    euid-aware (§7.4): both link + target live in root-only ``/usr/local/bin``,
    so a non-root (hal0) caller skips — ``install.sh``'s root prelude lays the
    symlink down alongside the wrapper before dropping to hal0.
    """
    if os.geteuid() != 0:
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if os.readlink(link) == str(target):
            return
        link.unlink()
    elif link.exists():
        link.unlink()
    os.symlink(str(target), str(link))


def _install_cli_wrapper(wrapper_src: Path) -> dict[str, Any]:
    """Install the root-only ``/usr/local/bin`` CLI infra — the genuinely-root
    slice of the install phase (§7.4 privilege split).

    Both entries live in root-owned ``/usr/local/bin``: the canonical
    ``hermes`` wrapper and the ``hal0-hermes`` back-compat symlink. When the
    provisioner runs as hal0 (§7.4 drop-to-hal0), install.sh's root prelude has
    already laid these down, so ``_copy_wrapper`` / ``_install_backcompat_symlink``
    detect non-root and skip. Grouping them in one helper keeps the phase body
    a clean two-part split — root-only infra here, hal0-owned artifacts after.

    Raises ``OSError`` on a genuine root-context write failure so the caller can
    surface a sudo hint.
    """
    _copy_wrapper(wrapper_src, HERMES_CLI_INSTALL_PATH)
    _install_backcompat_symlink(HERMES_CLI_INSTALL_PATH, WRAPPER_INSTALL_PATH)
    return {
        "hermes_cli": str(HERMES_CLI_INSTALL_PATH),
        "wrapper": str(WRAPPER_INSTALL_PATH),
    }


def _copy_plugin_tree(src: Path, dst: Path) -> None:
    """Mirror a plugin directory (idempotent)."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _phase_install(ctx: PhaseContext) -> PhaseResult:
    """Provision the managed Hermes venv + wrapper + plugin stubs.

    The plugin package at ``installer/agents/hermes/plugins/hal0-memory/`` (the
    canonical, shipped ``MemoryProvider`` source — see
    ``tests/agents/test_hal0_memory_client.py`` for its contract tests) is
    copied into ``$HERMES_HOME/plugins/hal0-memory/``.
    The legacy ``hal0`` model-provider plugin was removed (R4 H4): it
    hardcoded ``base_url=http://127.0.0.1:8000/api/v1`` which has no
    listener, and the direct-read composite model catalogue in
    :mod:`hal0.api` (``_fetch_hal0_composite_models``) now supersedes it.

    Skips heavy work when the venv binary already exists at the
    expected version — re-runs of ``hal0 agent bootstrap hermes`` are
    cheap unless ``--repair`` forces re-install.
    """
    state = ctx.state
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

    # Claim HERMES_HOME BEFORE any mutation (venv build, wrapper swap, plugin
    # copy). An unclaimed foreign home without --adopt is a true no-op abort —
    # we must NOT build the venv or swap /usr/local/bin/hermes and only then
    # bail. On --adopt this also runs the backup + token import up front, so the
    # marker is stamped before install populates the tree with plugin dirs.
    hermes_home = Path(state.hermes_home)
    claimed, reason, adopt_details = _claim_hermes_home(hermes_home, adopt=ctx.adopt)
    if not claimed:
        return PhaseResult(status=PhaseStatus.FAIL, reason=reason, fatal=True)
    if adopt_details is not None:
        details["adopted"] = adopt_details

    # ── root-only CLI infra (§7.4 privilege split) ──────────────────────────
    # Canonical entry point /usr/local/bin/hermes + the hal0-hermes back-compat
    # symlink. Both live in root-owned /usr/local/bin and are euid-guarded: root
    # installs them, a hal0-run provisioner finds them prelude-installed and
    # skips. install.sh's §7.4 root prelude owns them for the drop-to-hal0 path.
    try:
        details.update(_install_cli_wrapper(hermes_wrapper_src))
    except OSError as exc:
        # Non-root operators without the prelude land here — surface so they can sudo.
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"wrapper install to {HERMES_CLI_INSTALL_PATH} failed: {exc}",
            details=details,
        )

    # ── hal0-owned artifacts (born hal0:hal0 when the provisioner runs as hal0) ─
    hermes_bin = _venv_python(venv).parent / "hermes"
    if not hermes_bin.exists():
        try:
            ctx.io.install_venv(venv, requirements)
        except (subprocess.SubprocessError, RuntimeError, OSError) as exc:
            return PhaseResult(
                status=PhaseStatus.FAIL,
                reason=f"venv install failed: {exc}",
                details=details,
            )
    details["venv"] = str(venv)
    details["hermes_bin"] = str(hermes_bin)

    # Plugin stubs into HERMES_HOME-shaped locations. Real bodies in #241/#242.
    # HERMES_HOME was already claimed (marker stamped) above, before any
    # mutation — install populates it with plugin dirs below.
    plugin_targets = {
        "hal0-memory": hermes_home / "plugins" / "hal0-memory",
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

    # No chown-back (§7.4 F.7): provisioning runs as hal0 (cli/_provision_hermes
    # drops before this pipeline), so the venv under /var/lib/hal0/venvs is born
    # hal0:hal0.
    return PhaseResult(status=PhaseStatus.OK, details=details)


# ── Phase D: home_init ──────────────────────────────────────────────────────


_HAL0_MANAGED_MARKER = ".hal0-managed"

# Secret-key prefixes lifted from a foreign HERMES_HOME/.env into hal0's
# outbound vault on ``--adopt`` (prefix match). Everything else in the old
# .env stays put — we only import the platform/provider credentials the
# captured bot needs to keep polling.
_ADOPT_SECRET_PREFIXES: tuple[str, ...] = (
    "TELEGRAM_",
    "DISCORD_",
    "OPENROUTER_",
    "OPENAI_",
    "ANTHROPIC_",
    "FAL_",
    "HERMES_",
)


def _home_is_foreign(hermes_home: Path) -> bool:
    """True when HERMES_HOME is populated but not yet hal0-managed.

    That's the "capture an existing hermes install" case — a tree an
    operator (or older tooling) created by hand, lacking the
    ``.hal0-managed`` marker.
    """
    marker = hermes_home / _HAL0_MANAGED_MARKER
    return hermes_home.exists() and not marker.exists() and any(hermes_home.iterdir())


def _parse_env_secrets(env_file: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from ``env_file``, keeping recognized secrets.

    Only keys whose name prefix-matches :data:`_ADOPT_SECRET_PREFIXES` are
    returned. Tolerates ``export KEY=…`` and blank/comment lines; a missing
    or unreadable file yields ``{}``.
    """
    if not env_file.is_file():
        return {}
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, val = s.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if any(key.startswith(p) for p in _ADOPT_SECRET_PREFIXES):
            out[key] = val.strip()
    return out


def _adopt_foreign_home(hermes_home: Path) -> dict[str, Any]:
    """Back up a foreign HERMES_HOME + import its tokens before hal0 claims it.

    Three things, in order (the marker is stamped by the caller afterwards):

      1. Copy the whole tree to ``<home>.pre-hal0-<UTC>`` (perms preserved) so
         the operator's original install is fully recoverable.
      2. Snapshot ``config.yaml`` → ``config.yaml.pre-hal0`` and ``SOUL.md`` →
         ``SOUL.md.pre-hal0`` inside the home for a quick side-by-side diff.
      3. Import recognized secret keys from the old ``.env`` into hal0's
         outbound vault (:data:`HERMES_SECRETS_ENV`) via :func:`_merge_env_file`
         so the captured bot keeps its Telegram/Discord/provider tokens. The
         original ``.env`` is left untouched.

    Returns a details dict for the phase checkpoint.
    """
    ts = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d%H%M%S")
    backup_dir = hermes_home.parent / f"{hermes_home.name}.pre-hal0-{ts}"
    details: dict[str, Any] = {"backup_dir": str(backup_dir)}
    with contextlib.suppress(FileExistsError):
        shutil.copytree(hermes_home, backup_dir, symlinks=True)

    config = hermes_home / "config.yaml"
    if config.is_file():
        snap = hermes_home / "config.yaml.pre-hal0"
        with contextlib.suppress(OSError):
            shutil.copy2(config, snap)
        details["config_snapshot"] = str(snap)
    soul = hermes_home / "SOUL.md"
    if soul.is_file():
        snap = hermes_home / "SOUL.md.pre-hal0"
        with contextlib.suppress(OSError):
            shutil.copy2(soul, snap)
        details["soul_snapshot"] = str(snap)

    tokens = _parse_env_secrets(hermes_home / ".env")
    if tokens:
        _merge_env_file(HERMES_SECRETS_ENV, tokens)
        if os.geteuid() == 0:
            with contextlib.suppress(OSError):
                os.chown(HERMES_SECRETS_ENV, 0, 0)
    details["tokens_imported"] = sorted(tokens)
    return details


def _unclaimed_home_reason(hermes_home: Path) -> str:
    """The refusal message when a foreign HERMES_HOME is captured without --adopt."""
    return (
        f"{hermes_home} exists and is not hal0-managed (missing {_HAL0_MANAGED_MARKER}). "
        "Re-run with --adopt to back it up + import its tokens into the vault, or move "
        f"it aside before re-running. Operator overrides live at {OVERRIDES_PATH}."
    )


def _claim_hermes_home(
    hermes_home: Path, *, adopt: bool = False
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Stamp the ``.hal0-managed`` marker — refuse (or adopt) a foreign tree.

    Returns ``(claimed, reason, adopt_details)``:

      * empty / already-managed home → ``(True, None, None)`` (stamp + go).
      * populated + unmarked home without ``adopt`` → ``(False, reason, None)``;
        the caller turns this into a FATAL abort.
      * populated + unmarked home with ``adopt`` → back it up + import tokens,
        stamp the marker, ``(True, None, details)``.

    Used by both install (which writes plugins into the tree) and home_init
    (which makes the layout canonical).
    """
    marker = hermes_home / _HAL0_MANAGED_MARKER
    adopt_details: dict[str, Any] | None = None
    if _home_is_foreign(hermes_home):
        if not adopt:
            return (False, _unclaimed_home_reason(hermes_home), None)
        adopt_details = _adopt_foreign_home(hermes_home)
    hermes_home.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        marker.write_text(
            "hal0 — this HERMES_HOME is managed by hal0 (issue #240). Edits may be overwritten.\n",
            encoding="utf-8",
        )
    return (True, None, adopt_details)


def mark_home_managed_if_owned(hermes_home: Path) -> bool:
    """Stamp ``.hal0-managed`` on a HERMES_HOME hal0 itself owns.

    The hal0-api lifespan seeds default personas into HERMES_HOME on every
    start (fresh install + post-update convergence). On a FRESH box that seed
    populates ``/var/lib/hal0/.hermes`` BEFORE ``hal0 agent install hermes``
    ever runs — so the bootstrap's home-claim guard (:func:`_home_is_foreign`)
    would then mistake hal0's OWN seeded personas for a pre-existing foreign
    tree and fatal-abort every phase with "unclaimed HERMES_HOME". Stamping the
    marker at seed time (BEFORE the personas land) makes the home unambiguously
    hal0-managed so provisioning proceeds without ``--adopt``.

    A genuinely foreign tree (populated + unmarked already at call time — an
    operator's hand-made install) is deliberately left untouched so capture
    still routes through ``--adopt`` + its backup/token-import. Returns ``True``
    when the marker is present afterwards (freshly stamped or already there),
    ``False`` when the home was foreign and was intentionally not claimed.

    MUST be called before any content is seeded into the home — once personas
    land, a not-yet-marked home is indistinguishable from a foreign one.
    """
    claimed, _reason, _adopt_details = _claim_hermes_home(hermes_home, adopt=False)
    return claimed


def _phase_home_init(ctx: PhaseContext) -> PhaseResult:
    """Make the ``$HERMES_HOME`` layout canonical.

    Install (#240's first phase) already claimed the marker; home_init
    is responsible for the wider directory tree Hermes expects.
    Re-claiming via :func:`_claim_hermes_home` is harmless when install
    already did so, and necessary when home_init runs first
    (``--skip-phase install``).
    """
    hermes_home = Path(ctx.state.hermes_home)
    claimed, reason, adopt_details = _claim_hermes_home(hermes_home, adopt=ctx.adopt)
    if not claimed:
        return PhaseResult(status=PhaseStatus.FAIL, reason=reason, fatal=True)

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

    # No chown-back (§7.4 F.7): the tree is created as hal0 (the CLI drops
    # provisioning to hal0 before this pipeline) under the setgid /var/lib/hal0,
    # so the whole $HERMES_HOME layout is born hal0:hal0.
    details: dict[str, Any] = {
        "hermes_home": str(hermes_home),
        "marker": str(hermes_home / _HAL0_MANAGED_MARKER),
    }
    if adopt_details is not None:
        details["adopted"] = adopt_details
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


def _phase_env_probe(ctx: PhaseContext) -> PhaseResult:
    """Capture a host-environment snapshot for downstream phases.

    Writes the snapshot to ``$HERMES_HOME/env-<ts>.json`` AND keeps a
    pointer in ``provision.json``. Snapshot is overwritten on every
    re-run because it's a point-in-time view, not a checkpoint.
    """
    snapshot = ctx.io.read_env_probe()
    ts = _utcnow().replace(":", "").replace("-", "")
    hermes_home = Path(ctx.state.hermes_home)
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
            "timeout": 30,
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
    memory_provider: str = "hal0-memory",
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

    ``memory_provider`` is the resolved hermes ``memory.provider`` value
    (:func:`_resolve_memory_provider`) — ``"hal0-memory"`` (hindsight,
    default) or ``"honcho"``. The honcho.json wiring itself is a separate
    render (:func:`_render_honcho_json`); this only sets the scalar
    ``memory.*`` keys hermes's own config set can express.
    """
    base_url = "http://127.0.0.1:8080/v1" if live_resolve_enabled else primary["backend_url"]
    pairs: list[tuple[str, Any]] = []

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
    # hermes's config.yaml was dead config. Dropped for both branches rather
    # than ported to honcho (feat/honcho-memory).
    pairs += [
        ("memory.provider", memory_provider),
        ("memory.memory_enabled", True),
        ("memory.user_profile_enabled", True),
        ("memory.nudge_interval", 10),
    ]

    # mcp_servers via config set (NOT `hermes mcp add` — interactive/hangs).
    # No Bearer; agent identity flows via X-hal0-Agent.
    for srv in mcp_servers:
        name = srv["name"]
        pairs += [
            (f"mcp_servers.{name}.type", srv.get("type", "http")),
            (f"mcp_servers.{name}.url", srv["url"]),
            (f"mcp_servers.{name}.headers.X-hal0-Agent", agent_id),
            (f"mcp_servers.{name}.timeout", srv.get("timeout", 60)),
        ]
        if srv.get("private"):
            pairs.append((f"mcp_servers.{name}.headers.X-hal0-Private", "1"))

    pairs.append(("skills.creation_nudge_interval", 15))
    pairs += [("terminal.backend", "local"), ("terminal.cwd", "/etc/hal0")]
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
    env = {**os.environ, "HERMES_HOME": str(hermes_home)}
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
    env = {**os.environ, "HERMES_HOME": str(hermes_home)}
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


# ── Honcho memory-provider routing (feat/honcho-memory) ─────────────────────
#
# hal0.toml's [memory] agent_providers/agent_private + [honcho] sections
# (schema.py) let an operator route a given agent's memory onto Honcho
# instead of hal0's own Hindsight engine. _resolve_memory_provider picks the
# hermes-side provider string; _render_honcho_json / _disable_honcho_hermes_host
# keep $HERMES_HOME/honcho.json — the file hermes's BUILT-IN honcho provider
# reads directly — converged with that choice.


def _load_hal0_config() -> Any:
    """Load hal0.toml. Late import mirrors :func:`_read_env_probe`'s posture
    (keeps this module importable independent of hal0.config's own import
    graph). A missing/unreadable config.toml resolves to schema defaults —
    see :func:`hal0.config.loader.load_hal0_config`.
    """
    from hal0.config.loader import load_hal0_config

    return load_hal0_config()


def _resolve_memory_provider(agent_id: str, cfg: Any) -> str:
    """Map ``cfg.memory.agent_providers[agent_id]`` to hermes's ``memory.provider``.

    Absent ``agent_id``, or any value other than ``"honcho"``, resolves to
    the default hindsight routing → hermes's built-in ``hal0-memory`` plugin.
    Duck-typed attribute/dict access so tests can hand a bare
    ``SimpleNamespace`` instead of constructing a full ``Hal0Config``.
    """
    agent_providers = getattr(getattr(cfg, "memory", None), "agent_providers", None) or {}
    # Legacy provision.json state carries agent_id="hermes-agent" (pre-#1056);
    # the toggle is keyed by the canonical registry name ("hermes").
    canonical = agent_id.removesuffix("-agent") or agent_id
    choice = agent_providers.get(agent_id) or agent_providers.get(canonical, "hindsight")
    return "honcho" if choice == "honcho" else "hal0-memory"


def _render_honcho_json(hermes_home: Path, cfg: Any, agent_id: str) -> bool:
    """Deep-merge hal0's Honcho wiring into ``$HERMES_HOME/honcho.json``.

    Hermes's built-in honcho provider reads this file directly: root
    ``apiKey``/``baseUrl`` + per-host ``hosts.<key>`` blocks. A stale file
    left over from an earlier Honcho-CLOUD setup (``apiKey: hch-...``, no
    ``baseUrl``) is deterministically overwritten on the keys hal0 manages;
    any unknown key (an operator's hand edit, or a hermes feature hal0
    doesn't know about) is preserved — same deep-merge posture as
    :func:`_merge_config_yaml_layers` uses for config.yaml.

    Any EXISTING ``hosts.hermes_<profile>`` block (hermes profile host keys)
    also gets its ``workspace``/``peerName``/``aiPeer`` corrected — same
    brain, same identity, just a different session partition — but no NEW
    profile block is invented here; only present ones are touched.

    ``agent_id`` private (``cfg.memory.agent_private[agent_id]``) routes the
    host to an isolated ``<workspace>__private__<agent_id>`` workspace
    instead of the unified one. Returns True iff the file's content changed.
    """
    path = hermes_home / "honcho.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            existing = loaded

    private = bool((getattr(cfg.memory, "agent_private", None) or {}).get(agent_id, False))
    workspace = f"{cfg.honcho.workspace}__private__{agent_id}" if private else cfg.honcho.workspace
    managed_host_keys: dict[str, Any] = {
        "workspace": workspace,
        "peerName": cfg.honcho.user_peer,
        "aiPeer": agent_id,
    }

    hosts_overlay: dict[str, Any] = {
        "hermes": {
            "enabled": True,
            **managed_host_keys,
            "sessionStrategy": "per-session",
            "pinUserPeer": True,
            "saveMessages": True,
        }
    }
    existing_hosts = existing.get("hosts")
    if isinstance(existing_hosts, dict):
        for key in existing_hosts:
            if key != "hermes" and key.startswith("hermes_"):
                hosts_overlay[key] = dict(managed_host_keys)

    overlay = {
        "baseUrl": f"http://127.0.0.1:{cfg.honcho.port}",
        "apiKey": "hal0-local-noauth",
        "hosts": hosts_overlay,
    }
    merged = _deep_merge(existing, overlay)
    out = json.dumps(merged, indent=2, sort_keys=False) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == out:
        return False
    _atomic_write(path, out)
    return True


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


_HONCHO_SDK_SPEC = "honcho-ai>=2.1,<3"


def _ensure_honcho_sdk(venv: Path, *, run: Callable[..., Any]) -> bool:
    """Best-effort ``pip install --upgrade`` of the honcho-ai SDK in the hermes venv.

    Hermes's built-in honcho provider already works against the 2.0.1 the
    base requirements.txt installs; this is hygiene only (picks up the 2.1+
    line), so a failure (offline box, resolver hiccup) is logged and
    swallowed — never fails config_write.
    """
    pip = _venv_python(venv)
    if not pip.exists():
        return False
    try:
        run(
            [str(pip), "-m", "pip", "install", "--upgrade", _HONCHO_SDK_SPEC],
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603 — argv from local config
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("hermes_provision.honcho_sdk_upgrade_failed", error=str(exc))
        return False


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


def _phase_config_write(ctx: PhaseContext) -> PhaseResult:
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

    cfg = ctx.io.load_config()
    memory_provider = _resolve_memory_provider(state.agent_id, cfg)

    hermes_home.mkdir(parents=True, exist_ok=True)
    # Snapshot an EXISTING config.yaml to a single rolling ``config.yaml.bak``
    # BEFORE the first mutation (migrate + the config-set overlay), so a
    # repair-revert of hand-edits is recoverable. Distinct from the one-shot
    # ``config.yaml.pre-hal0`` an --adopt capture writes.
    if config_path.is_file():
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
    # Probe-driven mcp_servers (needs_previous: mcp_wire runs AFTER us, so this
    # only ever sees a persisted prior-run checkpoint). Fall back to the
    # builtin inventory on the very first pass — re-applied idempotently next run.
    cached_servers = ctx.output_of("mcp_wire").get("rendered_servers")
    have_probed = isinstance(cached_servers, list) and bool(cached_servers)
    mcp_servers = cached_servers if have_probed else _default_mcp_servers()
    # #702: silent fallbacks stay observable.
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
        memory_provider=memory_provider,
    )
    applied, errors = _apply_config_set(
        pairs, hermes_bin=hermes_bin, hermes_home=hermes_home, run=run
    )
    list_merge_changed = _merge_config_yaml_layers(
        config_path, list_keys=HAL0_CONFIG_LIST_KEYS, overrides_path=OVERRIDES_PATH
    )

    # honcho.json: hermes's built-in honcho provider reads this file
    # directly (config set can't reach it — it's not part of config.yaml).
    # Routed-to-honcho renders/enables it; routed-to-hindsight disables a
    # stale one in place rather than deleting it (keeps a --repair back-flip
    # to honcho cheap, and never destroys an operator's honcho.json by hand).
    honcho_sdk_upgraded = False
    if memory_provider == "honcho":
        honcho_json_changed = _render_honcho_json(hermes_home, cfg, state.agent_id)
        honcho_sdk_upgraded = _ensure_honcho_sdk(Path(state.venv), run=run)
    else:
        honcho_json_changed = _disable_honcho_hermes_host(hermes_home)

    new_hash = (
        content_hash(config_path.read_text(encoding="utf-8")) if config_path.exists() else None
    )
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
            "memory_provider": memory_provider,
            "honcho_json_changed": honcho_json_changed,
            "honcho_sdk_upgraded": honcho_sdk_upgraded,
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


def _phase_mcp_wire(ctx: PhaseContext) -> PhaseResult:
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
        probe = ctx.io.probe_mcp_server(
            entry["url"], agent_id=state.agent_id, private=entry["private"]
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
# Seeds the operator-visible personas hal0 manages on top of Hermes's own
# personality slot. Two personas land on first install — ``hermes``
# (default, helpful) and ``hal0-brain`` (the dashboard agent chat's
# platform steward). Operator edits survive re-runs; ``--repair``
# re-writes the seeds back to their canonical content. The active pointer
# flips to ``hermes`` only when missing or dangling — an operator-chosen
# active persona survives re-seed.


def _phase_persona_seed(ctx: PhaseContext) -> PhaseResult:
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


def _phase_context_link(ctx: PhaseContext) -> PhaseResult:
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
        live = render_live_context(
            hermes_home=hermes_home,
            slots_fetcher=ctx.io.fetch_slots,
            contexts_fetcher=ctx.io.fetch_model_contexts,
            health_probe=ctx.io.http_get,
        )
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


def _phase_namespace_register(ctx: PhaseContext) -> PhaseResult:
    """Write the Hermes identity card to the `agents` memory dataset.

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


def _phase_brain_profile_seed(ctx: PhaseContext) -> PhaseResult:
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
    private (its own 3-tier bank). google_workspace/hal0-browser are
    deliberately NOT included — the steward gets platform control + memory only.
    """
    from hal0.agents.personas import BRAIN_PROFILE_AGENT_ID

    return {
        "hal0-admin": {
            "type": "http",
            "url": "http://127.0.0.1:8080/mcp/admin/mcp",
            "headers": {"X-hal0-Agent": BRAIN_PROFILE_AGENT_ID},
            "timeout": 60,
        },
        "hal0-memory": {
            "type": "http",
            "url": "http://127.0.0.1:8080/mcp/memory/mcp",
            "headers": {"X-hal0-Agent": BRAIN_PROFILE_AGENT_ID, "X-hal0-Private": 1},
            "timeout": 30,
        },
    }


def _phase_brain_profile_mcp_wire(ctx: PhaseContext) -> PhaseResult:
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
_DELEGATION_SLOT_NAME = "agent"
_UTILITY_SLOT_NAME = "utility"


# The role→slot resolution primitives + policy now live in one shared module
# (``hal0.agents.role_resolution``) so the provision-time render below and the
# runtime ``GET /api/agents/{agent_id}/role-slots`` endpoint resolve roles
# identically. These aliases preserve the private names this module's callers
# and tests already use.
_find_named_ready_slot = role_resolution.find_named_ready_slot
_has_ready_npu_llm_slot = role_resolution.has_ready_npu_llm_slot


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
    provider to "custom". Delegates to
    :func:`hal0.agents.role_resolution.build_delegation`.
    """
    return role_resolution.build_delegation(slots, hal0_base_url=hal0_base_url)


def _resolve_auxiliary_tasks(
    slots: list[dict[str, Any]],
    *,
    hal0_base_url: str,
) -> dict[str, dict[str, Any]]:
    """Build the ``auxiliary_tasks`` template dict (task → {provider, model, base_url}).

    vision/web_extract always render as provider:"main" (no dedicated
    slot). The compaction/search/title group routes to the ``utility``
    slot when it's live; if that slot is missing the group falls back to
    the NPU llm slot (``hal0/npu``) and then to provider:"main" so
    side-tasks inherit the chat model rather than breaking. Resolution
    keys off the slot NAME (``utility``) and sends the slot's model_id —
    swapping the slot's model flows through on the next ``--repair``.
    Delegates to :func:`hal0.agents.role_resolution.build_auxiliary_tasks`.
    """
    return role_resolution.build_auxiliary_tasks(
        slots,
        hal0_base_url=hal0_base_url,
        main_tasks=_MAIN_AUX_TASKS,
        utility_tasks=_UTILITY_AUX_TASKS,
    )


def _phase_model_automap(ctx: PhaseContext) -> PhaseResult:
    """Refresh ``model.*`` + ``model_aliases.*`` via ``hermes config set``.

    Re-applies the model wiring + per-slot aliases so a post-bootstrap slot
    change (churn, a newly-loaded slot) lands in the hermes-owned config
    without a full re-render. ``config set`` is idempotent, so a no-drift run
    just re-writes the same values.

    Embed/rerank/img slots are deliberately NOT mapped
    (Hermes has no top-level embed abstraction; memory MCP handles it).
    """
    state = ctx.state
    hermes_home = Path(state.hermes_home)
    config_path = hermes_home / "config.yaml"
    if not config_path.exists():
        return PhaseResult(
            status=PhaseStatus.FAIL,
            reason=f"{config_path} missing — config_write must run first",
        )

    hermes_bin = _hermes_bin(Path(state.venv))
    slots = ctx.io.fetch_slots()
    chat_slots = _collect_chat_slots(slots, contexts=ctx.io.fetch_model_contexts())
    primary_raw = _resolve_primary_slot(slots_fetcher=lambda: slots)
    live_resolve_enabled = _live_resolve_enabled()
    base_url = "http://127.0.0.1:8080/v1" if live_resolve_enabled else primary_raw["base_url"]

    pairs: list[tuple[str, Any]] = [
        # ADR-0023: canonical default virtual is hal0/agent (was hal0/chat).
        ("model.default", "hal0/agent" if live_resolve_enabled else primary_raw["model"]),
        ("model.provider", "custom"),
        ("model.base_url", base_url),
    ]
    for slot in chat_slots:
        alias = slot["alias"]
        pairs += [
            (f"model_aliases.{alias}.model", slot["model_id"]),
            (f"model_aliases.{alias}.provider", "custom"),
            (f"model_aliases.{alias}.base_url", slot["backend_url"]),
        ]
    applied, errors = _apply_config_set(
        pairs, hermes_bin=hermes_bin, hermes_home=hermes_home, run=ctx.io.run
    )

    skipped = [_slot_alias(s) for s in slots if _slot_kind(s) in {"embed", "rerank", "img"}]
    aliases_written = [s["alias"] for s in chat_slots]
    status = PhaseStatus.OK if (applied or not pairs) else PhaseStatus.FAIL
    return PhaseResult(
        status=status,
        hash=content_hash(config_path.read_text(encoding="utf-8")),
        reason=("; ".join(errors[:3]) if status == PhaseStatus.FAIL else None),
        details={
            "config_path": str(config_path),
            "aliases_written": aliases_written,
            "skipped": skipped,
            "chat_slot_count": len(chat_slots),
            "slots_total": len(slots),
            "keys_applied": applied,
            "config_set_errors": errors,
        },
    )


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


def _phase_gateway_secrets_wire(ctx: PhaseContext) -> PhaseResult:
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

    if result.outcome == "skipped":
        return PhaseResult(status=PhaseStatus.SKIP, reason=result.reason, details=details)
    if result.outcome == "failed":
        return PhaseResult(status=PhaseStatus.FAIL, reason=result.reason, details=details)

    # "written" | "unchanged" → OK. Preserve the checkpoint's detail shape:
    # daemon_reload flag always present; `unchanged` only on the hash-skip path.
    details["daemon_reload"] = result.daemon_reload
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


def _privileged_systemctl(verb: str, body: str | None = None) -> None:
    """Run one hal0-systemctl seam verb as root via ``sudo -n``.

    ``body`` (when given) is piped on stdin — used for ``write-gateway-dropin``.
    Raises ``subprocess.CalledProcessError`` on a non-zero seam exit so the
    caller surfaces the failure instead of masquerading a broken gateway as up.
    """
    subprocess.run(  # nosec B603 — fixed argv; verb is a literal from our own code
        ["sudo", "-n", _HAL0_SYSTEMCTL, verb],
        input=body,
        text=True,
        check=True,
    )


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


def _phase_voice_wire(ctx: PhaseContext) -> PhaseResult:
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


def _smoke_chat_completions(state: BootstrapState, _io: PhaseIO) -> tuple[bool, str]:
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


def _smoke_memory_roundtrip(state: BootstrapState, io: PhaseIO) -> tuple[bool, str]:
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


def _smoke_admin_tools_list(state: BootstrapState, io: PhaseIO) -> tuple[bool, str]:
    probe = io.probe_mcp_server(
        "http://127.0.0.1:8080/mcp/admin",
        agent_id=state.agent_id,
        private=False,
    )
    if not probe["ok"]:
        return (False, probe["error"] or "unreachable")
    n = len(probe["tools"])
    return (n >= 5, f"{n} tools advertised")


def _smoke_hermes_md_contains_primary(state: BootstrapState, _io: PhaseIO) -> tuple[bool, str]:
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


def _smoke_wrapper_ready(_state: BootstrapState, io: PhaseIO) -> tuple[bool, str]:
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


def _smoke_hermes_doctor(_state: BootstrapState, io: PhaseIO) -> tuple[bool, str]:
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


def _phase_smoke_tests(ctx: PhaseContext) -> PhaseResult:
    """Run six diagnostic probes; collect results into the checkpoint."""
    state = ctx.state
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
            passed, detail = fn(state, ctx.io)
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


def _phase_self_report(ctx: PhaseContext) -> PhaseResult:
    """Write a bootstrap-completion summary into the agent's private namespace.

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
    if os.geteuid() == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".env.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
        with contextlib.suppress(OSError):
            os.chown(path, 0, 0)
    else:
        # Driver env lives in root:root /etc/hal0/agents — delegate the write
        # to the seam (it builds the path from the validated agent name).
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
    return path, True


def _phase_install_artifacts(ctx: PhaseContext) -> PhaseResult:
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


# ── ownership_reconcile phase removed (§7.4 F.7) ─────────────────────────────
#
# The late always-run phase re-chowned HERMES_HOME to hal0 and repaired 0711 on
# /var/lib/hal0/agents. It existed solely to undo the root:root config.yaml that
# the root-context config_write wrote AFTER home_init's chown. Provisioning now
# runs as hal0 (cli/_provision_hermes drops before this pipeline), so config.yaml
# and the whole home are born hal0:hal0 — there is nothing to reconcile. The
# 0711 traversal mode on /var/lib/hal0/agents is an OwnershipStore row applied by
# `doctor perms --fix`, not this phase.


# ── Phase pipeline plumbing (issue #702) ────────────────────────────────────
#
# The pipeline's IO seams + cross-phase reads, made explicit:
#
#   * ``PhaseIO`` bundles every external touchpoint a phase may use
#     (HTTP, subprocess, the slot/MCP/memory fetchers). Defaults are the
#     real module functions, so ``PhaseIO()`` IS production behaviour;
#     tests construct ``PhaseIO(fetch_slots=fake, ...)`` instead of
#     monkeypatching module globals.
#   * ``PhaseContext`` is what a phase receives: read-only state, the
#     ``--repair`` flag (formerly the ``_repair_flag`` sentinel smuggled
#     through ``state.phases``), the IO bundle, and ``output_of(name)``
#     — the ONLY sanctioned way to read another phase's checkpoint.
#   * ``Phase`` entries in ``PHASES`` declare their cross-phase reads via
#     ``needs`` (same-run: target precedes reader) or ``needs_previous``
#     (previous-run checkpoint: target follows reader in the list, so the
#     value can only come from a persisted prior run). ``output_of``
#     raises ``PhaseNeedError`` for any undeclared read;
#     ``_validate_phase_graph`` rejects a mis-ordered PHASES list at
#     import time.
#
# Path constants intentionally stay module-level (tests redirect them
# with monkeypatch) — only behavioural IO lives in PhaseIO.


class PhaseNeedError(RuntimeError):
    """A phase read another phase's output without declaring the need."""


@dataclass(frozen=True)
class PhaseIO:
    """The IO seams a phase may touch — the monkeypatch tax, typed.

    Defaults bind the real implementations, so a default-constructed
    ``PhaseIO`` changes nothing in production. ``run`` is
    :func:`subprocess.run` (gateway_secrets_wire's daemon-reload + the
    smoke-test exec path).
    """

    http_get: Callable[..., int] = _http_get
    fetch_slots: Callable[[], list[dict[str, Any]]] = _fetch_slots
    fetch_model_contexts: Callable[[], dict[str, int]] = _fetch_model_contexts
    probe_mcp_server: Callable[..., dict[str, Any]] = _probe_mcp_server
    mcp_memory_call: Callable[..., dict[str, Any]] = _mcp_memory_call
    install_venv: Callable[..., None] = _install_venv
    read_env_probe: Callable[[], dict[str, Any]] = _read_env_probe
    load_config: Callable[[], Any] = _load_hal0_config
    run: Callable[..., Any] = subprocess.run


@dataclass(frozen=True)
class PhaseContext:
    """Everything a phase body is allowed to see.

    ``state`` is a read-only view by convention (phases return a
    :class:`PhaseResult`; only the orchestrator writes checkpoints).
    ``output_of(name)`` returns the named phase's checkpoint ``details``
    dict — empty when the phase has no checkpoint yet (e.g. the
    cross-run ``config_write → mcp_wire`` read on a fresh install) —
    and raises :class:`PhaseNeedError` unless ``name`` was declared in
    the calling phase's ``needs`` / ``needs_previous``.
    """

    state: BootstrapState
    repair: bool = False
    io: PhaseIO = field(default_factory=PhaseIO)
    phase_name: str = "<anonymous>"
    allowed_needs: frozenset[str] = frozenset()
    # Capture mode: back up + import + claim a foreign HERMES_HOME (and
    # downgrade a foreign-gateway abort to a warning) rather than refusing.
    adopt: bool = False

    def output_of(self, name: str) -> dict[str, Any]:
        if name not in self.allowed_needs:
            raise PhaseNeedError(
                f"phase {self.phase_name!r} read output of {name!r} without "
                f"declaring it (declared needs: {sorted(self.allowed_needs)})"
            )
        entry = self.state.phases.get(name) or {}
        details = entry.get("details") or {}
        return details if isinstance(details, dict) else {}


@dataclass(frozen=True)
class Phase:
    """One PHASES entry: name, body, and declared cross-phase reads.

    ``needs``          — same-run reads; the target MUST precede this
                         phase in the list (validated at import).
    ``needs_previous`` — previous-run checkpoint reads; the target MUST
                         follow this phase in the list (if it preceded,
                         it would be a plain same-run need). The only
                         such edge today is ``config_write → mcp_wire``:
                         mcp_wire probes AFTER the first render and the
                         probed server list feeds the NEXT run's render.
    """

    name: str
    fn: Callable[[PhaseContext], PhaseResult]
    needs: tuple[str, ...] = ()
    needs_previous: tuple[str, ...] = ()
    # When True the orchestrator runs this phase on every invocation, even
    # when its checkpoint is already ok (no --repair). For phases whose work
    # reconciles state that drifts independently of checkpoints (ownership).
    always_run: bool = False

    @property
    def allowed_needs(self) -> frozenset[str]:
        return frozenset(self.needs) | frozenset(self.needs_previous)


def _validate_phase_graph(phases: list[Phase]) -> None:
    """Fail fast (import time) when PHASES violates a declared need."""
    index: dict[str, int] = {}
    for i, phase in enumerate(phases):
        if phase.name in index:
            raise ValueError(f"PHASES: duplicate phase name {phase.name!r}")
        index[phase.name] = i
    for i, phase in enumerate(phases):
        for need in phase.needs:
            if need not in index:
                raise ValueError(f"PHASES: {phase.name!r} needs unknown phase {need!r}")
            if index[need] >= i:
                raise ValueError(
                    f"PHASES: {phase.name!r} needs {need!r} which does not precede it "
                    f"(reader at {i}, target at {index[need]})"
                )
        for need in phase.needs_previous:
            if need not in index:
                raise ValueError(f"PHASES: {phase.name!r} needs_previous unknown phase {need!r}")
            if index[need] < i:
                raise ValueError(
                    f"PHASES: {phase.name!r} declares needs_previous on {need!r}, "
                    f"but {need!r} precedes it — declare it as a plain same-run need"
                )


PHASES: list[Phase] = [
    Phase("preflight", _phase_preflight),
    Phase("install", _phase_install),
    Phase("env_probe", _phase_env_probe),
    Phase("home_init", _phase_home_init),
    # #432: write the manager seed + driver env + runtime.json embed token
    # right after $HERMES_HOME exists and before mcp_wire reads the seed's
    # allow-list, so a single `bootstrap hermes` run leaves the artifacts the
    # manager + chat_proxy key off (previously only AgentManager.install wrote
    # them, so the bootstrap path left the agent reporting `broken`).
    Phase("install_artifacts", _phase_install_artifacts),
    # PR-3 Phase 8: seed personas BEFORE config_write so the first
    # config render gets the active persona's system_prompt prelude.
    # mcp_wire runs after config_write to probe the live MCP surface;
    # the probe results feed Phase 9 (model_automap)'s re-render so a
    # post-bootstrap config still picks up the validated server list —
    # hence config_write's needs_previous (cross-run) edge on mcp_wire.
    Phase("persona_seed", _phase_persona_seed),
    Phase("config_write", _phase_config_write, needs_previous=("mcp_wire",)),
    Phase("mcp_wire", _phase_mcp_wire),
    Phase("context_link", _phase_context_link),
    Phase("namespace_register", _phase_namespace_register),
    # Register the hal0-brain profile identity right after the default agent's
    # card, once the memory layer is up. Warn-as-OK like namespace_register.
    Phase("brain_profile_seed", _phase_brain_profile_seed),
    # Wire the hal0-owned MCP servers (admin + memory) into the hal0-brain
    # profile config so the steward can control the box. Warn-as-OK.
    Phase("brain_profile_mcp_wire", _phase_brain_profile_mcp_wire),
    # Both re-apply their slice of the overlay via `hermes config set` (no
    # full re-render), so neither reads mcp_wire's probed-server checkpoint
    # any more — only config_write still does (needs_previous above).
    Phase("model_automap", _phase_model_automap),
    Phase("voice_wire", _phase_voice_wire),
    # (ownership_reconcile phase removed — §7.4 F.7: provisioning runs as hal0,
    # so config.yaml / the home are born hal0:hal0 with nothing to reconcile.)
    # #437 (SYSTEM scope): wire the gateway secrets drop-in so fresh
    # provisions/reinstalls come up with Telegram + Discord connected,
    # surviving hermes_cli main-unit regeneration. Runs after voice_wire
    # (which may write the secrets vault this drop-in references) and
    # before smoke_tests. The orchestrator runs `hermes gateway install`
    # separately to lay down the main unit; this phase only owns the
    # drop-in + daemon-reload.
    Phase("gateway_secrets_wire", _phase_gateway_secrets_wire),
    Phase("smoke_tests", _phase_smoke_tests),
    Phase("self_report", _phase_self_report, needs=("smoke_tests",)),
]

_validate_phase_graph(PHASES)

PHASE_NAMES: tuple[str, ...] = tuple(p.name for p in PHASES)


def context_for(
    phase_name: str,
    state: BootstrapState,
    *,
    repair: bool = False,
    adopt: bool = False,
    io: PhaseIO | None = None,
) -> PhaseContext:
    """Build the :class:`PhaseContext` the orchestrator would hand ``phase_name``.

    Looks the phase up in :data:`PHASES` so the context carries the
    declared needs — the canonical way for per-phase unit tests to call
    a phase body directly without re-stating the needs graph.
    """
    phase = next((p for p in PHASES if p.name == phase_name), None)
    if phase is None:
        raise KeyError(f"unknown phase {phase_name!r} (known: {', '.join(PHASE_NAMES)})")
    return PhaseContext(
        state=state,
        repair=repair,
        adopt=adopt,
        io=io if io is not None else PhaseIO(),
        phase_name=phase.name,
        allowed_needs=phase.allowed_needs,
    )


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
    # Set when a phase returned a FATAL failure and the run stopped early
    # (unclaimed foreign HERMES_HOME, or a live foreign gateway). The CLI
    # surfaces ``abort_reason`` + the --adopt pointer and exits non-zero.
    aborted: bool = False
    abort_reason: str | None = None


def run(
    *,
    repair: bool = False,
    adopt: bool = False,
    dry_run: bool = False,
    skip_phases: tuple[str, ...] = (),
    state_root: Path | None = None,
    verbose: bool = False,
    initial_state: BootstrapState | None = None,
    io: PhaseIO | None = None,
) -> RunResult:
    """Run every phase in order, persisting checkpoints to ``state_root``.

    * ``repair`` — re-run every phase regardless of checkpoint state.
      Surfaced to phase bodies as ``ctx.repair`` (persona_seed +
      install_artifacts change behaviour under it).
    * ``dry_run`` — execute each phase but don't persist the state file.
    * ``skip_phases`` — skip the named phases (logged as ``skip``).
    * ``state_root`` — overrides the default ``provision.json`` location;
      tests pass a ``tmp_path``.
    * ``initial_state`` — seed state when no checkpoint exists; tests
      pass one with `hermes_home` + `venv` pointed at `tmp_path` so the
      real install/home_init phases don't need write access to /var/lib.
    * ``io`` — the :class:`PhaseIO` seam bundle every phase receives;
      ``None`` means the production wiring (``PhaseIO()``).

    FAIL policy is run-all: a failing phase never halts the loop or
    skips dependents (fallbacks keep phases independent; convergence
    comes via ``--repair``). ``completed_at`` is only stamped when no
    phase failed — unchanged from the pre-#702 contract.

    Returns a :class:`RunResult` capturing the post-run state + the
    per-phase outcomes the CLI surface pretty-prints.
    """
    root = state_root if state_root is not None else _DEFAULT_STATE_ROOT
    state = BootstrapState.load(root) or initial_state or BootstrapState()
    if state.started_at is None or repair:
        state.started_at = _utcnow()
        state.completed_at = None

    phase_io = io if io is not None else PhaseIO()
    skipped: list[str] = []
    failed: list[str] = []
    aborted = False
    abort_reason: str | None = None

    for phase in PHASES:
        name = phase.name

        # A prior FATAL phase aborts the run: record every remaining phase as
        # skipped (so provision.json shows why it stopped) and run nothing more.
        if aborted:
            state.phases[name] = {
                "status": PhaseStatus.SKIP.value,
                "at": _utcnow(),
                "reason": "aborted: unclaimed HERMES_HOME",
            }
            skipped.append(name)
            if verbose:
                print(f"[skip] {name} (aborted)")
            continue

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

        # always_run phases never phase_done-skip — their work reconciles state
        # that drifts independently of checkpoints. (No phase currently sets
        # always_run since the ownership_reconcile removal, §7.4 F.7; the
        # machinery stays for future phases.)
        if not repair and not phase.always_run and state.phase_done(name):
            if verbose:
                print(f"[skip] {name} (already ok)")
            skipped.append(name)
            continue

        if verbose:
            print(f"[run ] {name}")

        ctx = PhaseContext(
            state=state,
            repair=repair,
            adopt=adopt,
            io=phase_io,
            phase_name=name,
            allowed_needs=phase.allowed_needs,
        )
        result = phase.fn(ctx)
        entry = result.to_dict()
        entry["at"] = _utcnow()
        state.phases[name] = entry

        if result.status == PhaseStatus.FAIL:
            failed.append(name)
            state.errors.append(f"{name}: {result.reason or 'unspecified failure'}")
            if result.fatal:
                aborted = True
                abort_reason = result.reason
                if verbose:
                    print(f"[abort] {name}: {result.reason}")

    if not failed:
        state.completed_at = _utcnow()

    if not dry_run:
        state.save(root)

    return RunResult(
        state=state,
        phases=dict(state.phases),
        skipped=skipped,
        failed=failed,
        aborted=aborted,
        abort_reason=abort_reason,
    )


# ── CLI surface ──────────────────────────────────────────────────────────────


def bootstrap_cli(
    *,
    repair: bool,
    adopt: bool = False,
    dry_run: bool,
    skip_phases: tuple[str, ...],
    verbose: bool,
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
        target = (state_root or _DEFAULT_STATE_ROOT) / _STATE_FILE_NAME
        print(f"state: {target}")

    # Surface a foreign-gateway warning even on an otherwise-successful --adopt
    # run — hal0 won't auto-stop another user's poller, so the operator must.
    preflight = result.state.phases.get("preflight") or {}
    warn = (preflight.get("details") or {}).get("foreign_gateway_warning")
    if warn:
        print(f"WARNING: {warn}")

    if result.aborted:
        print(f"bootstrap aborted: {result.abort_reason or 'unclaimed HERMES_HOME'}")
        if not adopt:
            print(
                "Re-run with --adopt to safely capture the existing install "
                "(backs it up + imports its tokens), or move it aside."
            )
        print(f"Operator overrides live at {OVERRIDES_PATH}.")
        return 1
    if result.failed:
        print(f"bootstrap failed in phases: {', '.join(result.failed)}")
        return 1
    return 0
