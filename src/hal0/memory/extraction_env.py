"""Propagate the memory engine's LLM settings to hindsight-api (ADR-0023, #2919).

Hindsight builds its graph natively via its own extraction LLM, configured by the
``HINDSIGHT_API_LLM_MODEL`` env in the ``hindsight-api.service`` unit. To make the
target operator-selectable WITHOUT hand-editing the installer-owned base unit, hal0
owns a systemd **drop-in**::

    /etc/systemd/system/hindsight-api.service.d/extraction-model.conf
        [Service]
        Environment=HINDSIGHT_API_LLM_MODEL=hal0/memory
        Environment=HINDSIGHT_API_LLM_TIMEOUT=...   (+ the #1834 extraction limits)

and runs ``systemctl daemon-reload`` + ``systemctl restart hindsight-api`` so the
engine picks up a changed timeout or limit. The model line is the one fixed
virtual id :data:`MEMORY_ALIAS`: hal0's ``/v1`` resolves ``hal0/memory`` at request
time from ``[memory.graph]`` — to ``hal0/<extraction_slot>`` (ADR-0023 §2, a local
slot) or, when ``extraction_model`` is set, to that upstream model id
(:func:`memory_engine_target`). So the engine never learns which model it runs
on, switching between a local slot and an upstream model (OpenRouter, MiniMax, …)
is a hal0.toml write that applies on the next engine call, and the drop-in only
changes — and the engine only restarts — for the timeout and the limits. Before
#2919 the drop-in carried ``hal0/<slot>`` and every slot change cost a restart.

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

import math
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from hal0.agents.anchor_window import (
    SLOTS_DIR,
    AnchorWindow,
    resolve_anchor_window,
)
from hal0.memory import embedder_record
from hal0.memory.embedder_record import (
    EMBEDDINGS_MODEL_ENV,
    EMBEDDINGS_PROVIDER_ENV,
    Embedder,
    configured_embedder,
    ensure_embedder_record,
    persist_embedder_record,
    resolve_embedder,
)
from hal0.system.seam import HINDSIGHT_LEGACY_CAP_DROPIN_NAME, SeamTimeout, SystemCtlSeam

if TYPE_CHECKING:  # pragma: no cover
    from hal0.config.schema import (
        MemoryConfig,
        MemoryDecisionConfig,
        MemoryGraphConfig,
        MemoryLaneOpConfig,
        MemoryLanesConfig,
        MemoryModelsConfig,
    )

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

#: The one virtual model id hindsight-api is ever pointed at (#2919). Reserved
#: like ``hal0/agent`` / ``hal0/utility``: hal0's ``/v1`` alias rewrite resolves it
#: from ``[memory.graph]`` on every call (see :func:`memory_engine_target`), so a
#: slot literally named ``memory`` is not reachable through it. Spelled so it
#: passes the root-side drop-in allow-list (``^hal0/[A-Za-z0-9_.-]{1,64}$`` in
#: installer/wrappers/hal0-systemctl), which is what lets an upstream target
#: work without an installer change.
MEMORY_ALIAS = "hal0/memory"

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

    * ``max_concurrent`` -> ``HINDSIGHT_API_LLM_MAX_CONCURRENT`` and
      ``HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT``: in-flight LLM calls, including
      consolidation, reflect and mental-model refresh. The slot ``--parallel``
      clamp applies to both rendered values. 1 serialises them, so a long
      reflect or consolidation can delay retains until completion or timeout.
      The configured value remains in ``hal0.toml``; the rendered value is held
      to the extraction slot's ``--parallel`` by :func:`effective_limits`
      (#3347), because calls queued inside llama-server still spend their
      read timeout.
    * ``max_tokens`` -> ``HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS``: per-call
      completion cap. Hindsight refuses every retain into a bank whose chunk
      size is not strictly below it: the schema floor (3072) clears the engine
      default (3000), the 16384 default clears the coding-agent plugin's 12000
      strategy chunks, and ``retain_chunk_guard`` warns about banks it misses.
    * ``llm_retries`` -> ``HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES``: retries of one
      LLM call inside a retain attempt.
    * ``task_retries`` -> ``HINDSIGHT_API_WORKER_MAX_RETRIES``: requeues of a
      failed queued task before it is marked failed. Worker-wide in hindsight
      (there is no retain-only task retry): consolidation and mental-model
      refresh share it, and the schema description says so.
    * ``retry_backoff_s`` -> ``HINDSIGHT_API_WORKER_TASK_RETRY_BACKOFF_SECONDS``:
      wait before a requeue. Worker-wide likewise.

    ``HINDSIGHT_API_RETAIN_WALL_TIMEOUT`` is not a field: it is derived from
    ``llm_timeout_s`` and ``llm_retries`` by :func:`retain_wall_timeout_s`.
    """

    max_concurrent: int = 1
    max_tokens: int = 16384
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


#: Floor for the retain wall limit: hindsight-api's own default (1 h). hal0 never
#: lowers the limit below what the engine would have used.
RETAIN_WALL_FLOOR_S = 3600
#: Soft upper bound (4 h). One document holds the single extraction lane for at
#: most this long, unless the operator's own per-call budget already exceeds it
#: (see :func:`retain_wall_timeout_s`).
RETAIN_WALL_CEILING_S = 14400
#: A document may need this many worst-case calls (chunks) before the wall limit.
RETAIN_WALL_CALL_BUDGET = 3


def retain_wall_timeout_s(timeout_s: int, llm_retries: int) -> int:
    """Value for ``HINDSIGHT_API_RETAIN_WALL_TIMEOUT`` (seconds, #3214).

    hindsight-api cancels a retain task that runs past this limit and marks it
    failed, with no retry. One extraction call may take ``timeout_s`` per
    attempt and is attempted ``llm_retries + 1`` times, so the limit must be
    strictly above that per-call budget or it cuts a call mid-flight::

        per_call = timeout_s * (llm_retries + 1)
        wall = max(min(max(3600, 3 * per_call), 14400), per_call + timeout_s)

    That is: three worst-case calls' worth, never below the engine's own 1 h,
    capped at 4 h so one document cannot hold the single extraction lane for
    longer — except that the result is always at least one further ``timeout_s``
    above ``per_call``, so a large operator-chosen timeout (up to 3600 s x 6
    attempts) raises the cap rather than racing the limit. Defaults (300 s, 1
    retry) give 3600 s.
    """
    per_call = int(timeout_s) * (int(llm_retries) + 1)
    bounded = min(
        max(RETAIN_WALL_FLOOR_S, RETAIN_WALL_CALL_BUDGET * per_call), RETAIN_WALL_CEILING_S
    )
    return max(bounded, per_call + int(timeout_s))


_DROP_IN_TEMPLATE = (
    "# Managed by hal0 (ADR-0023 / #2919 — memory.graph llm_timeout_s and the\n"
    "# #1834 extraction limits). Overrides the hindsight-api LLM target, timeout\n"
    "# and extraction limits set in the base hindsight-api.service unit. The model\n"
    "# is always the virtual id hal0/memory: hal0's /v1 resolves it to\n"
    "# [memory.graph].extraction_slot or extraction_model on every call, so the\n"
    "# engine target changes without touching this file. Do not edit by hand; set\n"
    "# via `hal0 memory graph enable` or the dashboard, which rewrite this file\n"
    "# and restart the service when a timeout or limit changes.\n"
    "[Service]\n"
    "Environment=HINDSIGHT_API_LLM_MODEL=" + MEMORY_ALIAS + "\n"
    "Environment=HINDSIGHT_API_LLM_TIMEOUT={timeout_s}\n"
    "Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT={max_concurrent}\n"
    "Environment=HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT={max_concurrent}\n"
    "Environment=HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS={max_tokens}\n"
    "Environment=HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES={llm_retries}\n"
    "Environment=HINDSIGHT_API_WORKER_MAX_RETRIES={task_retries}\n"
    "Environment=HINDSIGHT_API_WORKER_TASK_RETRY_BACKOFF_SECONDS={retry_backoff_s}\n"
    "Environment=HINDSIGHT_API_RETAIN_WALL_TIMEOUT={wall_timeout_s}\n"
)


#: Second, additive drop-in for the Hindsight 1.2 showcase config
#: ([memory.models]/[memory.decision]/[memory.lanes]) — kept separate from
#: ``extraction-model.conf`` (which owns the hal0/memory alias + #1834
#: extraction limits and is rewritten by the `hal0 memory graph` path) so the
#: two renderers stay independently testable and a graph-only PUT never
#: clobbers the showcase lanes, or vice versa. systemd merges all `.conf`
#: files in the `.d` dir, last-Environment-line-wins on duplicate keys, so the
#: load order between the two is irrelevant: they never set the same key.
SHOWCASE_DROP_IN_PATH = DROP_IN_DIR / "showcase-lanes.conf"

#: The local decision-model unit (``installer/systemd/hal0-hs-decide.service``).
#: The installer does not lay it down yet, so its presence is what says the
#: ``provider = "local"`` decision lane can answer at all (#3519).
DECISION_UNIT_PATH = Path("/etc/systemd/system/hal0-hs-decide.service")

#: Base URL of the hosted decision model (``provider = "hindsight-cloud"``).
HINDSIGHT_CLOUD_DECISION_URL = "https://api.hindsight.ai"

#: The decision base URLs the root-side ``write-hindsight-showcase-dropin``
#: allow-list accepts (``installer/wrappers/hal0-systemctl``): a loopback port
#: or the hosted API. Anything else would get the whole drop-in refused.
_ENGINE_DECISION_URL_RE = re.compile(r"^http://127\.0\.0\.1:[0-9]{1,5}$")


def decision_url_renderable(url: str) -> bool:
    """True when the showcase drop-in can carry ``url`` as the decision base URL."""
    return bool(_ENGINE_DECISION_URL_RE.match(url)) or url == HINDSIGHT_CLOUD_DECISION_URL


def decision_unit_installed(path: Path | None = None) -> bool:
    """True when the ``hal0-hs-decide`` unit file is installed on this box."""
    try:
        return (path if path is not None else DECISION_UNIT_PATH).is_file()
    except OSError:
        return False


def decision_engine_base_url(
    decision: MemoryDecisionConfig, *, unit_installed: bool | None = None
) -> str | None:
    """Where hindsight-api should send decision calls, or ``None`` for no decision model.

    * disabled, or ``provider = "off"`` -> ``None``;
    * an explicit ``base_url`` -> that URL, as configured, when the drop-in
      allow-list accepts it (:func:`decision_url_renderable`); a URL it would
      refuse renders no decision env rather than failing the whole write;
    * ``provider = "local"`` -> ``http://127.0.0.1:<port>`` only when the
      ``hal0-hs-decide`` unit is installed (``unit_installed``, probed from
      :data:`DECISION_UNIT_PATH` when ``None``). Pointing the engine at a port
      nothing serves makes every decision call fail, retry, and fall back to
      the LLM lane (#3519); with no decision env the engine takes that LLM
      path directly;
    * ``provider = "hindsight-cloud"`` -> :data:`HINDSIGHT_CLOUD_DECISION_URL`.
    """
    if not decision.enabled or decision.provider == "off":
        return None
    if decision.base_url:
        url = decision.base_url.rstrip("/")
        return url if decision_url_renderable(url) else None
    if decision.provider == "local":
        installed = decision_unit_installed() if unit_installed is None else unit_installed
        return f"http://127.0.0.1:{decision.port}" if installed else None
    return HINDSIGHT_CLOUD_DECISION_URL


#: The hand-maintained H0-98 workaround drop-in that the showcase drop-in
#: replaces (#3520). It sorts after ``showcase-lanes.conf``, so while it exists
#: its ``HINDSIGHT_API_LLM_MAX_CONCURRENT`` beats the one hal0 renders. The
#: privileged writer removes exactly this filename when it installs the
#: showcase drop-in; nothing else in the directory is ever touched.
LEGACY_LLM_CAP_DROP_IN_PATH = DROP_IN_DIR / HINDSIGHT_LEGACY_CAP_DROPIN_NAME

#: Op name -> HINDSIGHT_API_<PREFIX>_LLM_* env prefix.
LANE_OP_ENV_PREFIX = {
    "retain": "RETAIN",
    "consolidation": "CONSOLIDATION",
    "reflect": "REFLECT",
    "mental_model_refresh": "MENTAL_MODEL_REFRESH",
}


def render_showcase_env(
    *,
    models: MemoryModelsConfig,
    decision: MemoryDecisionConfig,
    lanes: MemoryLanesConfig,
    ceiling: int | None = None,
    decision_unit_installed: bool | None = None,
    embedder: Embedder | None = None,
) -> str:
    """Render the Hindsight 1.2 showcase env block (PLAN.md section 2).

    The embeddings provider and model are the pair the store was built with,
    never ``models.embeddings_provider`` / ``models.embeddings_model``
    (#3518): ``embedder`` when the caller already holds it, else resolved
    here (:func:`hal0.memory.embedder_record.resolve_embedder`: the live
    engine's pair while it runs, the record otherwise, and what the unit
    files and disk say on a box with no record). Swapping the embedder under
    an existing store degrades recall with no error, so a config value that
    differs is reported, not rendered. Every other key below renders from
    config as before.

    ``ceiling`` (#3492) is the extraction drop-in's effective concurrency —
    ``[memory.graph]`` held to the memory slot's ``--parallel``
    (:func:`effective_limits`). This drop-in sorts after
    ``extraction-model.conf``, so its ``HINDSIGHT_API_LLM_MAX_CONCURRENT`` and
    per-op caps are the values the engine runs with; holding the global cap
    and every op that still routes to the memory slot (no ``virtual_model``)
    to the ceiling keeps the slot clamp intact. An op routed elsewhere keeps
    its own cap. ``None`` renders the configured values unclamped.

    Single source for the global LLM concurrency cap
    (``HINDSIGHT_API_LLM_MAX_CONCURRENT``) plus any per-op override — this is
    the H0-98 fix: before this renderer existed, hal0 only ever wrote the
    retain-specific cap (see :data:`_DROP_IN_TEMPLATE`), so consolidation /
    reflect / mental-model-refresh calls queued behind the memory slot's own
    concurrency with no cap of their own, and an operator had to hand-maintain
    ``zz-h0-98-llm-global-cap.conf`` to work around it. The privileged writer
    deletes that hand drop-in whenever it installs this one (#3520).

    The decision-model env is rendered only when
    :func:`decision_engine_base_url` names a server: ``provider == "off"``,
    a disabled lane, or a ``"local"`` lane whose ``hal0-hs-decide`` unit is
    not installed (``decision_unit_installed``, probed when ``None``) omit it
    entirely so hindsight-api falls back to its configured LLM/reranker path
    (#3519).
    """
    if embedder is None:
        embedder = resolve_embedder(configured_embedder(models)).render
    lines: list[str] = [
        "# Managed by hal0 ([memory.models]/[memory.decision]/[memory.lanes] in",
        "# hal0.toml, Hindsight 1.2 showcase — see PLAN.md section 2). Do not edit",
        "# by hand; this file is fully rewritten on every apply. Retires any",
        "# hand-maintained zz-h0-98-llm-global-cap.conf drop-in.",
        "[Service]",
        f"Environment={EMBEDDINGS_PROVIDER_ENV}={embedder.provider}",
        f"Environment={EMBEDDINGS_MODEL_ENV}={embedder.model}",
        f"Environment=HINDSIGHT_API_RERANKER_PROVIDER={models.reranker_provider}",
        f"Environment=HINDSIGHT_API_RERANKER_LOCAL_MODEL={models.reranker_model}",
        "Environment=HINDSIGHT_API_RERANKER_LOCAL_BUCKET_BATCHING="
        + ("true" if models.reranker_bucket_batching else "false"),
    ]

    base_url = decision_engine_base_url(decision, unit_installed=decision_unit_installed)
    if base_url is not None:
        lines += [
            "Environment=HINDSIGHT_API_DECISION_MODEL_PROVIDER=hindsight",
            f"Environment=HINDSIGHT_API_DECISION_MODEL_BASE_URL={base_url}",
            f"Environment=HINDSIGHT_API_DECISION_MODEL_MODEL={decision.model}",
            f"Environment=HINDSIGHT_API_DECISION_MODEL_MAX_CONCURRENT={decision.max_concurrent}",
            "Environment=HINDSIGHT_API_DECISION_MODEL_MAX_REQUEST_TOKENS="
            f"{decision.max_request_tokens}",
            # hindsight-api requires a non-empty key even for a local,
            # unauthenticated base URL (verified on CT143 — see
            # zz-showcase-1.2.conf): omitting it raises a startup ValueError.
            "Environment=HINDSIGHT_API_DECISION_MODEL_API_KEY=hal0-local-noauth",
        ]

    def _held(value: int) -> int:
        return value if ceiling is None else min(value, ceiling)

    global_cap = _held(lanes.llm_max_concurrent)
    lines += [
        f"Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT={global_cap}",
        f"Environment=HINDSIGHT_API_LLM_REASONING_EFFORT={lanes.reasoning_effort}",
        "Environment=HINDSIGHT_API_LLM_STRICT_SCHEMA="
        + ("true" if lanes.strict_schema else "false"),
    ]

    for op, prefix in LANE_OP_ENV_PREFIX.items():
        op_cfg: MemoryLaneOpConfig = getattr(lanes, op)
        max_concurrent = (
            op_cfg.max_concurrent if op_cfg.max_concurrent is not None else lanes.llm_max_concurrent
        )
        if op_cfg.virtual_model is None:
            max_concurrent = _held(max_concurrent)
        lines.append(f"Environment=HINDSIGHT_API_{prefix}_LLM_MAX_CONCURRENT={max_concurrent}")
        if op_cfg.virtual_model is not None:
            lines.append(f"Environment=HINDSIGHT_API_{prefix}_LLM_MODEL={op_cfg.virtual_model}")
        if op_cfg.timeout_s is not None:
            lines.append(f"Environment=HINDSIGHT_API_{prefix}_LLM_TIMEOUT={op_cfg.timeout_s}")
        if op_cfg.reasoning_effort is not None:
            lines.append(
                f"Environment=HINDSIGHT_API_{prefix}_LLM_REASONING_EFFORT={op_cfg.reasoning_effort}"
            )
        if op_cfg.strict_schema is not None:
            lines.append(
                f"Environment=HINDSIGHT_API_{prefix}_LLM_STRICT_SCHEMA="
                + ("true" if op_cfg.strict_schema else "false")
            )

    return "\n".join(lines) + "\n"


def showcase_drop_in_matches(
    *,
    models: MemoryModelsConfig,
    decision: MemoryDecisionConfig,
    lanes: MemoryLanesConfig,
    ceiling: int | None = None,
    unit_env: Mapping[str, str] | None = None,
) -> bool:
    """True when the on-disk showcase drop-in already matches the config.

    With ``unit_env`` (the unit's merged ``Environment=``, see
    :func:`_systemctl_show_environment`), every key this drop-in sets must
    also hold the rendered value there (#3388): a later-sorting drop-in that
    overrides one of them reads as drift even though hal0's own file is
    byte-for-byte right. An empty mapping (systemd absent) cannot refute the
    file and is ignored.
    """
    try:
        body = render_showcase_env(models=models, decision=decision, lanes=lanes, ceiling=ceiling)
        if SHOWCASE_DROP_IN_PATH.read_text(encoding="utf-8") != body:
            return False
    except (OSError, UnicodeDecodeError):
        return False
    return not unit_env or not _env_drift(_env_from_body(body), unit_env)


def _env_from_body(body: str) -> dict[str, str]:
    """``Environment=NAME=VALUE`` lines of a rendered drop-in → ``{NAME: VALUE}``."""
    out: dict[str, str] = {}
    for line in body.splitlines():
        if not line.startswith("Environment="):
            continue
        name, sep, value = line[len("Environment=") :].partition("=")
        if sep and name:
            out[name] = value
    return out


def _env_drift(expected: Mapping[str, str], effective: Mapping[str, str]) -> list[dict[str, Any]]:
    """Keys whose effective value differs from ``expected``, sorted by name."""
    return [
        {"key": key, "expected": value, "effective": effective.get(key)}
        for key, value in sorted(expected.items())
        if effective.get(key) != value
    ]


def _showcase_keys_on_disk(path: Path | None = None) -> set[str]:
    """Names the on-disk showcase drop-in sets (empty when absent/unreadable)."""
    try:
        text = (path or SHOWCASE_DROP_IN_PATH).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    return set(_env_from_body(text))


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
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    *,
    limits: ExtractionLimits | None = None,
) -> str:
    """Return the drop-in contents pinning the engine to :data:`MEMORY_ALIAS`,
    the LLM timeout and the extraction limits (``limits`` defaults to the schema
    defaults, see :class:`ExtractionLimits`)."""
    lim = limits if limits is not None else ExtractionLimits()
    return _DROP_IN_TEMPLATE.format(
        timeout_s=int(timeout_s),
        max_concurrent=int(lim.max_concurrent),
        max_tokens=int(lim.max_tokens),
        llm_retries=int(lim.llm_retries),
        task_retries=int(lim.task_retries),
        retry_backoff_s=int(lim.retry_backoff_s),
        wall_timeout_s=retain_wall_timeout_s(timeout_s, lim.llm_retries),
    )


def drop_in_matches(
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    *,
    limits: ExtractionLimits | None = None,
    unit_env: Mapping[str, str] | None = None,
) -> bool:
    """True when the on-disk drop-in already reflects ``(timeout_s, limits)``
    and pins :data:`MEMORY_ALIAS` (a pre-#2919 drop-in naming ``hal0/<slot>``
    reads as drift, so the next enabled PUT rewrites it once).

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

    #3388: the file alone is not what the engine runs with. With ``unit_env``
    (the unit's merged ``Environment=``), each key this drop-in sets must also
    hold the rendered value there — except the keys hal0's own showcase
    drop-in sets, which legitimately win over this file (it sorts later). An
    operator drop-in overriding a key therefore reads as drift. An empty
    mapping (systemd absent) cannot refute the file and is ignored.
    """
    try:
        body = render_drop_in(timeout_s, limits=limits)
        if DROP_IN_PATH.read_text(encoding="utf-8") != body:
            return False
    except (OSError, UnicodeDecodeError):
        return False
    if not unit_env:
        return True
    shadowed = _showcase_keys_on_disk()
    expected = {k: v for k, v in _env_from_body(body).items() if k not in shadowed}
    return not _env_drift(expected, unit_env)


#: The env file the hindsight-api unit reads (``EnvironmentFile=-``) for its
#: /v1 credential. systemd applies ``EnvironmentFile=`` after every
#: ``Environment=`` line, drop-ins included, so PROVIDER/BASE_URL/MODEL lines an
#: operator adds here silently beat hal0's target (#2918). hal0-api can read it
#: (0640 root:hal0) and reports what the engine actually loads.
HINDSIGHT_LLM_ENV_PATH = Path("/etc/hal0/hindsight-llm.env")

#: The keys that decide which LLM the engine talks to. The credential key is
#: deliberately not among them: it is never read into a status payload.
_LLM_TARGET_KEYS: tuple[str, ...] = (
    "HINDSIGHT_API_LLM_PROVIDER",
    "HINDSIGHT_API_LLM_BASE_URL",
    "HINDSIGHT_API_LLM_MODEL",
)

#: ``systemctl show`` is a subprocess; the dashboard polls status every few
#: seconds, so the unit environment is remembered this long.
_UNIT_ENV_TTL_S = 30.0
_unit_env_cache: tuple[float, dict[str, str]] | None = None


def _parse_systemctl_environment(raw: str) -> dict[str, str]:
    """``systemctl show -p Environment --value`` → ``{KEY: VALUE}``.

    One line of space-separated ``KEY=VALUE`` tokens; a token whose value
    carries spaces is printed quoted as a whole, which ``shlex`` undoes.
    """
    out: dict[str, str] = {}
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    for tok in tokens:
        if "=" not in tok:
            continue
        key, value = tok.split("=", 1)
        if key:
            out[key] = value
    return out


def _systemctl_show_environment() -> dict[str, str]:
    """The merged ``Environment=`` of the hindsight-api unit (base + drop-ins),
    as systemd would hand it to the process — before any ``EnvironmentFile=``.
    Unprivileged read; ``{}`` when systemd is not there or the unit is unknown."""
    global _unit_env_cache
    now = time.monotonic()
    if _unit_env_cache is not None and now - _unit_env_cache[0] < _UNIT_ENV_TTL_S:
        return dict(_unit_env_cache[1])
    try:
        proc = subprocess.run(
            ["systemctl", "show", SERVICE, "-p", "Environment", "--value"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        env = _parse_systemctl_environment(proc.stdout) if proc.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError):
        env = {}
    _unit_env_cache = (now, env)
    return dict(env)


def hindsight_unit_env() -> dict[str, str]:
    """Public read of the hindsight-api unit's merged ``Environment=`` (cached 30 s)."""
    return _systemctl_show_environment()


def _invalidate_unit_env_cache() -> None:
    """Forget the cached unit env — a daemon-reload just changed it."""
    global _unit_env_cache
    _unit_env_cache = None


def _read_env_file(path: Path) -> dict[str, str]:
    """``KEY=VALUE`` lines of a systemd environment file; ``{}`` if unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        item = line.strip()
        if item.startswith("export "):
            item = item[len("export ") :].strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, value = item.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _engine_main_pid() -> int | None:
    """hindsight-api's MainPID, or None when it is not running / systemd absent."""
    try:
        proc = subprocess.run(
            ["systemctl", "show", SERVICE, "-p", "MainPID", "--value"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        pid = int((proc.stdout or "").strip() or "0")
    except ValueError:
        return None
    return pid if proc.returncode == 0 and pid > 0 else None


def _proc_environ_path(pid: int) -> Path:
    return Path(f"/proc/{pid}/environ")


def _read_engine_process_env(
    keys: Sequence[str] = _LLM_TARGET_KEYS,
) -> dict[str, str] | None:
    """``keys`` (the three LLM target keys by default) from the RUNNING engine's environment.

    hindsight-api runs as the same ``hal0`` uid as hal0-api, so its
    ``/proc/<pid>/environ`` is readable without opening
    /etc/hal0/hindsight-llm.env (0600 root:hal0 on some boxes). This is what
    the engine actually loaded, after every ``Environment=`` and
    ``EnvironmentFile=``.

    The raw buffer is parsed in memory and only ``keys`` are
    kept: the credential and every other variable are dropped before
    anything leaves this function, and no failure carries the buffer or the
    OS error text (which can echo it) into a log, an exception or the
    result. Returns ``None`` when the engine is not running or its environ
    cannot be read, ``{}`` when it is readable but sets none of the keys.
    """
    pid = _engine_main_pid()
    if pid is None:
        return None
    try:
        raw = _proc_environ_path(pid).read_bytes()
    except Exception:  # never surface the error text
        log.debug("hal0.memory.engine_environ_unreadable", pid=pid)
        return None
    wanted = set(keys)
    out: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        key, sep, value = entry.partition(b"=")
        if not sep:
            continue
        name = key.decode("utf-8", "replace")
        if name in wanted:
            out[name] = value.decode("utf-8", "replace")
    return out


def effective_engine_llm(
    *,
    env_file: Path = HINDSIGHT_LLM_ENV_PATH,
    unit_env: Mapping[str, str] | None = None,
    show_unit_env: Callable[[], Mapping[str, str]] = _systemctl_show_environment,
    read_process_env: Callable[[], Mapping[str, str] | None] = _read_engine_process_env,
) -> dict[str, Any]:
    """The LLM hindsight-api really runs on, and whether a file override set it (#2918).

    First source of truth is the running engine's own environment
    (``read_process_env``, ``source: "process"``): its three target keys are
    compared with the unit's merged ``Environment=`` (``unit_env`` when given,
    else ``show_unit_env()``); any key that differs came from
    ``EnvironmentFile=`` — the only other source systemd applies — so it is
    reported as an override of ``env_file`` without ever opening that file.

    When the process cannot be read (engine down), the three keys from
    ``env_file`` are overlaid on the unit env, which is what systemd does at
    start (``source: "unit+file"``); if that file exists but cannot be read
    either, ``overridden`` is ``None`` — "cannot verify", never "not
    overridden" — and the values are the unit's (``source: "unit"``).
    Returns::

        {"provider", "base_url", "model",            # as the engine sees them
         "overridden": bool | None,                  # None = cannot verify
         "override_file": str | None, "override_keys": [sorted key names],
         "source": "process" | "unit+file" | "unit"}

    The credential is never read into the result. Everything degrades rather
    than raising — this feeds a status route.
    """
    unit = dict(unit_env) if unit_env is not None else dict(show_unit_env() or {})

    def _shape(
        values: Mapping[str, str],
        overridden: bool | None,
        keys: list[str],
        file_named: bool,
        source: str,
    ) -> dict[str, Any]:
        return {
            "provider": values.get("HINDSIGHT_API_LLM_PROVIDER") or None,
            "base_url": values.get("HINDSIGHT_API_LLM_BASE_URL") or None,
            "model": values.get("HINDSIGHT_API_LLM_MODEL") or None,
            "overridden": overridden,
            "override_file": str(env_file) if file_named else None,
            "override_keys": keys,
            "source": source,
        }

    process = read_process_env()
    if process is not None:
        # A difference is blamed on the env file only when that file exists:
        # without it the engine simply has not restarted onto the unit's
        # current values (a drop-in written but the restart failed, or a
        # daemon-reload alone) — the "still pinned" case, not an override.
        keys = sorted(
            k for k in _LLM_TARGET_KEYS if (process.get(k) or None) != (unit.get(k) or None)
        )
        if not Path(env_file).exists():
            keys = []
        return _shape(process, bool(keys), keys, bool(keys), "process")

    path = Path(env_file)
    if path.exists() and not os.access(path, os.R_OK):
        return _shape(unit, None, [], True, "unit")
    file_env = _read_env_file(path)
    keys = sorted(k for k in _LLM_TARGET_KEYS if file_env.get(k))
    merged = {**unit, **{k: file_env[k] for k in keys}}
    return _shape(merged, bool(keys), keys, bool(keys), "unit+file" if path.exists() else "unit")


#: The two keys that decide which embedder the engine runs (#3518).
_EMBEDDER_KEYS: tuple[str, ...] = (EMBEDDINGS_PROVIDER_ENV, EMBEDDINGS_MODEL_ENV)


def effective_engine_embedder(
    *,
    env_file: Path | None = None,
    unit_env: Mapping[str, str] | None = None,
    show_unit_env: Callable[[], Mapping[str, str]] | None = None,
    read_process_env: Callable[[Sequence[str]], Mapping[str, str] | None] | None = None,
    showcase_path: Path | None = None,
) -> dict[str, Any]:
    """The embedder hindsight-api runs, and the one its unit files would start it on (#3518).

    The embedder counterpart of :func:`effective_engine_llm`, over the same
    three readers, so the embedder record, the lanes route and the
    ``memory_engine_env`` doctor row cannot see different environments.
    Returns::

        {"live": {"provider", "model"} | None,     # the running engine's own environment
         "on_disk": {"provider", "model"} | None,  # what a start would load
         "on_disk_source": "unit+file" | "unit" | "drop-in" | None}

    Each value is the raw one, ``None`` where the environment leaves the key
    unset (the engine then uses its default).

    * ``live`` comes from ``read_process_env`` (``/proc/<MainPID>/environ``):
      what the engine loaded, after every ``Environment=`` and
      ``EnvironmentFile=``. ``None`` when it is not running or cannot be read.
    * ``on_disk`` is the unit's merged ``Environment=`` (``unit_env`` when
      given, else ``show_unit_env()``: base unit plus every drop-in, a
      later-sorting operator one included, in systemd's order) with the two
      keys from ``env_file`` on top, which is what systemd does at start.
    * Where there is no unit to ask (systemd absent, unit unknown), hal0's
      own showcase drop-in file is read instead (``"drop-in"``), and
      ``on_disk`` is ``None`` when that is missing too.

    Never raises: this feeds status routes and ``hal0 doctor``.
    """
    env_file = env_file if env_file is not None else HINDSIGHT_LLM_ENV_PATH
    read_process = read_process_env if read_process_env is not None else _read_engine_process_env
    out: dict[str, Any] = {"live": None, "on_disk": None, "on_disk_source": None}

    def _pair(values: Mapping[str, str]) -> dict[str, str | None]:
        return {
            "provider": values.get(EMBEDDINGS_PROVIDER_ENV) or None,
            "model": values.get(EMBEDDINGS_MODEL_ENV) or None,
        }

    try:
        process = read_process(_EMBEDDER_KEYS)
        if process is not None:
            out["live"] = _pair(process)
        if unit_env is not None:
            unit = dict(unit_env)
        else:
            unit = dict((show_unit_env or _systemctl_show_environment)() or {})
        if unit:
            file_env = _read_env_file(Path(env_file))
            from_file = {k: file_env[k] for k in _EMBEDDER_KEYS if file_env.get(k)}
            out["on_disk"] = _pair({**unit, **from_file})
            out["on_disk_source"] = "unit+file" if from_file else "unit"
            return out
        showcase = showcase_path if showcase_path is not None else SHOWCASE_DROP_IN_PATH
        try:
            body = showcase.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return out
        out["on_disk"] = _pair(_env_from_body(body))
        out["on_disk_source"] = "drop-in"
    except Exception as exc:  # status surface: degrade, never raise
        log.debug("hal0.memory.engine_embedder_evidence_failed", error=str(exc))
    return out


def expected_engine_env(
    memory: MemoryConfig,
    *,
    include_extraction: bool = True,
    include_showcase: bool = True,
    slots_dir: Path = SLOTS_DIR,
    embedder: Embedder | None = None,
) -> dict[str, str]:
    """The ``HINDSIGHT_API_*`` values hal0's drop-ins render for ``memory``.

    ``embedder`` is the pair the showcase drop-in carries, resolved from the
    embedder record when not given (see :func:`render_showcase_env`).

    Both renderers merged in systemd's lexical order — ``extraction-model.conf``
    first, ``showcase-lanes.conf`` after it, last ``Environment=`` wins — so a
    key both set (the concurrency caps) carries the showcase value, exactly as
    the engine would see it with no other drop-in present.
    """
    graph = memory.graph
    limits = effective_limits(graph, slots_dir=slots_dir)
    out: dict[str, str] = {}
    if include_extraction:
        out.update(_env_from_body(render_drop_in(graph.llm_timeout_s, limits=limits)))
    if include_showcase:
        out.update(
            _env_from_body(
                render_showcase_env(
                    models=memory.models,
                    decision=memory.decision,
                    lanes=memory.lanes,
                    ceiling=limits.max_concurrent,
                    embedder=embedder,
                )
            )
        )
    return out


def engine_env_report(
    memory: MemoryConfig,
    *,
    unit_env: Mapping[str, str] | None = None,
    show_unit_env: Callable[[], Mapping[str, str]] = _systemctl_show_environment,
    drop_in_path: Path | None = None,
    showcase_path: Path | None = None,
    legacy_path: Path | None = None,
    slots_dir: Path = SLOTS_DIR,
) -> dict[str, Any]:
    """Does hindsight-api's unit env carry what hal0 renders? (#3388 / #3520 / #3389).

    Compares every ``HINDSIGHT_API_*`` key hal0's drop-ins set (only the
    drop-ins present on this box) with the unit's merged ``Environment=``
    (``unit_env`` when given, else ``show_unit_env()`` — injectable for
    tests). A mismatch means another drop-in overrides hal0's value, or a
    rendered change has not been loaded yet (no ``daemon-reload`` + restart;
    a stale ``--parallel`` clamp shows up here too). Returns::

        {"installed": bool,             # hal0's extraction drop-in exists
         "checked": bool,               # a unit env was available to compare
         "drift": [{"key", "expected", "effective"}],
         "legacy_drop_in": str | None,  # zz-h0-98 file still present
         "effective": {KEY: VALUE}}     # unit values for the managed keys

    Never raises: this feeds a status route and ``hal0 doctor``.
    """
    drop_in = drop_in_path or DROP_IN_PATH
    showcase = showcase_path or SHOWCASE_DROP_IN_PATH
    leftover = legacy_path or LEGACY_LLM_CAP_DROP_IN_PATH
    report: dict[str, Any] = {
        "installed": False,
        "checked": False,
        "drift": [],
        "legacy_drop_in": None,
        "effective": {},
    }
    try:
        report["legacy_drop_in"] = str(leftover) if leftover.exists() else None
        report["installed"] = drop_in.exists()
        if not report["installed"]:
            return report
        unit = dict(unit_env) if unit_env is not None else dict(show_unit_env() or {})
        # #3518: the two embedder keys are judged on the evidence the embedder
        # record uses (the running engine first, then the unit files with the
        # EnvironmentFile on top), so this report and the memory_embedder
        # doctor row cannot disagree about which embedder the engine is on.
        # A key the engine leaves unset is its default, not a missing value.
        view = resolve_embedder(
            configured_embedder(memory.models),
            evidence=embedder_record.engine_evidence(unit_env=unit, showcase_path=showcase),
        )
        running = view.live
        if running is None and view.effective_source in ("unit", "unit+file"):
            running = view.on_disk
        if unit and running is not None:
            unit[EMBEDDINGS_PROVIDER_ENV] = running.provider
            unit[EMBEDDINGS_MODEL_ENV] = running.model
        # Credential keys are never compared: an operator drop-in carrying a
        # real key would otherwise put it into a status payload.
        expected = {
            k: v
            for k, v in expected_engine_env(
                memory,
                include_showcase=showcase.exists(),
                slots_dir=slots_dir,
                embedder=view.render,
            ).items()
            if not k.endswith("_API_KEY")
        }
    except Exception as exc:  # status surface: degrade, never raise
        log.debug("hal0.memory.engine_env_report_failed", error=str(exc))
        return report
    report["effective"] = {k: unit[k] for k in sorted(expected) if k in unit}
    if not unit:
        return report
    report["checked"] = True
    report["drift"] = _env_drift(expected, unit)
    return report


def engine_env_warning(report: Mapping[str, Any]) -> str | None:
    """One operator sentence for an :func:`engine_env_report`, or ``None`` when clean."""
    parts: list[str] = []
    leftover = report.get("legacy_drop_in")
    if leftover:
        parts.append(
            f"{leftover} is still present and overrides hal0's showcase drop-in; "
            "save the memory settings (or run `hal0 update`) to remove it"
        )
    drift = report.get("drift") or []
    if drift:
        shown = ", ".join(
            f"{d['key']}={d.get('effective')} (hal0 renders {d['expected']})" for d in drift[:4]
        )
        more = f" and {len(drift) - 4} more" if len(drift) > 4 else ""
        parts.append(
            f"hindsight-api runs with {shown}{more}: another drop-in in "
            f"{DROP_IN_DIR} overrides it, or the change is not loaded yet "
            "(daemon-reload, then restart hindsight-api)"
        )
    return "; ".join(parts) or None


def memory_engine_target(graph: MemoryGraphConfig) -> tuple[str, str]:
    """What :data:`MEMORY_ALIAS` resolves to under ``graph`` (#2919).

    ``("upstream", <model id>)`` when ``extraction_model`` is set — an id hal0's
    ``/v1`` routes to a configured upstream — else ``("slot", "hal0/<slot>")``,
    the ADR-0023 virtual for the local extraction slot. The ``/v1`` alias
    rewrite, the graph status route and the CLI all ask here, so there is one
    answer to "which model is the memory engine on".
    """
    model = getattr(graph, "extraction_model", None)
    if model:
        return ("upstream", model)
    return ("slot", extraction_model_name(graph.extraction_slot))


def clamp_to_parallel(limits: ExtractionLimits, parallel: int | None) -> ExtractionLimits:
    """``limits`` with ``max_concurrent`` held to the slot's ``--parallel`` (#3347).

    Calls beyond the slot's sequences queue inside llama-server, and that wait
    counts against the caller's read timeout, so a larger value only converts
    capacity the slot does not have into 504s. ``parallel`` of ``None`` (no
    explicit ``--parallel``, an upstream target, an unreadable slot) leaves the
    configured value alone: hal0 has no number to hold it to.
    """
    if parallel is None or parallel < 1 or limits.max_concurrent <= parallel:
        return limits
    return replace(limits, max_concurrent=parallel)


def _extraction_slot_config(name: str, slots_dir: Path) -> dict[str, Any] | None:
    """The extraction slot's on-disk config, flattened; ``None`` when unreadable.

    The slot is resolved through the ONE bilingual authority,
    :func:`hal0.slots.layout.resolve_slot_stem`, so an id-keyed layout
    (``slots/<id>.toml`` with the display name in the body) resolves too. A
    name-keyed-only read found nothing there and left the concurrency clamp
    silently unapplied (#3473).

    Read unvalidated on purpose: this feeds a status route and the update
    reconcile, both of which must degrade, never raise, on a slot file the
    typed loader would reject.
    """
    from hal0.config.loader import _flatten_slot_toml, _read_toml
    from hal0.slots.layout import resolve_slot_stem

    try:
        stem = resolve_slot_stem(slots_dir, name)
        if stem is None:
            return None
        raw = _read_toml(Path(slots_dir) / f"{stem}.toml")
        return _flatten_slot_toml(raw, slot_name=name)
    except Exception:
        log.debug("hal0.memory.extraction_slot_unreadable", slot=name)
        return None


def extraction_slot_parallel(
    graph: MemoryGraphConfig, *, slots_dir: Path = SLOTS_DIR
) -> int | None:
    """The ``--parallel`` the extraction slot launches with, or ``None`` when unknown.

    ``None`` for an upstream target (no local slot is in play), a slot whose TOML
    cannot be read, and a slot that sets no explicit ``--parallel``. Never raises:
    this feeds a status route and the update reconcile.
    """
    kind, _target = memory_engine_target(graph)
    if kind != "slot":
        return None
    raw = _extraction_slot_config(graph.extraction_slot, slots_dir)
    if raw is None:
        return None
    from hal0.providers.container import slot_parallel_sequences

    try:
        return slot_parallel_sequences(raw)
    except Exception:
        log.debug("hal0.memory.extraction_parallel_unresolved", slot=graph.extraction_slot)
        return None


def effective_limits(graph: MemoryGraphConfig, *, slots_dir: Path = SLOTS_DIR) -> ExtractionLimits:
    """The limits the drop-in is rendered from: ``[memory.graph]`` held to the slot (#3347).

    The one answer for the graph PUT, the update reconcile and the status
    route, so the file they write and compare against cannot disagree.
    """
    return clamp_to_parallel(
        ExtractionLimits.from_config(graph), extraction_slot_parallel(graph, slots_dir=slots_dir)
    )


def concurrency_clamp_warning(graph: MemoryGraphConfig, parallel: int | None) -> str | None:
    """Operator-facing sentence when ``extraction_max_concurrent`` exceeds the slot, else ``None``."""
    configured = graph.extraction_max_concurrent
    if parallel is None or configured <= parallel:
        return None
    return (
        f"[memory.graph].extraction_max_concurrent = {configured} is above extraction slot "
        f"{graph.extraction_slot!r}'s --parallel {parallel}; hal0 renders {parallel} into "
        "HINDSIGHT_API_LLM_MAX_CONCURRENT and HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT so "
        "extra calls do not queue inside llama-server and time out. Lower "
        "extraction_max_concurrent, or raise the slot's "
        "--parallel, to make the two agree."
    )


def apply_extraction_slot(
    slot: str,
    *,
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    limits: ExtractionLimits | None = None,
    restart: bool = True,
    seam: SystemCtlSeam | None = None,
) -> dict[str, Any]:
    """Write the drop-in and (best-effort) restart hindsight-api.

    ``slot`` is the configured extraction slot, reported back in the status for
    the caller's log line; since #2919 the drop-in itself pins
    :data:`MEMORY_ALIAS` and hal0's ``/v1`` resolves that to the slot (or to
    ``extraction_model``) per call, so a slot change alone no longer needs this.

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
    model = MEMORY_ALIAS
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
            render_drop_in(timeout_s, limits=lim),
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
            _invalidate_unit_env_cache()
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


def reconcile_extraction_dropin(
    *, restart: bool = True, seam: SystemCtlSeam | None = None
) -> dict[str, Any]:
    """Re-render the extraction drop-in when it no longer matches ``hal0.toml``.

    ``hal0.toml`` is the source of truth, but the drop-in is written only by a
    graph-settings save. A config migration that changes an extraction limit
    (v3 raises a stored ``extraction_max_tokens`` that cannot clear the coding-agent
    plugin's chunks, #3326) would therefore leave the engine on the old cap until
    someone saved the settings by hand.

    Acts only on a box that already has a drop-in: a box without one never ran the
    memory engine, and writing one is install.sh's job. Never raises. Returns
    ``{"status": "skipped"|"converged"|"applied"|"failed", ...}``.
    """
    if not DROP_IN_PATH.exists():
        return {"status": "skipped", "reason": "no drop-in on this box"}
    try:
        from hal0.config.loader import load_hal0_config

        graph = load_hal0_config().memory.graph
    except Exception as exc:
        return {"status": "skipped", "reason": f"hal0.toml unreadable: {_detail(exc)}"}
    limits = effective_limits(graph)
    if drop_in_matches(graph.llm_timeout_s, limits=limits):
        return {"status": "converged"}
    result = apply_extraction_slot(
        graph.extraction_slot,
        timeout_s=graph.llm_timeout_s,
        limits=limits,
        restart=restart,
        seam=seam,
    )
    return {"status": "failed" if result.get("error") else "applied", **result}


def showcase_ceiling(graph: MemoryGraphConfig | None) -> int | None:
    """The concurrency ceiling the showcase drop-in is held to (#3492).

    The extraction drop-in's effective ``max_concurrent`` — the one answer for
    the settings PUT, the graph PUT and the update reconcile. ``None`` (render
    unclamped) only when no graph config is supplied.
    """
    if graph is None:
        return None
    return effective_limits(graph).max_concurrent


def reload_and_restart_hindsight(*, seam: SystemCtlSeam | None = None) -> dict[str, Any]:
    """``daemon-reload`` then ``restart hindsight-api``, in that order. Never raises.

    A drop-in is read only at unit load: restarting without the reload would
    bring the engine back on the old environment. Returns
    ``{"daemon_reloaded", "restarted", "error"}``.
    """
    seam = seam if seam is not None else SystemCtlSeam()
    result: dict[str, Any] = {"daemon_reloaded": False, "restarted": False, "error": None}
    for step, args in (
        ("daemon_reloaded", ("systemctl", "daemon-reload")),
        ("restarted", ("systemctl", "restart", SERVICE)),
    ):
        try:
            seam.systemctl(*args, check=True, timeout=_SYSTEMCTL_TIMEOUT_S)
            result[step] = True
            _invalidate_unit_env_cache()
        except (OSError, subprocess.SubprocessError, SeamTimeout) as exc:
            result["error"] = f"{' '.join(args)} failed: {_detail(exc)}"
            log.warning("hal0.memory.hindsight_reload_restart_failed", step=step, error=str(exc))
            return result
    return result


def apply_showcase_dropin(
    *,
    models: MemoryModelsConfig,
    decision: MemoryDecisionConfig,
    lanes: MemoryLanesConfig,
    graph: MemoryGraphConfig | None = None,
    restart: bool = True,
    seam: SystemCtlSeam | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    """Write the showcase drop-in and (best-effort) restart hindsight-api (#3492).

    The :func:`apply_extraction_slot` shape for ``[memory.models]`` /
    ``[memory.decision]`` / ``[memory.lanes]``: render with
    :func:`render_showcase_env` (held to :func:`showcase_ceiling` of
    ``graph``), write through the privileged ``write-hindsight-showcase-dropin``
    seam verb (allow-listed root-side), then :func:`reload_and_restart_hindsight`
    — the engine reads these env vars only at start. Never raises; returns
    ``{"drop_in", "written", "daemon_reloaded", "restarted", "error"}``.
    Blocking: event-loop callers must hop a thread.

    The embeddings provider and model come from the store's embedder record
    (#3518), or from the running engine when it is on something else, so this
    write never swaps a running engine's embedder. A box whose engine has
    already run gets its record written before the render; a box with no
    store yet is recorded as the configured pair only after the drop-in
    carrying it is on disk, so the record cannot name an embedder the engine
    was never started with. A record file that cannot be read is left alone.

    ``embedder`` renders that pair instead and leaves the record exactly as
    it is. It has one caller, :mod:`hal0.memory.first_start`, which uses it
    to try a new install's engine on the engine default before any store
    exists and then either records that pair or puts the drop-in back. Used
    anywhere else it would swap the embedder under a store.
    """
    seam = seam if seam is not None else SystemCtlSeam()
    result: dict[str, Any] = {
        "drop_in": str(SHOWCASE_DROP_IN_PATH),
        "written": False,
        "daemon_reloaded": False,
        "restarted": False,
        "error": None,
    }
    view = ensure_embedder_record(configured_embedder(models))
    try:
        body = render_showcase_env(
            models=models,
            decision=decision,
            lanes=lanes,
            ceiling=showcase_ceiling(graph),
            embedder=embedder if embedder is not None else view.render,
        )
        seam.write_hindsight_showcase_dropin(
            body, path=SHOWCASE_DROP_IN_PATH, timeout=_SYSTEMCTL_TIMEOUT_S
        )
        result["written"] = True
        if embedder is None and view.record_error is None and not view.record.persisted:
            persist_embedder_record(view.record)
        # #3520: the write also retires the hand-maintained H0-98 drop-in;
        # say so if it survived (e.g. a pre-#3520 seam wrapper on the box).
        result["legacy_drop_in_present"] = LEGACY_LLM_CAP_DROP_IN_PATH.exists()
    except (OSError, subprocess.SubprocessError, SeamTimeout) as exc:
        result["error"] = (
            f"could not write {SHOWCASE_DROP_IN_PATH}: {_detail(exc)}{_stale_wrapper_hint(exc)}"
        )
        log.warning("hal0.memory.showcase_dropin_write_failed", error=str(exc))
        return result

    if not restart:
        return result

    result.update(reload_and_restart_hindsight(seam=seam))
    if result["error"] is None:
        log.info("hal0.memory.showcase_dropin_applied")
    return result


def reconcile_showcase_dropin(
    *, restart: bool = True, seam: SystemCtlSeam | None = None
) -> dict[str, Any]:
    """Re-render the showcase drop-in when it no longer matches ``hal0.toml``.

    Like :func:`reconcile_extraction_dropin`, acts only on a box whose engine
    is installed (the extraction drop-in exists); there it also *creates* the
    showcase drop-in on first run, which is how an existing box picks up the
    1.2 models/decision/lanes env after an update. Never raises. Returns
    ``{"status": "skipped"|"converged"|"applied"|"failed", ...}``.
    """
    if not DROP_IN_PATH.exists():
        return {"status": "skipped", "reason": "no memory engine on this box"}
    try:
        from hal0.config.loader import load_hal0_config

        mem = load_hal0_config().memory
        ceiling = showcase_ceiling(mem.graph)
    except Exception as exc:
        return {"status": "skipped", "reason": f"hal0.toml unreadable: {_detail(exc)}"}
    # #3520: a leftover hand-maintained H0-98 drop-in overrides this one, and
    # only the privileged write removes it — so its presence alone is drift.
    if not LEGACY_LLM_CAP_DROP_IN_PATH.exists() and showcase_drop_in_matches(
        models=mem.models, decision=mem.decision, lanes=mem.lanes, ceiling=ceiling
    ):
        return {"status": "converged"}
    result = apply_showcase_dropin(
        models=mem.models,
        decision=mem.decision,
        lanes=mem.lanes,
        graph=mem.graph,
        restart=restart,
        seam=seam,
    )
    return {"status": "failed" if result.get("error") else "applied", **result}


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
                f"{head} The limit is {self._ceiling_owner_phrase}: "
                f"{self._ceiling_note} sets ctx_size = {self.ceiling:,} "
                f"(slot file {self.slot_path}). Fix: {self.fix_command}"
            )
        ceiling_note = (
            f"{self._ceiling_note} sets ctx_size = {self.ceiling:,}, which is not the limit; "
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
        ceiling_source=window.ceiling_source,
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

#: Completion tokens the projection charges one extraction call at most. The
#: 16384 default cap (#3215) exists so the engine accepts banks with a 12000
#: chunk size, not because a call writes that much. Charging the whole cap would
#: call an ordinary 45 tok/s GPU slot too slow against the default 300 s timeout.
EXTRACTION_PROJECTED_COMPLETION_TOKENS = 4096

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
    that hit :data:`PROBE_TIMEOUT_S`. Only when metrics reported idle does
    ``prompt_tps`` hold the upper bound
    ``PROBE_PROMPT_TOKENS / PROBE_TIMEOUT_S``; without a busy signal a queued
    request cannot be distinguished from slow prefill, so no rate is inferred.

    The projection is ``EXTRACTION_PROMPT_TOKENS / prompt_tps + completion /
    decode_tps`` against ``timeout_s``, where ``completion`` is the smaller of
    ``max_tokens`` (``[memory.graph].extraction_max_tokens``, the cap
    hindsight-api runs under) and :data:`EXTRACTION_PROJECTED_COMPLETION_TOKENS`.
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

        After a timed-out probe on an idle slot only the prefill term is known
        (and only as a lower bound), so the projection is that lower bound alone.
        """
        if not self.prompt_tps or self.prompt_tps <= 0:
            return None
        prefill = EXTRACTION_PROMPT_TOKENS / self.prompt_tps
        if self.probe_timed_out:
            return prefill
        if not self.decode_tps or self.decode_tps <= 0:
            return None
        return prefill + self._projected_completion_tokens / self.decode_tps

    @property
    def _projected_completion_tokens(self) -> int:
        return min(self.max_tokens, EXTRACTION_PROJECTED_COMPLETION_TOKENS)

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
            "<name>`) or raise [memory.graph].llm_timeout_s."
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
            f"extraction (~{EXTRACTION_PROMPT_TOKENS} prompt + "
            f"~{self._projected_completion_tokens} completion tokens) projects to ~{projected:.0f} s"
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


def parse_busy_requests(text: str) -> int | None:
    """Running/queued requests, or ``None`` when no usable busy signal exists."""
    busy = 0.0
    found = False
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2 or parts[0] not in _BUSY_METRICS:
            continue
        try:
            value = float(parts[1])
        except ValueError:
            return None
        if not math.isfinite(value) or value < 0:
            return None
        found = True
        busy += value
    return math.ceil(busy) if found else None


def _probe_body(model: str | None) -> dict[str, Any]:
    # /v1/chat/completions: the route extraction itself uses, and the only one
    # FLM slots serve.
    body = {
        "messages": [{"role": "user", "content": _PROBE_SENTENCE * _PROBE_REPEATS}],
        "max_tokens": PROBE_DECODE_TOKENS,
        "temperature": 0,
        "stream": False,
        # Decode the whole budget, and prefill the whole prompt every time: a
        # prompt cached by an earlier probe would read as a fast slot.
        "ignore_eos": True,
        "cache_prompt": False,
    }
    if model:
        body["model"] = model
    return body


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
    model: str | None,
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

    ``model`` is the slot's model id, omitted from the request when unknown.
    A timeout without a usable metrics busy signal is unknown: the probe may
    have queued behind another request rather than measured slow prefill.

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
        busy: int | None = None
        try:
            resp = await client.get(f"{base}/metrics")
            if resp.status_code == 200:
                p_tps, d_tps = parse_throughput_metrics(resp.text)
                if p_tps and d_tps:
                    return _result(prompt_tps=p_tps, decode_tps=d_tps, source="metrics")
                busy = parse_busy_requests(resp.text)
                if busy is not None and busy > 0:
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
                json=_probe_body(model),
                timeout=httpx.Timeout(PROBE_TIMEOUT_S),
            )
        except httpx.TimeoutException:
            if busy is None:
                return _result(
                    source="probe",
                    probe_timed_out=True,
                    detail=(
                        f"probe exceeded {PROBE_TIMEOUT_S:.0f} s; no usable /metrics busy "
                        "signal was available, so it may have queued behind another request; "
                        "retry when the slot is idle"
                    ),
                )
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
                detail=f"probe returned HTTP {resp.status_code}; the slot rejected the request"
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
    "LEGACY_LLM_CAP_DROP_IN_PATH",
    "PROBE_DECODE_TOKENS",
    "PROBE_PROMPT_TOKENS",
    "PROBE_TIMEOUT_S",
    "SHOWCASE_DROP_IN_PATH",
    "ExtractionThroughput",
    "ExtractionWindow",
    "apply_extraction_slot",
    "apply_showcase_dropin",
    "drop_in_matches",
    "engine_env_report",
    "engine_env_warning",
    "expected_engine_env",
    "extraction_floor",
    "extraction_model_name",
    "hindsight_unit_env",
    "measure_extraction_throughput",
    "parse_busy_requests",
    "parse_throughput_metrics",
    "reconcile_extraction_dropin",
    "reconcile_showcase_dropin",
    "reload_and_restart_hindsight",
    "render_drop_in",
    "resolve_extraction_window",
    "showcase_ceiling",
]
