"""A new install's first memory-engine start, on the configured embedder (#3518).

ADR-0058 decision 3: a new install builds its store with the configured
embedder (``[memory.models]``, ``hindsight-ai/hs-embed-1`` unless changed); an
upgraded box stays on whatever built its store. The second half is
:mod:`hal0.memory.embedder_record`. This module is the first half, and
``install.sh`` is its one caller::

    <venv>/bin/python -I -m hal0.memory.first_start

run after the engine's unit file is installed and before the installer's own
``systemctl enable --now hindsight-api``. Before it existed the installer
started a new engine on the base unit, which names no embedder, so every new
store was built with the engine default whatever the config said.

What it does
------------

It acts only on a box with no memory store
(:func:`hal0.memory.embedder_record.store_state` says ``absent``) whose engine
is not running. On any other box (a store exists, its location is unavailable,
the engine is up, the record file is unreadable) it returns ``skipped`` and
touches nothing: the embedder of an existing store is
:mod:`~hal0.memory.embedder_record`'s business alone.

On a box with no store:

1. :func:`hal0.memory.extraction_env.apply_showcase_dropin` writes the managed
   drop-in and, once it is on disk, the record. This module adds no writer and
   no renderer. With no record that is the configured pair; with a record left
   by an earlier run it is that record's pair, so a re-run converges.
2. When the record is the configured pair and that is not the engine default,
   the engine is started here and watched (:func:`_watch`) until the first
   of: it answers ``/health``; its main process exits (systemd shows a
   restart, another ``MainPID``, or a unit that is no longer ``active``); or
   :data:`FIRST_START_WAIT_S` passes.
3. An engine whose first process is still running when the wait runs out is
   left alone. It is still downloading its models, and the configured embedder
   is what it will come up on.
4. A first exit is a failing start, but not yet a reason to give the
   configured embedder up: a reranker that cannot be downloaded, a taken port,
   a database that will not initialise and a box with no network all fail the
   same way. So the cause is proved first (:func:`_prove_and_fall_back`).

   If the model host cannot be reached at all (:func:`model_host_reachable`)
   nothing is tried: the engine default could not be downloaded either. The
   drop-in and the record stay on the configured pair and the engine is left
   retrying (``models-unreachable``).

   Otherwise the engine is stopped, the drop-in is rendered with the
   engine-default embedder and nothing else changed, the record is left as it
   is, and the engine is started and watched again.

   * It becomes healthy: the embedder was the cause. Only now is the record
     rewritten as the engine default, with the source
     :data:`~hal0.memory.embedder_record.SOURCE_FIRST_START_FALLBACK`.
   * It does not: the embedder was not the cause. The engine is stopped, the
     drop-in is rendered from the record again (the configured pair), and the
     engine is started on it and left retrying. When whatever was wrong is
     fixed it comes up on the configured embedder and the record is already
     right.

The trial on the engine default can only be reached in the process that saw
the store absent and started the engine itself, and an engine that never
answered ``/health`` never took a write, so no embedder is swapped under
stored vectors.

One invariant holds at every return: the record names the embedder the
drop-in on disk names. Between the trial start and its verdict the two differ
on purpose; if the drop-in cannot be put back afterwards the record is moved
to the drop-in instead.

Measured on a test container (2026-11-11, hindsight-api 1.2.0): a first start
with the network up is healthy after about 25 s; a model id that does not
exist makes the first process exit after about 21 s, and the trial on the
engine default is healthy about 14 s later; with the model host unreachable
the engine logs its startup failure at about 79 s but the process only exits
at about 127 s, because the download retries of the other model keep it
alive.

Never raises: an install must not die here.
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import structlog

from hal0.memory import embedder_record, extraction_env
from hal0.memory.embedder_record import (
    ENGINE_DEFAULT,
    SOURCE_CONFIGURED,
    SOURCE_FIRST_START_FALLBACK,
    STORE_ABSENT,
    EmbedderRecord,
    EmbedderRecordError,
    configured_embedder,
    describe_embedder,
)
from hal0.memory.engine_upgrade import ENGINE_BASE_URL, _http_get_json
from hal0.memory.extraction_env import SERVICE
from hal0.system.seam import SeamTimeout, SystemCtlSeam

log = structlog.get_logger(__name__)

#: The longest one start is watched. A start that cannot work ends with the
#: engine's process exiting, and the slowest such exit measured is about 127 s
#: (model host unreachable: huggingface_hub retries each file five times with
#: 1/2/4/8/8 s backoff before the engine gives up). 240 s is comfortably past
#: that, so only a start that is really still working reaches the cap.
FIRST_START_WAIT_S = 240.0
_POLL_STEP_S = 3.0
_SYSTEMCTL_TIMEOUT_S = 60.0

#: Restarts that mean the start failed. One: the unit is
#: ``Restart=on-failure``, so the first exit of the engine's process is
#: already a restart, and a healthy first start never has one.
FAILED_START_RESTARTS = 1

#: ``ActiveState`` values a unit that was just started only shows once its
#: main process has gone (``activating`` is the wait before an auto-restart).
_EXITED_STATES = frozenset({"failed", "activating", "deactivating", "inactive"})

#: Where the engine downloads its local models from unless its unit sets
#: ``HF_ENDPOINT``.
MODEL_HOST = "https://huggingface.co"
_MODEL_HOST_TIMEOUT_S = 5.0

#: ``status`` values of :func:`run_first_start`.
SKIPPED = "skipped"
FAILED = "failed"
PREPARED = "prepared"
STARTED = "started"
STARTING = "starting"
FELL_BACK = "fell-back"
NOT_STARTED = "not-started"
MODELS_UNREACHABLE = "models-unreachable"

#: Process exit codes for ``install.sh``.
EXIT_OK = 0
EXIT_FELL_BACK = 3
EXIT_FAILED = 4
EXIT_NOT_STARTED = 5
EXIT_MODELS_UNREACHABLE = 6

#: :func:`_watch` verdicts.
_HEALTHY = "healthy"
_FAILING = "failing"
_SLOW = "slow"

UnitState = dict[str, Any]


@dataclass(frozen=True)
class _Probes:
    """How a start is watched; every field is a test seam."""

    seam: SystemCtlSeam
    healthy: Callable[[], bool]
    unit_state: Callable[[], UnitState]
    evidence: Callable[[], dict[str, Any]]
    host_reachable: Callable[[], bool]
    wait_s: float
    sleep: Callable[[float], None]
    monotonic: Callable[[], float]


def engine_unit_state() -> UnitState:
    """``{"active", "restarts", "main_pid"}`` of the engine's unit, from ``systemctl show``.

    ``active`` is systemd's ``ActiveState`` and ``main_pid`` its ``MainPID``
    (``0`` while no process runs); both are ``None`` when they cannot be
    read. ``restarts`` is ``NRestarts``, ``0`` when it cannot be read. An
    unreadable unit therefore never looks like a failed start.
    """
    state: UnitState = {"active": None, "restarts": 0, "main_pid": None}
    try:
        proc = subprocess.run(
            [
                "systemctl",
                "show",
                SERVICE,
                "-p",
                "ActiveState",
                "-p",
                "NRestarts",
                "-p",
                "MainPID",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return state
    if proc.returncode != 0:
        return state
    for line in (proc.stdout or "").splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key == "ActiveState":
            state["active"] = value.strip() or None
        elif key == "NRestarts":
            try:
                state["restarts"] = int(value.strip() or "0")
            except ValueError:
                state["restarts"] = 0
        elif key == "MainPID":
            try:
                state["main_pid"] = int(value.strip())
            except ValueError:
                state["main_pid"] = None
    return state


def engine_healthy() -> bool:
    """True when the engine answers ``/health``."""
    return _http_get_json(f"{ENGINE_BASE_URL}/health", timeout=3.0) is not None


def model_host_reachable() -> bool:
    """True when the host the engine downloads its models from answers at all.

    One HTTPS request with a 5 s timeout to ``HF_ENDPOINT`` when the engine's
    unit sets it, else :data:`MODEL_HOST`. Any HTTP answer, an error status
    included, means the host is reachable; a name that does not resolve, a
    refused or timed-out connection means it is not. It is asked from this
    process, so a proxy set only in the unit's environment is not used.
    """
    endpoint = str(extraction_env.hindsight_unit_env().get("HF_ENDPOINT") or "").rstrip("/")
    if not endpoint.startswith(("https://", "http://")):
        endpoint = MODEL_HOST
    request = urllib.request.Request(
        endpoint, method="HEAD", headers={"User-Agent": "hal0-installer"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_MODEL_HOST_TIMEOUT_S):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _process_exited(state: UnitState, first_pid: int | None) -> bool:
    """True when the process this start launched has gone (see :data:`_EXITED_STATES`)."""
    if state.get("active") in _EXITED_STATES:
        return True
    if int(state.get("restarts") or 0) >= FAILED_START_RESTARTS:
        return True
    pid = state.get("main_pid")
    return bool(first_pid) and pid is not None and pid != first_pid


def _watch(probes: _Probes) -> str:
    """Watch one start: healthy, failing (its process exited) or still slow at the cap."""
    first_pid = probes.unit_state().get("main_pid") or None
    deadline = probes.monotonic() + probes.wait_s
    while True:
        if probes.healthy():
            return _HEALTHY
        if _process_exited(probes.unit_state(), first_pid):
            # One more look: it may have come up between the two probes.
            return _HEALTHY if probes.healthy() else _FAILING
        if probes.monotonic() >= deadline:
            return _SLOW
        probes.sleep(_POLL_STEP_S)


def _systemctl(seam: SystemCtlSeam, verb: str) -> None:
    try:
        seam.systemctl("systemctl", verb, SERVICE, check=False, timeout=_SYSTEMCTL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError, SeamTimeout) as exc:
        log.warning("hal0.memory.first_start_systemctl_failed", verb=verb, error=str(exc))


def _start(seam: SystemCtlSeam) -> dict[str, Any]:
    """Start the engine on what the unit files say now.

    ``reset-failed`` first: it clears a start limit an earlier failing start
    reached and zeroes ``NRestarts``, so the next watch counts this start
    only. Then ``daemon-reload`` and ``restart``.
    """
    _systemctl(seam, "reset-failed")
    return extraction_env.reload_and_restart_hindsight(seam=seam)


def _stopped(probes: _Probes) -> bool:
    """Stop the engine; True once no engine process is left."""
    _systemctl(probes.seam, "stop")
    return probes.evidence().get("live") is None


def _write_dropin(mem: Any, seam: SystemCtlSeam, **kwargs: Any) -> dict[str, Any]:
    return extraction_env.apply_showcase_dropin(
        models=mem.models,
        decision=mem.decision,
        lanes=mem.lanes,
        graph=mem.graph,
        restart=False,
        seam=seam,
        **kwargs,
    )


def _named(record: EmbedderRecord) -> dict[str, Any]:
    return {"provider": record.provider, "model": record.model, "source": record.source}


def _as_fallback(record: EmbedderRecord) -> EmbedderRecord:
    return replace(
        record,
        provider=ENGINE_DEFAULT.provider,
        model=ENGINE_DEFAULT.model,
        source=SOURCE_FIRST_START_FALLBACK,
        mixed=False,
        conflict=None,
    )


def _record_the_default(record: EmbedderRecord) -> EmbedderRecord | None:
    """Rewrite the record as the engine default; ``None`` when it cannot be written."""
    try:
        return embedder_record.save_embedder_record(_as_fallback(record))
    except OSError as exc:
        log.warning("hal0.memory.first_start_record_write_failed", error=str(exc))
        return None


def _prove_and_fall_back(mem: Any, record: EmbedderRecord, probes: _Probes) -> dict[str, Any]:
    """The first start is failing: find out whether the embedder is why.

    Try the engine on the engine-default embedder with the record untouched.
    Healthy: record the default (``fell-back``). Anything else: put the
    configured drop-in back and leave the engine retrying on it
    (``not-started``). See the module docstring.
    """
    seam = probes.seam
    configured = {"provider": record.provider, "model": record.model}

    def not_started(reason: str) -> dict[str, Any]:
        return {"status": NOT_STARTED, "reason": reason, **_named(record)}

    if not probes.host_reachable():
        # Nothing can be downloaded, the engine default included: a trial
        # would prove nothing. Leave the engine retrying on the configured pair.
        return {"status": MODELS_UNREACHABLE, **_named(record)}
    if not _stopped(probes):
        # Still up on the configured drop-in, which the record names.
        return not_started("the engine did not stop")
    trial = _write_dropin(mem, seam, embedder=ENGINE_DEFAULT)
    if not trial.get("written"):
        # The configured drop-in is still on disk: start on it again.
        _start(seam)
        return not_started(trial.get("error") or "the drop-in could not be rewritten")

    _start(seam)
    proved = _watch(probes) == _HEALTHY
    if proved:
        saved = _record_the_default(record)
        if saved is not None:
            log.warning(
                "hal0.memory.first_start_fell_back",
                configured=configured,
                provider=saved.provider,
                model=saved.model,
            )
            return {"status": FELL_BACK, "configured": configured, **_named(saved)}

    # Not the embedder (or its record could not be written): back to the
    # configured drop-in. The render follows a running engine, so the engine
    # has to be down before the record's pair can be rendered.
    reason = (
        "the embedder record could not be rewritten"
        if proved
        else "it does not start on the engine default either"
    )
    if _stopped(probes):
        restored = _write_dropin(mem, seam)
        if restored.get("written"):
            _start(seam)
            return not_started(reason)
        reason = restored.get("error") or "the configured drop-in could not be put back"
    else:
        reason = "the engine did not stop"
    # The drop-in on disk still names the engine default. The record follows
    # the drop-in, never the other way round.
    saved = _record_the_default(record)
    _start(seam)
    if saved is None:
        return {"status": FAILED, "reason": reason, **_named(record)}
    return {"status": FELL_BACK, "configured": configured, "reason": reason, **_named(saved)}


def run_first_start(
    *,
    seam: SystemCtlSeam | None = None,
    healthy: Callable[[], bool] | None = None,
    unit_state: Callable[[], UnitState] | None = None,
    evidence: Callable[[], dict[str, Any]] | None = None,
    host_reachable: Callable[[], bool] | None = None,
    wait_s: float = FIRST_START_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Put a box with no memory store on the configured embedder (module docstring).

    Returns ``{"status": ...}`` plus, where an embedder was settled,
    ``provider``, ``model`` and ``source``. ``healthy``, ``unit_state``,
    ``evidence``, ``host_reachable``, ``sleep`` and ``monotonic`` are test
    seams. Never raises.
    """
    probes = _Probes(
        seam=seam if seam is not None else SystemCtlSeam(),
        healthy=healthy if healthy is not None else engine_healthy,
        unit_state=unit_state if unit_state is not None else engine_unit_state,
        evidence=evidence if evidence is not None else embedder_record.engine_evidence,
        host_reachable=host_reachable if host_reachable is not None else model_host_reachable,
        wait_s=wait_s,
        sleep=sleep,
        monotonic=monotonic,
    )
    try:
        from hal0.config.loader import load_hal0_config

        mem = load_hal0_config().memory
    except Exception as exc:
        return {"status": SKIPPED, "reason": f"hal0.toml unreadable ({type(exc).__name__})"}

    view = embedder_record.resolve_embedder(configured_embedder(mem.models))
    if view.store != STORE_ABSENT:
        return {"status": SKIPPED, "reason": f"the memory store's state is '{view.store}'"}
    if view.live is not None:
        return {"status": SKIPPED, "reason": "the engine is already running"}
    if view.record_error is not None:
        return {"status": SKIPPED, "reason": "the embedder record cannot be read"}

    applied = _write_dropin(mem, probes.seam)
    if not applied.get("written"):
        return {"status": FAILED, "reason": applied.get("error") or "the drop-in was not written"}
    try:
        record = embedder_record.load_embedder_record()
    except EmbedderRecordError:
        record = None
    if record is None:
        # The drop-in is on disk and names an embedder; the next update reads
        # that back from the unit files and records it.
        return {"status": FAILED, "reason": "the embedder record was not written"}
    if record.source != SOURCE_CONFIGURED or record.embedder == ENGINE_DEFAULT:
        return {"status": PREPARED, **_named(record)}

    started = _start(probes.seam)
    if started.get("error"):
        # No evidence about the embedder either way: leave it to the installer.
        return {"status": STARTING, "reason": started["error"], **_named(record)}
    verdict = _watch(probes)
    if verdict == _HEALTHY:
        return {"status": STARTED, **_named(record)}
    if verdict == _SLOW:
        return {"status": STARTING, **_named(record)}
    return _prove_and_fall_back(mem, record, probes)


def describe(result: dict[str, Any]) -> str:
    """One line for the install log."""
    status = result.get("status")
    name = (
        describe_embedder(result.get("provider"), result.get("model"))
        if result.get("model")
        else ""
    )
    if status == SKIPPED:
        return f"memory embedder: left as it is ({result.get('reason')})"
    if status == PREPARED:
        return f"memory embedder: {name} ({result.get('source')})"
    if status == STARTED:
        return f"memory embedder: the engine is up on {name}, the configured embedder"
    if status == STARTING:
        return (
            f"memory embedder: the engine is starting on {name}, the configured embedder, "
            "and is not healthy yet"
        )
    if status == FELL_BACK:
        configured = result.get("configured") or {}
        wanted = describe_embedder(configured.get("provider"), configured.get("model"))
        if result.get("reason"):
            # Not a proved embedder failure: the drop-in could not be put back.
            return (
                f"memory embedder: the engine did not start on {wanted} and its drop-in "
                f"could not be put back ({result['reason']}); the drop-in and the record "
                f"both name {name} now"
            )
        return (
            f"memory embedder: the engine does not start on {wanted}, the configured "
            f"embedder; it is on {name} instead, and that is what is recorded for this store"
        )
    if status == MODELS_UNREACHABLE:
        return (
            "memory engine: it could not download its models (the model host is not "
            f"reachable); it keeps retrying and will start on {name}, the configured embedder"
        )
    if status == NOT_STARTED:
        return (
            f"memory engine: it did not start ({result.get('reason')}); its drop-in and "
            f"record stay on {name}, the configured embedder"
        )
    return (
        "memory embedder: could not set up the engine's embedder before its first start "
        f"({result.get('reason')})"
    )


def main() -> int:
    try:
        result = run_first_start()
    except Exception as exc:  # an install must not die here
        log.warning("hal0.memory.first_start_failed", error=str(exc))
        print(f"memory embedder: first-start step failed ({type(exc).__name__})")
        return EXIT_FAILED
    print(describe(result))
    status = result.get("status")
    if status == FELL_BACK:
        return EXIT_FELL_BACK
    if status == NOT_STARTED:
        return EXIT_NOT_STARTED
    if status == MODELS_UNREACHABLE:
        return EXIT_MODELS_UNREACHABLE
    if status == FAILED:
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via install.sh
    sys.exit(main())
