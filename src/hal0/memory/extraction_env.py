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
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from hal0.agents.anchor_window import SLOTS_DIR, AnchorWindow, resolve_anchor_window
from hal0.system.seam import SystemCtlSeam

if TYPE_CHECKING:  # pragma: no cover
    from hal0.config.schema import MemoryGraphConfig

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


@dataclass(frozen=True)
class ExtractionLimits:
    """The ``[memory.graph]`` extraction limits, as rendered into the drop-in (#1834).

    Defaults mirror :class:`hal0.config.schema.MemoryGraphConfig` and are
    deliberately conservative for the shipped shape — one shared llama-server
    slot — rather than hindsight-api's own (32 concurrent calls, 64000
    completion tokens, two layers of 3 retries), which on a slow slot turn one
    stuck extraction into a queue that never drains.

    * ``max_concurrent`` -> ``HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT``: in-flight
      extraction calls against the slot. 1 serialises them.
    * ``max_tokens`` -> ``HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS``: per-call
      completion cap. Must exceed hindsight's retain chunk size (3000) or the
      daemon refuses to start; the schema floor (3072) keeps that unreachable.
    * ``llm_retries`` -> ``HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES``: retries of one
      LLM call inside a retain attempt.
    * ``task_retries`` -> ``HINDSIGHT_API_WORKER_MAX_RETRIES``: requeues of a
      failed queued task before it is marked failed. Worker-wide in hindsight
      (there is no retain-only task retry): consolidation and mental-model
      refresh share it, and the schema description says so.
    * ``retry_backoff_s`` -> ``HINDSIGHT_API_WORKER_TASK_RETRY_BACKOFF_SECONDS``:
      wait before a requeue. Worker-wide likewise.
    """

    max_concurrent: int = 1
    max_tokens: int = 4096
    llm_retries: int = 1
    task_retries: int = 2
    retry_backoff_s: int = 120

    @classmethod
    def from_config(cls, cfg: MemoryGraphConfig) -> ExtractionLimits:
        return cls(
            max_concurrent=cfg.extraction_max_concurrent,
            max_tokens=cfg.extraction_max_tokens,
            llm_retries=cfg.extraction_llm_retries,
            task_retries=cfg.extraction_task_retries,
            retry_backoff_s=cfg.extraction_retry_backoff_s,
        )

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


_DROP_IN_TEMPLATE = (
    "# Managed by hal0 (ADR-0023 — memory.graph.extraction_slot / llm_timeout_s /\n"
    "# extraction limits, #1834). Overrides the hindsight-api LLM target, timeout\n"
    "# and extraction limits set in the base hindsight-api.service unit. Do not\n"
    "# edit by hand; set via `hal0 memory graph enable --slot <name>` or the\n"
    "# dashboard, which rewrites this file and restarts the service.\n"
    "[Service]\n"
    "Environment=HINDSIGHT_API_LLM_MODEL=hal0/{slot}\n"
    "Environment=HINDSIGHT_API_LLM_TIMEOUT={timeout_s}\n"
    "Environment=HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT={max_concurrent}\n"
    "Environment=HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS={max_tokens}\n"
    "Environment=HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES={llm_retries}\n"
    "Environment=HINDSIGHT_API_WORKER_MAX_RETRIES={task_retries}\n"
    "Environment=HINDSIGHT_API_WORKER_TASK_RETRY_BACKOFF_SECONDS={retry_backoff_s}\n"
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


def render_drop_in(
    slot: str,
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    *,
    limits: ExtractionLimits | None = None,
) -> str:
    """Return the drop-in contents pinning extraction to ``hal0/<slot>``, the
    LLM timeout and the extraction limits (``limits`` defaults to the schema
    defaults, see :class:`ExtractionLimits`)."""
    lim = limits if limits is not None else ExtractionLimits()
    return _DROP_IN_TEMPLATE.format(
        slot=slot,
        timeout_s=int(timeout_s),
        max_concurrent=int(lim.max_concurrent),
        max_tokens=int(lim.max_tokens),
        llm_retries=int(lim.llm_retries),
        task_retries=int(lim.task_retries),
        retry_backoff_s=int(lim.retry_backoff_s),
    )


def drop_in_matches(
    slot: str,
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    *,
    limits: ExtractionLimits | None = None,
) -> bool:
    """True when the on-disk drop-in already reflects ``(slot, timeout_s, limits)``.

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
        return DROP_IN_PATH.read_text(encoding="utf-8") == render_drop_in(
            slot, timeout_s, limits=limits
        )
    except (OSError, UnicodeDecodeError):
        return False


def apply_extraction_slot(
    slot: str,
    *,
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    limits: ExtractionLimits | None = None,
    restart: bool = True,
    seam: SystemCtlSeam | None = None,
) -> dict[str, Any]:
    """Write the drop-in for ``slot`` and (best-effort) restart hindsight-api.

    Returns a status dict::

        {"slot", "model", "timeout_s", "limits", "drop_in", "written",
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
    lim = limits if limits is not None else ExtractionLimits()
    result: dict[str, Any] = {
        "slot": slot,
        "model": model,
        "timeout_s": int(timeout_s),
        "limits": lim.as_dict(),
        "drop_in": str(DROP_IN_PATH),
        "written": False,
        "daemon_reloaded": False,
        "restarted": False,
        "error": None,
    }

    try:
        seam.write_hindsight_dropin(
            render_drop_in(slot, timeout_s, limits=lim),
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


def restart_hindsight_background(*, seam: SystemCtlSeam | None = None) -> bool:
    """Restart ``hindsight-api`` through the privileged seam, fire-and-forget.

    For callers that change something the engine only reads at start (e.g. the
    rotated client key in ``hindsight-llm.env``, #2448) as a side effect of an
    unrelated response — passed straight to ``BackgroundTasks.add_task``. Uses
    the existing ``svc-restart hindsight`` seam verb (no new privilege). Never
    raises: a failure is logged and returned as ``False``.
    """
    seam = seam if seam is not None else SystemCtlSeam()
    try:
        seam.systemctl("systemctl", "restart", SERVICE, check=True, timeout=_SYSTEMCTL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning(
            "hal0.memory.hindsight_restart_failed",
            error=_detail(exc),
            remedy=f"run 'systemctl restart {SERVICE}' by hand",
        )
        return False
    log.info("hal0.memory.hindsight_restarted")
    return True


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
    (slot, profile, effective window, binding ctx_size, fix command),
    memory-extraction words.
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
                f"name the profile to raise — run `hal0 slot list` and raise the profile "
                f"ctx_size of the slot behind {self.model!r} to at least {self.floor:,}."
            )
        if self.ceiling_is_binding:
            return (
                f"{head} The limit is the slot profile's own window: "
                f"{self._profile_note} sets ctx_size = {self.ceiling:,} "
                f"(slot file {self.slot_path}). Fix: {self.fix_command}"
            )
        ceiling_note = (
            f"{self._profile_note} sets ctx_size = {self.ceiling:,}, which is not the limit; "
            if self.ceiling is not None
            else f"{self._profile_note} sets no ctx_size; "
        )
        return (
            f"{head} The limit is the model behind the slot — {ceiling_note}"
            f"the window comes from the model, which only advertises {self.effective:,}. "
            f"Raise the slot profile's ctx_size to at least {self.floor:,} "
            f"({self.fix_command}), or point slot {self.slot!r} at a model whose window "
            f"is at least {self.floor:,} (`hal0 slot edit {self.slot} --model <model-id>`)."
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
        profile=window.profile,
    )


# ── extraction throughput preflight (#2353) ────────────────────────────────────
#
# The context-window floor above answers "can the slot fit the prompt at all";
# this answers "can it finish one extraction before hindsight-api gives up".
# A CPU-only slot at ~12 prompt tok/s needs ~270 s just to prefill the ~3.2k-
# token extraction prompt (#1834), so against the 300 s default
# ``[memory.graph].llm_timeout_s`` every attempt times out, and the operator
# only finds out after the retry ladder has run dry, from an empty bank.
#
# Operator decision on #2353: warn, never refuse; at most one bounded probe.
# Prefer the slot's own llama-server metrics; only when they carry no reading,
# run one completion of at most 512 prompt + 32 decode tokens, capped at 30 s.

#: Prompt-token cost the projection charges one extraction call. Same
#: rationale as :data:`EXTRACTION_MIN_CONTEXT_TOKENS` (hal0 never sees the
#: vendored prompt template): rc.6 observed ~3-4k tokens for the prompt alone
#: and #1834 measured ~3.2k. The round figure the issue's formula names.
EXTRACTION_PROMPT_TOKENS = 3500

#: Probe budget (#2353 decision): one completion, these caps, nothing more.
PROBE_PROMPT_TOKENS = 512
PROBE_DECODE_TOKENS = 32
PROBE_TIMEOUT_S = 30.0

#: Bound on the ``/metrics`` read that may let the probe be skipped.
_METRICS_TIMEOUT_S = 2.0

#: Filler for the probe prompt: ~10 tokens a sentence on common tokenizers, so
#: 45 repeats stays under the 512-token cap. The projection uses the rate the
#: server reports, not this estimate.
_PROBE_SENTENCE = "The quick brown fox jumps over the lazy dog. "
_PROBE_REPEATS = 45

_PROMPT_TPS_METRIC = "llamacpp:prompt_tokens_seconds"
_DECODE_TPS_METRIC = "llamacpp:predicted_tokens_seconds"
_BUSY_METRICS = ("llamacpp:requests_processing", "llamacpp:requests_deferred")


@dataclass(frozen=True)
class ExtractionThroughput:
    """Measured slot throughput projected onto one extraction call (#2353).

    ``prompt_tps`` / ``decode_tps`` are what the slot measured; ``source``
    says where from (``metrics``, ``probe``, or ``none`` when nothing could be
    read, with the reason in ``detail``). ``probe_timed_out`` marks a probe
    that hit :data:`PROBE_TIMEOUT_S`: the slot then prefilled fewer than
    ``PROBE_PROMPT_TOKENS / PROBE_TIMEOUT_S`` tokens a second, and
    ``prompt_tps`` holds that upper bound.

    The projection is ``EXTRACTION_PROMPT_TOKENS / prompt_tps + max_tokens /
    decode_tps`` against ``timeout_s``, the formula on the issue.
    ``max_tokens`` is ``[memory.graph].extraction_max_tokens``, the completion
    cap hindsight-api runs under, so the decode term is the worst case.
    """

    slot: str
    timeout_s: int
    max_tokens: int
    prompt_tps: float | None = None
    decode_tps: float | None = None
    source: str = "none"
    probe_timed_out: bool = False
    measured_at: float = 0.0
    detail: str = ""

    @property
    def projected_s(self) -> float | None:
        """Projected seconds for one extraction, or ``None`` when unmeasured.

        After a timed-out probe only the prefill term is known (and only as a
        lower bound), so the projection is that lower bound alone.
        """
        if not self.prompt_tps or self.prompt_tps <= 0:
            return None
        prefill = EXTRACTION_PROMPT_TOKENS / self.prompt_tps
        if self.probe_timed_out:
            return prefill
        if not self.decode_tps or self.decode_tps <= 0:
            return None
        return prefill + self.max_tokens / self.decode_tps

    @property
    def verdict(self) -> str:
        """``ok`` | ``too_slow`` | ``unknown``."""
        projected = self.projected_s
        if projected is None:
            return "unknown"
        if projected > self.timeout_s:
            return "too_slow"
        return "unknown" if self.probe_timed_out else "ok"

    def reproject(self, *, timeout_s: int, max_tokens: int) -> ExtractionThroughput:
        """The same measurement projected against a new timeout / completion cap."""
        return replace(self, timeout_s=int(timeout_s), max_tokens=int(max_tokens))

    def _remedy(self) -> str:
        return (
            "Point extraction at a faster slot (`hal0 memory graph enable --slot "
            "<name>`), raise [memory.graph].llm_timeout_s, or lower "
            "[memory.graph].extraction_max_tokens."
        )

    def message(self) -> str:
        """One operator-facing sentence naming the measured rates and the timeout."""
        timeout = f"[memory.graph].llm_timeout_s = {self.timeout_s} s"
        projected = self.projected_s
        if projected is None:
            why = f" ({self.detail})" if self.detail else ""
            return (
                f"could not measure extraction slot {self.slot!r}'s throughput{why}, "
                f"so hal0 cannot tell whether one extraction fits inside {timeout}"
            )
        consequence = (
            "Retains will be accepted, time out in extraction and land under Retry "
            f"failed once the retries run out. {self._remedy()}"
        )
        if self.probe_timed_out:
            head = (
                f"extraction slot {self.slot!r} did not finish a "
                f"{PROBE_PROMPT_TOKENS}-token probe inside {PROBE_TIMEOUT_S:.0f} s "
                f"(under {self.prompt_tps:.1f} prompt tok/s), so prefilling the "
                f"~{EXTRACTION_PROMPT_TOKENS}-token extraction prompt alone takes "
                f"over {projected:.0f} s"
            )
            if self.verdict == "too_slow":
                return f"{head}, more than {timeout}. {consequence}"
            return (
                f"{head}; with decoding on top, one extraction may not fit inside "
                f"{timeout} (or the slot was busy during the probe). {self._remedy()}"
            )
        rates = (
            f"extraction slot {self.slot!r} measured {self.prompt_tps:.1f} prompt tok/s "
            f"and {self.decode_tps:.1f} decode tok/s (from {self.source}); one "
            f"extraction (~{EXTRACTION_PROMPT_TOKENS} prompt + up to {self.max_tokens} "
            f"completion tokens) projects to ~{projected:.0f} s"
        )
        if self.verdict == "too_slow":
            return f"{rates}, over {timeout}. {consequence}"
        return f"{rates}, inside {timeout}"

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        projected = self.projected_s
        out["projected_s"] = round(projected, 1) if projected is not None else None
        out["prompt_tokens"] = EXTRACTION_PROMPT_TOKENS
        out["verdict"] = self.verdict
        out["message"] = self.message()
        return out


def parse_throughput_metrics(text: str) -> tuple[float | None, float | None]:
    """``(prompt_tps, decode_tps)`` from a llama-server ``/metrics`` body.

    A gauge that is absent, unparseable or zero (llama-server reports 0 for a
    window with no work in it) comes back ``None``.
    """
    found: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2 or parts[0] not in (_PROMPT_TPS_METRIC, _DECODE_TPS_METRIC):
            continue
        try:
            value = float(parts[1])
        except ValueError:
            continue
        if value > 0:
            found[parts[0]] = value
    return found.get(_PROMPT_TPS_METRIC), found.get(_DECODE_TPS_METRIC)


def parse_busy_requests(text: str) -> int:
    """Requests a llama-server ``/metrics`` body says are running or queued."""
    busy = 0.0
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2 or parts[0] not in _BUSY_METRICS:
            continue
        try:
            busy += max(float(parts[1]), 0.0)
        except ValueError:
            continue
    return int(busy)


def _probe_body() -> dict[str, Any]:
    # /v1/chat/completions: the route extraction itself uses, and the only one
    # FLM slots serve.
    return {
        "messages": [{"role": "user", "content": _PROBE_SENTENCE * _PROBE_REPEATS}],
        "max_tokens": PROBE_DECODE_TOKENS,
        "temperature": 0,
        "stream": False,
        # Decode the whole budget, and prefill the whole prompt every time: a
        # prompt cached by an earlier probe would read as a fast slot.
        "ignore_eos": True,
        "cache_prompt": False,
    }


def _rates_from_probe(payload: Any, elapsed_s: float) -> tuple[float | None, float | None]:
    """Prompt and decode tok/s from a completion response.

    llama-server's ``timings`` split the two phases. Without them (another
    engine) fall back to the usage counts over wall-clock time, which charges
    each phase the whole elapsed time: conservative, never optimistic.
    """
    if not isinstance(payload, dict):
        return (None, None)
    timings = payload.get("timings")
    if isinstance(timings, dict):
        try:
            p_tps = float(timings.get("prompt_per_second") or 0)
            d_tps = float(timings.get("predicted_per_second") or 0)
        except (TypeError, ValueError):
            p_tps = d_tps = 0.0
        if p_tps > 0 and d_tps > 0:
            return (p_tps, d_tps)
    usage = payload.get("usage")
    if isinstance(usage, dict) and elapsed_s > 0:
        try:
            p_n = int(usage.get("prompt_tokens") or 0)
            d_n = int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            p_n = d_n = 0
        if p_n > 0 and d_n > 0:
            return (p_n / elapsed_s, d_n / elapsed_s)
    return (None, None)


async def measure_extraction_throughput(
    slot: str,
    port: int,
    *,
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    max_tokens: int = ExtractionLimits.max_tokens,
    host: str = "127.0.0.1",
    transport: Any = None,
    clock: Callable[[], float] = time.monotonic,
) -> ExtractionThroughput:
    """Measure ``slot``'s throughput and project one extraction onto it (#2353).

    Reads the slot's llama-server ``/metrics`` first. Only when that carries
    no non-zero prompt AND decode rate does it run the one bounded probe:
    ``PROBE_PROMPT_TOKENS`` prompt + ``PROBE_DECODE_TOKENS`` decode tokens,
    capped at ``PROBE_TIMEOUT_S``. Never raises: an unreachable slot comes back
    as an ``unknown`` estimate with the reason in ``detail``.

    ``transport`` is an ``httpx`` transport injection point for tests.
    """
    import httpx

    from hal0.http_client import async_client

    def _result(**kw: Any) -> ExtractionThroughput:
        return ExtractionThroughput(
            slot=slot,
            timeout_s=int(timeout_s),
            max_tokens=int(max_tokens),
            measured_at=time.time(),
            **kw,
        )

    if port <= 0:
        return _result(detail="the slot has no port to measure")

    base = f"http://{host}:{port}"
    kwargs: dict[str, Any] = {"timeout": httpx.Timeout(_METRICS_TIMEOUT_S)}
    if transport is not None:
        kwargs["transport"] = transport
    async with async_client(**kwargs) as client:
        try:
            resp = await client.get(f"{base}/metrics")
            if resp.status_code == 200:
                p_tps, d_tps = parse_throughput_metrics(resp.text)
                if p_tps and d_tps:
                    return _result(prompt_tps=p_tps, decode_tps=d_tps, source="metrics")
                busy = parse_busy_requests(resp.text)
                if busy > 0:
                    # A one-request slot working through an extraction would
                    # queue the probe, and a timeout there would read as a
                    # slow slot. Measure nothing rather than mislead.
                    return _result(
                        detail=f"the slot is busy with {busy} request(s), so no probe was run"
                    )
        except (httpx.HTTPError, OSError):
            pass

        started = clock()
        try:
            resp = await client.post(
                f"{base}/v1/chat/completions",
                json=_probe_body(),
                timeout=httpx.Timeout(PROBE_TIMEOUT_S),
            )
        except httpx.TimeoutException:
            return _result(
                prompt_tps=PROBE_PROMPT_TOKENS / PROBE_TIMEOUT_S,
                source="probe",
                probe_timed_out=True,
                detail=f"probe exceeded {PROBE_TIMEOUT_S:.0f} s",
            )
        except (httpx.HTTPError, OSError) as exc:
            return _result(detail=f"probe failed: {exc.__class__.__name__}")
        elapsed = clock() - started
        if resp.status_code != 200:
            return _result(
                detail=f"probe returned HTTP {resp.status_code}; is a model loaded on the slot?"
            )
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        p_tps, d_tps = _rates_from_probe(payload, elapsed)
        if p_tps is None or d_tps is None:
            return _result(detail="the probe response carried no timings or token usage")
        return _result(prompt_tps=p_tps, decode_tps=d_tps, source="probe")


__all__ = [
    "DROP_IN_PATH",
    "EXTRACTION_FLOOR_ENV",
    "EXTRACTION_MIN_CONTEXT_TOKENS",
    "EXTRACTION_PROMPT_TOKENS",
    "PROBE_DECODE_TOKENS",
    "PROBE_PROMPT_TOKENS",
    "PROBE_TIMEOUT_S",
    "ExtractionThroughput",
    "ExtractionWindow",
    "apply_extraction_slot",
    "drop_in_matches",
    "extraction_floor",
    "extraction_model_name",
    "measure_extraction_throughput",
    "parse_busy_requests",
    "parse_throughput_metrics",
    "render_drop_in",
    "resolve_extraction_window",
]
