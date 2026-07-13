"""Agent-agnostic provisioning state machine.

Distilled from the Hermes bootstrap pipeline
(:mod:`hal0.agents.hermes_provision`) so multiple heavyweight bundled
agents (turnstone today; Hermes can migrate onto it in a follow-up)
share one phase-orchestration contract instead of each copying it.

A provisioner supplies three things:

  * a concrete :class:`BootstrapState` subclass — agent-specific fields
    (home dir, binary path, version pins) with defaults;
  * an IO bundle (any object; conventionally a frozen dataclass of the
    external seams its phases touch — HTTP, subprocess, slot/MCP
    fetchers). The engine treats it opaquely and hands it to each phase
    via ``ctx.io``;
  * an ordered ``list[Phase]`` of ``(PhaseContext) -> PhaseResult``
    bodies.

The engine owns everything agent-independent: checkpointing to
``provision.json``, skip-if-already-ok, ``--repair`` force-rerun,
fatal-abort propagation, and import-time validation of the
``needs`` / ``needs_previous`` cross-phase graph. It imports nothing
hal0- or agent-specific, so it stays trivially unit-testable and safe
to share.

State file lives OUTSIDE the agent's own tree (e.g.
``/var/lib/hal0/state/agents/<agent>/provision.json``) so an upstream
``reset`` subcommand can't trample hal0's bookkeeping.
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
from typing import Any, ClassVar

# Schema version embedded in every provision.json. Bump when the on-disk
# shape changes in a way that can't be migrated by ignoring unknown keys.
SCHEMA_VERSION = 1


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
    alone is insufficient.

    ``details`` is a free-form dict each phase can stash. The
    orchestrator never inspects its contents; it just JSON-serialises
    them into the checkpoint.

    A fatal ``FAIL`` aborts the run: the orchestrator stops executing
    subsequent phases and records them as skipped (used by capture
    guards — an unclaimed foreign home, a live foreign listener). A
    normal ``FAIL`` stays run-all (fallbacks keep phases independent).
    """

    status: PhaseStatus
    details: dict[str, Any] = field(default_factory=dict)
    hash: str | None = None
    reason: str | None = None
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
    """In-memory mirror of ``provision.json`` — the generic base.

    Subclass to add agent-specific fields (home dir, binary path,
    version pin); the dataclass machinery + :func:`dataclasses.asdict`
    means ``save`` / ``load`` round-trip the subclass fields with no
    extra code. ``phases`` is keyed by phase name with values built from
    :meth:`PhaseResult.to_dict` plus an ``at`` timestamp.

    Override :attr:`STATE_FILE_NAME` only if an agent needs a
    non-default checkpoint filename (none do today).
    """

    STATE_FILE_NAME: ClassVar[str] = "provision.json"

    schema_version: int = SCHEMA_VERSION
    started_at: str | None = None
    completed_at: str | None = None
    hal0_version: str | None = None
    agent_id: str = ""
    phases: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BootstrapState:
        # Ignore unknown keys so a forward-compat schema bump doesn't
        # crash an older orchestrator reading a newer file.
        valid = set(cls.__dataclass_fields__)
        kwargs = {k: v for k, v in data.items() if k in valid}
        return cls(**kwargs)

    def phase_done(self, name: str) -> bool:
        """True iff the phase already ran to a terminal non-failure state.

        Both ``ok`` and ``skip`` count as "done" — a phase that
        legitimately skipped shouldn't re-run on every invocation.
        ``--repair`` is the explicit force-rerun knob.
        """
        entry = self.phases.get(name)
        if not entry:
            return False
        return entry.get("status") in {PhaseStatus.OK.value, PhaseStatus.SKIP.value}

    def save(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        target = root / self.STATE_FILE_NAME
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")
        os.replace(tmp, target)

    @classmethod
    def load(cls, root: Path) -> BootstrapState | None:
        target = root / cls.STATE_FILE_NAME
        if not target.exists():
            return None
        try:
            data = json.loads(target.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return cls.from_dict(data)


# ── Phase pipeline plumbing ──────────────────────────────────────────────────


class PhaseNeedError(RuntimeError):
    """A phase read another phase's output without declaring the need."""


@dataclass(frozen=True)
class PhaseContext:
    """Everything a phase body is allowed to see.

    ``state`` is a read-only view by convention (phases return a
    :class:`PhaseResult`; only the orchestrator writes checkpoints).
    ``io`` is the provisioner's opaque IO bundle. ``output_of(name)``
    returns the named phase's checkpoint ``details`` dict — empty when
    the phase has no checkpoint yet — and raises :class:`PhaseNeedError`
    unless ``name`` was declared in the calling phase's ``needs`` /
    ``needs_previous``.
    """

    state: BootstrapState
    repair: bool = False
    io: Any = None
    phase_name: str = "<anonymous>"
    allowed_needs: frozenset[str] = frozenset()
    # Capture mode: back up + import + claim a foreign home (and downgrade
    # a foreign-listener abort to a warning) rather than refusing.
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
    """One entry in a provisioner's ordered phase list.

    ``needs``          — same-run reads; the target MUST precede this
                         phase in the list (validated at import).
    ``needs_previous`` — previous-run checkpoint reads; the target MUST
                         follow this phase in the list (if it preceded,
                         it would be a plain same-run need).
    ``always_run``     — run on every invocation even when the checkpoint
                         is already ok (for phases that reconcile state
                         drifting independently of checkpoints).
    """

    name: str
    fn: Callable[[PhaseContext], PhaseResult]
    needs: tuple[str, ...] = ()
    needs_previous: tuple[str, ...] = ()
    always_run: bool = False

    @property
    def allowed_needs(self) -> frozenset[str]:
        return frozenset(self.needs) | frozenset(self.needs_previous)


def validate_phase_graph(phases: list[Phase]) -> None:
    """Fail fast (import time) when a phase list violates a declared need."""
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


def context_for(
    phases: list[Phase],
    phase_name: str,
    state: BootstrapState,
    *,
    repair: bool = False,
    adopt: bool = False,
    io: Any = None,
) -> PhaseContext:
    """Build the :class:`PhaseContext` the orchestrator would hand ``phase_name``.

    Looks the phase up in ``phases`` so the context carries the declared
    needs — the canonical way for per-phase unit tests to call a phase
    body directly without re-stating the needs graph.
    """
    phase = next((p for p in phases if p.name == phase_name), None)
    if phase is None:
        known = ", ".join(p.name for p in phases)
        raise KeyError(f"unknown phase {phase_name!r} (known: {known})")
    return PhaseContext(
        state=state,
        repair=repair,
        adopt=adopt,
        io=io,
        phase_name=phase.name,
        allowed_needs=phase.allowed_needs,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def utcnow() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat().replace("+00:00", "Z")


def content_hash(*pieces: str | bytes) -> str:
    """Stable content hash phases use to detect "inputs unchanged".

    Phases that produce on-disk outputs hash the rendered content and
    stash it in ``PhaseResult.hash``. A re-run computes the hash again;
    mismatch → ``repair_needed``.
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

    ``phases`` mirrors ``BootstrapState.phases`` post-run for test-side
    assertions; ``state`` is the persisted dataclass. ``aborted`` /
    ``abort_reason`` are set when a phase returned a FATAL failure and
    the run stopped early.
    """

    state: BootstrapState
    phases: dict[str, dict[str, Any]]
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str | None = None


def run(
    phases: list[Phase],
    *,
    state_root: Path,
    io: Any = None,
    state_cls: type[BootstrapState] = BootstrapState,
    initial_state: BootstrapState | None = None,
    repair: bool = False,
    adopt: bool = False,
    dry_run: bool = False,
    skip_phases: tuple[str, ...] = (),
    verbose: bool = False,
) -> RunResult:
    """Run every phase in ``phases`` in order, persisting checkpoints.

    * ``state_root`` — directory holding ``provision.json``.
    * ``io`` — the opaque IO bundle every phase receives via ``ctx.io``.
    * ``state_cls`` — the concrete :class:`BootstrapState` subclass to
      load/construct (agent-specific fields).
    * ``initial_state`` — seed state when no checkpoint exists; tests
      pass one with paths pointed at a ``tmp_path``.
    * ``repair`` — re-run every phase regardless of checkpoint state.
    * ``dry_run`` — execute each phase but don't persist the state file.
    * ``skip_phases`` — skip the named phases (logged as ``skip``).

    FAIL policy is run-all: a failing (non-fatal) phase never halts the
    loop or skips dependents. ``completed_at`` is only stamped when no
    phase failed.
    """
    state = state_cls.load(state_root) or initial_state or state_cls()
    if state.started_at is None or repair:
        state.started_at = utcnow()
        state.completed_at = None

    skipped: list[str] = []
    failed: list[str] = []
    aborted = False
    abort_reason: str | None = None

    for phase in phases:
        name = phase.name

        # A prior FATAL phase aborts the run: record every remaining phase
        # as skipped (so provision.json shows why it stopped) and run nothing.
        if aborted:
            state.phases[name] = {
                "status": PhaseStatus.SKIP.value,
                "at": utcnow(),
                "reason": f"aborted: {abort_reason or 'fatal failure'}",
            }
            skipped.append(name)
            if verbose:
                print(f"[skip] {name} (aborted)")
            continue

        if name in skip_phases:
            state.phases[name] = {
                "status": PhaseStatus.SKIP.value,
                "at": utcnow(),
                "reason": "--skip-phase",
            }
            skipped.append(name)
            if verbose:
                print(f"[skip] {name} (--skip-phase)")
            continue

        # always_run phases never phase_done-skip — their work reconciles
        # state that drifts independently of checkpoints.
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
            io=io,
            phase_name=name,
            allowed_needs=phase.allowed_needs,
        )
        result = phase.fn(ctx)
        entry = result.to_dict()
        entry["at"] = utcnow()
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
        state.completed_at = utcnow()

    if not dry_run:
        state.save(state_root)

    return RunResult(
        state=state,
        phases=dict(state.phases),
        skipped=skipped,
        failed=failed,
        aborted=aborted,
        abort_reason=abort_reason,
    )
