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
        entry = self.phases.get(name)
        if not entry:
            return False
        return entry.get("status") == PhaseStatus.OK.value

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


_phase_preflight = _stub("preflight")
_phase_install = _stub("install")
_phase_env_probe = _stub("env_probe")
_phase_home_init = _stub("home_init")
_phase_config_write = _stub("config_write")
_phase_mcp_wire = _stub("mcp_wire")
_phase_context_link = _stub("context_link")
_phase_namespace_register = _stub("namespace_register")
_phase_model_automap = _stub("model_automap")
_phase_voice_wire = _stub("voice_wire")
_phase_smoke_tests = _stub("smoke_tests")
_phase_self_report = _stub("self_report")


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
) -> RunResult:
    """Run every phase in order, persisting checkpoints to ``state_root``.

    * ``repair`` — re-run every phase regardless of checkpoint state.
    * ``dry_run`` — execute each phase but don't persist the state file.
    * ``skip_phases`` — skip the named phases (logged as ``skip``).
    * ``state_root`` — overrides the default ``provision.json`` location;
      tests pass a ``tmp_path``.

    Returns a :class:`RunResult` capturing the post-run state + the
    per-phase outcomes the CLI surface pretty-prints.
    """
    root = state_root if state_root is not None else _DEFAULT_STATE_ROOT
    state = BootstrapState.load(root) or BootstrapState()
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
