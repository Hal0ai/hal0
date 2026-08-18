"""Propagate the memory graph extraction slot to hindsight-api (ADR-0023).

Hindsight builds its graph natively via its own extraction LLM, configured by the
``HINDSIGHT_API_LLM_MODEL`` env in the ``hindsight-api.service`` unit. To make the
target operator-selectable WITHOUT hand-editing the installer-owned base unit, hal0
owns a systemd **drop-in**::

    /etc/systemd/system/hindsight-api.service.d/extraction-model.conf
        [Service]
        Environment=HINDSIGHT_API_LLM_MODEL=hal0/<slot>

and runs ``systemctl daemon-reload`` + ``systemctl restart hindsight-api`` so the
engine picks up the new target. The slot is addressed as the ``hal0/<slot>`` virtual
(resolved by the dispatcher to that slot's model — ADR-0023 §2), so the value tracks
the slot, never a hardcoded model id.

Privileged operation, routed through the seam (#1641): both the drop-in write and
the restart are genuinely-root, and hal0-api runs as the unprivileged ``hal0``
service user (``User=hal0``). The original implementation wrote
``/etc/systemd/system`` directly and shelled out to a bare ``systemctl``, so on
every standard install the write died with ``EPERM`` (the ``.d`` dir is
``root:root``) and the restart, had it been reached, would have hit polkit's
"Interactive authentication required" — while ``hal0.toml`` recorded the new slot
regardless, so the dashboard reported an override that was never applied. Every
step now goes through :class:`hal0.system.SystemCtlSeam`:

* the drop-in write -> ``hal0-systemctl write-hindsight-dropin`` (body on stdin,
  the path is a root-side literal — the ``write-gateway-dropin`` posture);
* ``daemon-reload`` -> the seam's own verb;
* the restart -> ``svc-restart hindsight`` (the wrapper's closed companion-unit
  map), which is why the unit is spelled ``hindsight-api.service`` here.

Off the ``hal0`` service account (root, a dev shell, CI, the unit tests) the seam
is a passthrough and everything runs directly, exactly as before.

Still best-effort: this returns a status dict describing what happened rather than
raising, so the API can surface a partial result instead of 500ing.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from hal0.agents.anchor_window import SLOTS_DIR, AnchorWindow, resolve_anchor_window
from hal0.system.seam import SystemCtlSeam

log = structlog.get_logger(__name__)

#: systemd drop-in directory + file for the hindsight-api extraction model override.
DROP_IN_DIR = Path("/etc/systemd/system/hindsight-api.service.d")
DROP_IN_PATH = DROP_IN_DIR / "extraction-model.conf"
#: Full unit name — ``COMPANION_SERVICE_UNITS`` keys on it to reach the wrapper's
#: ``svc-restart hindsight`` arm. A bare ``hindsight-api`` would miss that map and
#: fall through to an unprivileged (polkit-blocked) systemctl.
SERVICE = "hindsight-api.service"

#: Bound on each seam call so a wedged unit can never pin an event-loop thread.
_SYSTEMCTL_TIMEOUT_S = 60.0

#: Default daemon LLM timeout (seconds) — mirrors MemoryGraphConfig.llm_timeout_s.
DEFAULT_LLM_TIMEOUT_S = 300

#: Conservative floor for Hindsight's native extraction prompt (#1903).
#:
#: Hindsight builds its graph via its OWN extraction LLM call, dispatched to
#: ``hal0/<extraction_slot>`` (see the module docstring) — the same
#: virtual-model-through-the-routing-fallback-chain shape #1867 preflights
#: for Hermes' anchor, just never checked on this side. hal0 never sees the
#: vendored prompt template (it lives in the hindsight-api package, not this
#: repo), so this is deliberately a ROUND, DEFENSIBLE floor rather than an
#: exact token count: rc.6 validation observed the prompt (few-shot examples
#: + narrator scaffolding + the extraction schema) run ~3-4k tokens on its
#: own, and the reproduced failure (ct151-cpu-fresh, `known-issues.yaml:
#: memory-extraction-quality-is-anchor-dependent`) sat at ctx=4096 — i.e.
#: at or below the prompt's own footprint, before a single byte of the
#: document being retained is added. 8192 doubles that observed prompt cost
#: so a slot which clears the floor has headroom left for the document text
#: itself; a slot still under it cannot fit the prompt regardless of what is
#: being retained, which is the catastrophic (retain silently dropped, or
#: prompt content persisted as fact) shape this preflight exists to catch.
EXTRACTION_MIN_CONTEXT_TOKENS = 8192

_DROP_IN_TEMPLATE = (
    "# Managed by hal0 (ADR-0023 — memory.graph.extraction_slot / llm_timeout_s).\n"
    "# Overrides HINDSIGHT_API_LLM_MODEL and HINDSIGHT_API_LLM_TIMEOUT in the base\n"
    "# hindsight-api.service unit. Do not edit by hand; set via `hal0 memory graph\n"
    "# enable --slot <name>` or the dashboard, which rewrites this file and\n"
    "# restarts the service.\n"
    "[Service]\n"
    "Environment=HINDSIGHT_API_LLM_MODEL=hal0/{slot}\n"
    "Environment=HINDSIGHT_API_LLM_TIMEOUT={timeout_s}\n"
)


def _detail(exc: BaseException) -> str:
    """``str(exc)`` plus the child's stderr when there is one.

    A seam failure's *cause* almost always lives in stderr — ``sudo: a password
    is required`` (no grant), ``hal0-systemctl: bad cmd`` (stale wrapper) — while
    ``str(exc)`` is just the exit code. Both go to the operator.
    """
    stderr = getattr(exc, "stderr", "") or ""
    if isinstance(stderr, bytes):  # TimeoutExpired can carry raw bytes
        stderr = stderr.decode("utf-8", "replace")
    stderr = stderr.strip()
    return f"{exc}{(' — ' + stderr) if stderr else ''}"


#: The wrapper's own ``die()`` exit code (installer/wrappers/hal0-systemctl).
_SEAM_USAGE_RC = 64

_STALE_WRAPPER_HINT = (
    " — the installed /usr/lib/hal0/bin/hal0-systemctl predates this verb; "
    "re-run the installer (`sudo bash install.sh`) to refresh the seam wrapper"
)


def _stale_wrapper_hint(exc: BaseException) -> str:
    """Remediation text when the seam rejected the verb outright.

    ``hal0 update`` swaps the release tree and re-pips the venv but does not
    reinstall ``${LIB_DIR}/bin/*`` — only ``install.sh`` does. So new Python can
    meet an old wrapper, which answers a verb it doesn't know with
    ``hal0-systemctl: bad cmd: ...`` and exit 64. That is a fixable operator
    condition, not a bug report, so say how to fix it.
    """
    rc = getattr(exc, "returncode", None)
    if rc == _SEAM_USAGE_RC or "bad cmd" in _detail(exc):
        return _STALE_WRAPPER_HINT
    return ""


def render_drop_in(slot: str, timeout_s: int = DEFAULT_LLM_TIMEOUT_S) -> str:
    """Return the drop-in contents pinning extraction to ``hal0/<slot>`` + timeout."""
    return _DROP_IN_TEMPLATE.format(slot=slot, timeout_s=int(timeout_s))


def drop_in_matches(slot: str, timeout_s: int = DEFAULT_LLM_TIMEOUT_S) -> bool:
    """True when the on-disk drop-in already reflects ``(slot, timeout_s)``.

    #1682 review: comparing only against ``hal0.toml`` is not enough to
    decide propagation is unnecessary. A host hit by the pre-seam write bug
    (or any other silent failure) can have ``hal0.toml`` already recording
    this exact slot while the drop-in was never actually written or still
    names something else — re-requesting the *same* slot would then never
    reconcile.

    The drop-in is 0644 root:root (world-readable, #1641), so this is a
    plain, unprivileged read — no seam round trip. Read as explicit UTF-8
    (#1717 review): the file is always written UTF-8 by
    :class:`~hal0.system.seam.SystemCtlSeam`, and the template's em dash
    would otherwise be locale-dependent — under e.g. ``LC_ALL=C`` a bare
    ``Path.read_text()`` can fail to decode a byte-for-byte correct file,
    misreporting a healthy drop-in as drift on every enabled graph PUT.
    Any read failure (missing file on a fresh install or a host that never
    propagated, a permission oddity, or genuinely undecodable content —
    ``Path.read_text()`` raises ``UnicodeDecodeError``, not ``OSError``)
    counts as "does not match": propagate and let the atomic rewrite
    repair it, instead of an otherwise-idempotent PUT 500ing.
    """
    try:
        return DROP_IN_PATH.read_text(encoding="utf-8") == render_drop_in(slot, timeout_s)
    except (OSError, UnicodeDecodeError):
        return False


def apply_extraction_slot(
    slot: str,
    *,
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    restart: bool = True,
    seam: SystemCtlSeam | None = None,
) -> dict[str, Any]:
    """Write the drop-in for ``slot`` and (best-effort) restart hindsight-api.

    Returns a status dict::

        {"slot", "model", "timeout_s", "drop_in", "written",
         "daemon_reloaded", "restarted", "error"}

    ``error`` is ``None`` on full success. The write is atomic (temp + rename) so a
    crash mid-write never leaves a half-written override that would wedge the unit.

    ``seam`` is an injection point (default-constructed = production behaviour), so
    the privileged routing is unit-testable without sudo, a real ``hal0`` user, or
    a writable ``/etc``. Blocking: callers on the event loop must hop a thread
    (``asyncio.to_thread``) — the restart waits on a hindsight-api cold start.
    """
    seam = seam if seam is not None else SystemCtlSeam()
    model = f"hal0/{slot}"
    result: dict[str, Any] = {
        "slot": slot,
        "model": model,
        "timeout_s": int(timeout_s),
        "drop_in": str(DROP_IN_PATH),
        "written": False,
        "daemon_reloaded": False,
        "restarted": False,
        "error": None,
    }

    try:
        seam.write_hindsight_dropin(
            render_drop_in(slot, timeout_s),
            path=DROP_IN_PATH,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        )
        result["written"] = True
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = (
            f"could not write {DROP_IN_PATH}: {_detail(exc)}{_stale_wrapper_hint(exc)}"
        )
        log.warning("hal0.memory.extraction_dropin_write_failed", slot=slot, error=str(exc))
        return result

    if not restart:
        return result

    for step, args in (
        ("daemon_reloaded", ("systemctl", "daemon-reload")),
        ("restarted", ("systemctl", "restart", SERVICE)),
    ):
        try:
            seam.systemctl(*args, check=True, timeout=_SYSTEMCTL_TIMEOUT_S)
            result[step] = True
        except (OSError, subprocess.SubprocessError) as exc:
            result["error"] = f"{' '.join(args)} failed: {_detail(exc)}"
            log.warning(
                "hal0.memory.extraction_restart_failed",
                slot=slot,
                step=step,
                error=str(exc),
            )
            return result

    log.info("hal0.memory.extraction_slot_applied", slot=slot, model=model)
    return result


#: Local alias so this module doesn't have to re-derive the ``hal0/`` prefix
#: :mod:`hal0.agents.anchor_window` already owns.
_VIRTUAL_PREFIX = "hal0/"


def extraction_model_name(slot: str) -> str:
    """The virtual model id Hindsight's extraction LLM call is spelled as.

    Mirrors :data:`_DROP_IN_TEMPLATE`'s ``HINDSIGHT_API_LLM_MODEL=hal0/{slot}``
    — the same string, so a caller resolving the extraction window is asking
    about the exact handle the drop-in actually points the engine at.
    """
    return f"{_VIRTUAL_PREFIX}{slot}"


#: Operator escape hatch for :data:`EXTRACTION_MIN_CONTEXT_TOKENS`. The floor
#: is honestly an estimate (hal0 never sees the vendored prompt template), so
#: a box where the estimate is wrong must not be stuck between "no memory
#: writes" and "resize a slot": set this on the hal0-api service to any
#: positive token count and it replaces the default floor. Recorded in
#: ``floor_source`` so the rendered error says when it was in play.
EXTRACTION_FLOOR_ENV = "HAL0_MEMORY_EXTRACTION_FLOOR"


#: Invalid override values already warned about, so a persistent typo in the
#: service environment logs ONCE per value instead of once per memory write —
#: ``extraction_floor`` runs on the write hot path (auto-retain fires on a
#: timer for every agent on the box), which would otherwise flood the journal
#: indefinitely.
_floor_override_warned: set[str] = set()


def extraction_floor() -> tuple[int, str]:
    """Return ``(floor, floor_source)`` honouring :data:`EXTRACTION_FLOOR_ENV`.

    An unset/blank variable yields the default; a non-integer or non-positive
    value is *ignored* (with the default returned) rather than raised — a
    typo'd override must not turn every memory write into a 500.
    """
    raw = (os.environ.get(EXTRACTION_FLOOR_ENV) or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return (value, f"env:{EXTRACTION_FLOOR_ENV}")
        if raw not in _floor_override_warned:
            _floor_override_warned.add(raw)
            log.warning("hal0.memory.extraction_floor_override_invalid", raw=raw)
    return (EXTRACTION_MIN_CONTEXT_TOKENS, "hal0:extraction-prompt-floor")


@dataclass(frozen=True)
class ExtractionWindow(AnchorWindow):
    """An :class:`AnchorWindow` whose rendered message names THIS subsystem.

    The parent's :meth:`~AnchorWindow.message` is hard-coded for Hermes'
    64,000-token anchor preflight ("Hermes requires", "EVERY turn will
    fail") — fed the extraction floor it would tell an operator debugging a
    failed memory write that their agent runtime is broken, against a
    threshold that contradicts the real Hermes check. Same resolved facts
    (slot, effective window, binding ceiling, fix command), memory-extraction
    words.
    """

    def message(self) -> str:
        where = f" at {self.endpoint}" if self.endpoint else ""
        floor_note = (
            f" (operator override via {EXTRACTION_FLOOR_ENV})"
            if self.floor_source.startswith("env:")
            else (
                f" (a conservative estimate of the prompt's footprint — override with "
                f"{EXTRACTION_FLOOR_ENV} on the hal0-api service if it is wrong for "
                f"this engine)"
            )
        )
        if self.verdict == "unknown":
            return (
                f"memory extraction model {self.model!r} advertises no context window"
                f"{where} right now — cannot check it against the {self.floor:,}-token "
                f"extraction-prompt floor. Not a pass, but memory writes are not "
                f"blocked on an unproven window; re-check once the extraction slot's "
                f"model is loaded"
            )
        if self.verdict == "ok":
            return (
                f"memory extraction model {self.model!r} ({self._slot_note}) resolves "
                f"to {self.effective:,} tokens ≥ the {self.floor:,}-token "
                f"extraction-prompt floor"
            )
        head = (
            f"memory write refused: Hindsight's extraction call dispatches to "
            f"{self.model!r}, which resolves to {self._slot_note} with an effective "
            f"context window of {self.effective:,} tokens — below the {self.floor:,} "
            f"tokens the extraction prompt alone needs{floor_note}. A retain would be "
            f"accepted and then silently dropped (or answered by persisting prompt "
            f'scaffolding as a "fact"), so /api/memory/add fails fast instead. Chat '
            f"is unaffected — only memory extraction is gated."
        )
        if self.slot is None:
            return (
                f"{head} hal0 could not prove which slot is serving it, so it cannot "
                f"name the ceiling to raise — run `hal0 slot list` and raise the "
                f"window of the slot behind {self.model!r} to at least {self.floor:,}."
            )
        if self.ceiling_is_binding:
            return (
                f"{head} The limit is this slot's own configured ceiling: "
                f"[model].context_size = {self.ceiling:,} in {self.slot_path}. "
                f"Fix: {self.fix_command}"
            )
        ceiling_note = (
            f"the slot ceiling ({self.ceiling:,} in {self.slot_path}) is not the limit; "
            if self.ceiling is not None
            else f"no ceiling is set in {self.slot_path}; "
        )
        return (
            f"{head} The limit is the model behind the slot — {ceiling_note}"
            f"the model itself only advertises {self.effective:,}. Point slot "
            f"{self.slot!r} at a model whose window is at least {self.floor:,} "
            f"(`hal0 slot edit {self.slot} --model <model-id>`), or set "
            f"defaults.context_size on the current model if it really supports more."
        )


def resolve_extraction_window(
    slot: str,
    *,
    entry: Mapping[str, Any] | None,
    catalog: Sequence[Mapping[str, Any]] | None = None,
    floor: int | None = None,
    slots_dir: Path = SLOTS_DIR,
    endpoint: str = "",
) -> ExtractionWindow:
    """Resolve the extraction slot's effective window against the prompt floor (#1903).

    Reuses :func:`hal0.agents.anchor_window.resolve_anchor_window` — the
    #1877 resolver — rather than reimplementing the virtual-model routing
    resolution it already gets right (never trust the ``hal0/<slot>``
    spelling; ask the by-id catalog row the routing fallback chain actually
    answered with). Only the model handle, the floor, and the rendered
    message differ from the Hermes-anchor call: extraction is checked
    against :func:`extraction_floor` (the prompt's own footprint, or the
    operator's :data:`EXTRACTION_FLOOR_ENV` override), not Hermes' much
    larger hard floor, and the verdict renders through
    :class:`ExtractionWindow` so a below-floor 503 talks about memory
    extraction rather than Hermes.

    ``entry``/``catalog`` are the resolved virtual-model row and the local
    slot-alias catalog for ``hal0/<slot>`` — injected so this stays pure and
    testable without a running gateway (see :class:`AnchorWindow`).
    """
    if floor is None:
        floor, floor_source = extraction_floor()
    else:
        floor_source = "hal0:extraction-prompt-floor"
    window = resolve_anchor_window(
        extraction_model_name(slot),
        entry=entry,
        catalog=catalog,
        floor=floor,
        floor_source=floor_source,
        slots_dir=slots_dir,
        endpoint=endpoint,
    )
    return ExtractionWindow(
        model=window.model,
        slot=window.slot,
        effective=window.effective,
        ceiling=window.ceiling,
        floor=window.floor,
        floor_source=window.floor_source,
        slots_dir=window.slots_dir,
        endpoint=window.endpoint,
    )


__all__ = [
    "DROP_IN_PATH",
    "EXTRACTION_FLOOR_ENV",
    "EXTRACTION_MIN_CONTEXT_TOKENS",
    "ExtractionWindow",
    "apply_extraction_slot",
    "drop_in_matches",
    "extraction_floor",
    "extraction_model_name",
    "render_drop_in",
    "resolve_extraction_window",
]
