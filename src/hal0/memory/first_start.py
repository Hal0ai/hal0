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
   the engine is started here and watched until it answers ``/health``.
3. If systemd reports the start failing (the unit is ``failed``, or it has been
   restarted :data:`FAILED_START_RESTARTS` times) the engine cannot come up on
   the configured embedder. It is stopped, the record is rewritten as the
   engine default with the source :data:`SOURCE_FIRST_START_FALLBACK`, the
   drop-in is rendered again from that record, and the engine is started on
   it. Nothing was ever embedded: an engine that never answered ``/health``
   never took a write.
4. An engine that is still on its first attempt when the wait runs out is left
   alone. It is usually still downloading its models, and the configured
   embedder is what it will come up on.

The fallback can only be reached in the process that saw the store absent and
started the engine itself, so it cannot swap the embedder under a store that
holds vectors. One invariant holds on every path: the record never names an
embedder the unit files would not start the engine on.

Never raises: an install must not die here.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
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

#: How long the first start is watched. The same 120 s ``install.sh`` gives
#: the engine afterwards (40 probes, 3 s apart).
FIRST_START_WAIT_S = 120.0
_POLL_STEP_S = 3.0
_SYSTEMCTL_TIMEOUT_S = 60.0

#: Restarts that mean the start is failing, not merely slow. The unit is
#: ``Restart=on-failure`` with ``RestartSec=3``, and the engine exits when a
#: model cannot be loaded, so a start that cannot work gets here in seconds.
#: Two, not one, so a single unrelated crash does not cost the box its
#: configured embedder.
FAILED_START_RESTARTS = 2

#: ``status`` values of :func:`run_first_start`.
SKIPPED = "skipped"
FAILED = "failed"
PREPARED = "prepared"
STARTED = "started"
STARTING = "starting"
FELL_BACK = "fell-back"
FALLBACK_FAILED = "fallback-failed"

#: Process exit codes for ``install.sh``.
EXIT_OK = 0
EXIT_FELL_BACK = 3
EXIT_FAILED = 4

UnitState = dict[str, Any]


def engine_unit_state() -> UnitState:
    """``{"active", "restarts"}`` of the engine's unit, from ``systemctl show``.

    ``active`` is systemd's ``ActiveState`` (``None`` when it cannot be read)
    and ``restarts`` its ``NRestarts`` (``0`` when it cannot be read), so an
    unreadable unit never looks like a failed start.
    """
    state: UnitState = {"active": None, "restarts": 0}
    try:
        proc = subprocess.run(
            ["systemctl", "show", SERVICE, "-p", "ActiveState", "-p", "NRestarts"],
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
    return state


def engine_healthy() -> bool:
    """True when the engine answers ``/health``."""
    return _http_get_json(f"{ENGINE_BASE_URL}/health", timeout=3.0) is not None


def _start_is_failing(state: UnitState) -> bool:
    return state.get("active") == "failed" or int(state.get("restarts") or 0) >= (
        FAILED_START_RESTARTS
    )


def _named(record: EmbedderRecord) -> dict[str, Any]:
    return {"provider": record.provider, "model": record.model, "source": record.source}


def _fall_back(
    mem: Any,
    record: EmbedderRecord,
    *,
    seam: SystemCtlSeam,
    evidence: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Move a box whose first start failed onto the engine default.

    Stop the engine, rewrite the record, render the drop-in from it, start
    again. If the drop-in cannot be written the old record goes back, so the
    record and the unit files still name the same embedder.
    """
    configured = {"provider": record.provider, "model": record.model}
    try:
        seam.systemctl("systemctl", "stop", SERVICE, check=False, timeout=_SYSTEMCTL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError, SeamTimeout) as exc:
        log.warning("hal0.memory.first_start_stop_failed", error=str(exc))
    # The render follows a running engine, so the default can only be
    # rendered once the engine is really down.
    if evidence().get("live") is not None:
        return {
            "status": FALLBACK_FAILED,
            "reason": "the engine did not stop",
            **_named(record),
        }
    fallback = replace(
        record,
        provider=ENGINE_DEFAULT.provider,
        model=ENGINE_DEFAULT.model,
        source=SOURCE_FIRST_START_FALLBACK,
        mixed=False,
        conflict=None,
    )
    try:
        embedder_record.save_embedder_record(fallback)
    except OSError as exc:
        extraction_env.reload_and_restart_hindsight(seam=seam)
        return {
            "status": FALLBACK_FAILED,
            "reason": f"the embedder record could not be rewritten ({type(exc).__name__})",
            **_named(record),
        }
    applied = extraction_env.apply_showcase_dropin(
        models=mem.models,
        decision=mem.decision,
        lanes=mem.lanes,
        graph=mem.graph,
        restart=True,
        seam=seam,
    )
    if not applied.get("written"):
        try:
            embedder_record.save_embedder_record(record)
        except OSError as exc:
            log.warning("hal0.memory.first_start_record_restore_failed", error=str(exc))
        extraction_env.reload_and_restart_hindsight(seam=seam)
        return {
            "status": FALLBACK_FAILED,
            "reason": applied.get("error") or "the drop-in could not be rewritten",
            **_named(record),
        }
    log.warning(
        "hal0.memory.first_start_fell_back",
        configured=configured,
        provider=fallback.provider,
        model=fallback.model,
    )
    return {
        "status": FELL_BACK,
        "configured": configured,
        "restarted": bool(applied.get("restarted")),
        "error": applied.get("error"),
        **_named(fallback),
    }


def run_first_start(
    *,
    seam: SystemCtlSeam | None = None,
    healthy: Callable[[], bool] | None = None,
    unit_state: Callable[[], UnitState] | None = None,
    evidence: Callable[[], dict[str, Any]] | None = None,
    wait_s: float = FIRST_START_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Put a box with no memory store on the configured embedder (module docstring).

    Returns ``{"status": ...}`` plus, where an embedder was settled,
    ``provider``, ``model`` and ``source``. ``healthy``, ``unit_state``,
    ``evidence``, ``sleep`` and ``monotonic`` are test seams. Never raises.
    """
    seam = seam if seam is not None else SystemCtlSeam()
    healthy = healthy if healthy is not None else engine_healthy
    unit_state = unit_state if unit_state is not None else engine_unit_state
    evidence = evidence if evidence is not None else embedder_record.engine_evidence
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

    applied = extraction_env.apply_showcase_dropin(
        models=mem.models,
        decision=mem.decision,
        lanes=mem.lanes,
        graph=mem.graph,
        restart=False,
        seam=seam,
    )
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

    started = extraction_env.reload_and_restart_hindsight(seam=seam)
    if started.get("error"):
        # No evidence about the embedder either way: leave it to the installer.
        return {"status": STARTING, "reason": started["error"], **_named(record)}
    deadline = monotonic() + wait_s
    while True:
        if healthy():
            return {"status": STARTED, **_named(record)}
        if _start_is_failing(unit_state()):
            if healthy():
                return {"status": STARTED, **_named(record)}
            return _fall_back(mem, record, seam=seam, evidence=evidence)
        if monotonic() >= deadline:
            return {"status": STARTING, **_named(record)}
        sleep(_POLL_STEP_S)


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
    if status == FAILED:
        return (
            "memory embedder: could not write the engine's drop-in before its first start "
            f"({result.get('reason')}); a new store will be built with the engine default"
        )
    if status == PREPARED:
        return f"memory embedder: {name} ({result.get('source')})"
    if status == STARTED:
        return f"memory embedder: the engine is up on {name}, the configured embedder"
    if status == STARTING:
        return (
            f"memory embedder: the engine is starting on {name}, the configured embedder, "
            "and is not healthy yet"
        )
    configured = result.get("configured") or result
    wanted = describe_embedder(configured.get("provider"), configured.get("model"))
    if status == FELL_BACK:
        return (
            f"memory embedder: the engine could not start on {wanted}; it now starts on "
            f"{name}, and that is what is recorded for this store"
        )
    return (
        f"memory embedder: the engine could not start on {wanted}, and moving it to the "
        f"engine default failed ({result.get('reason')})"
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
    if status in (FAILED, FALLBACK_FAILED):
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via install.sh
    sys.exit(main())
