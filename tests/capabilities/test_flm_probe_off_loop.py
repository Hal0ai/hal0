"""#1974 review: no caller ever waits on the FLM-image probe.

``_flm_image_present()`` asks the ``hal0-podman-ro`` seam, a blocking call
that can take up to its 10 s timeout when podman is wedged. It is reached
from many request paths, directly and indirectly: ``/api/capabilities``,
``/api/backends``, ``/api/models`` (``model_to_dict`` → ``runs_on_for_model``
→ ``_backend_variants`` → ``available_backends``) and capability POST
validation (``models_for_capability``). Wrapping handlers one by one missed
some, and parking executor workers on a lock exhausts the pool under a burst.

So the probe runs on ONE background thread per miss and callers answer from
what is already known. These tests pin that contract with a provider whose
``image_present`` blocks like a hung seam:

  * cold-cache calls through ``/api/models`` and capability validation return
    well before the probe finishes, and the event loop keeps running while
    the probe is in flight;
  * a burst of concurrent callers costs exactly one probe;
  * the probe's answer serves the next call, and definitive answers are
    cached;
  * a reset during an in-flight probe drops that probe's answer and never
    waits for it; a previously known answer is kept while the re-probe runs.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest

from hal0.api.routes import backends as backends_routes
from hal0.capabilities import catalog
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.errors import Hal0Error
from hal0.providers import container as container_mod
from hal0.registry.model import Model
from hal0.services import models_service

#: How long the stub probe blocks, and the bound a non-blocking call must beat.
_PROBE_BLOCK_S = 0.6
_FAST_S = _PROBE_BLOCK_S / 2
_HEARTBEAT_S = 0.01


class _SlowProvider:
    """``ContainerProvider`` stand-in whose ``image_present`` blocks like a
    hung seam, then answers ``answer``."""

    def __init__(self, answer: bool | None = True) -> None:
        self.answer = answer
        self.probe_threads: list[int] = []

    def image_present(self, image: str) -> bool | None:
        self.probe_threads.append(threading.get_ident())
        time.sleep(_PROBE_BLOCK_S)
        return self.answer


def _npu_only_hw() -> Any:
    return types.SimpleNamespace(npu=types.SimpleNamespace(present=True), gpus=[])


@pytest.fixture
def slow_probe(monkeypatch: pytest.MonkeyPatch) -> Any:
    provider = _SlowProvider()
    monkeypatch.setattr(container_mod, "container_provider", lambda: provider)
    monkeypatch.setattr(catalog, "load_hardware_info", _npu_only_hw)
    monkeypatch.setattr(catalog, "_flm_last_definitive", None)
    catalog.reset_flm_image_present_cache()
    yield provider
    if catalog._flm_probe_thread is not None:
        catalog._flm_probe_thread.join(timeout=5)
    catalog.reset_flm_image_present_cache()


def _settle() -> None:
    catalog.prime_flm_image_probe(timeout=5)


async def _probe_lands_while_loop_runs() -> int:
    """Wait for the in-flight probe off the loop; return heartbeat ticks seen."""
    ticks = 0
    done = asyncio.Event()

    async def beat() -> None:
        nonlocal ticks
        while not done.is_set():
            await asyncio.sleep(_HEARTBEAT_S)
            ticks += 1

    task = asyncio.create_task(beat())
    try:
        await asyncio.to_thread(_settle)
    finally:
        done.set()
        await task
    return ticks


def _assert_probe_ran_off_loop(provider: _SlowProvider, ticks: int, loop_thread: int) -> None:
    assert provider.probe_threads, "the FLM-image probe was never started"
    assert loop_thread not in provider.probe_threads, "probe ran on the event-loop thread"
    # ~_PROBE_BLOCK_S / _HEARTBEAT_S ticks when the loop is free; 0-1 if blocked.
    assert ticks >= 5, f"event loop stalled while the probe was in flight (ticks={ticks})"


class _NoSlots:
    """Slot manager with nothing loaded: every lookup misses."""

    async def iter_configs(self) -> list[dict[str, Any]]:
        return []

    async def status(self, slot_name: str) -> Any:
        raise LookupError(slot_name)

    async def list(self) -> list[Any]:
        return []


def _raise() -> Any:
    raise FileNotFoundError("no hardware probe in unit tests")


# ── indirect request paths never wait on the probe ──────────────────────────


async def test_models_list_cold_cache_does_not_wait_on_probe(
    slow_probe: _SlowProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/api/models`` → ``list_all`` → ``model_to_dict`` → ``runs_on_for_model``
    reaches ``available_backends()`` for an ordinary local GGUF."""
    import hal0.providers.flm as flm_mod

    monkeypatch.setattr(flm_mod, "flm_served_models", lambda: [])
    model = Model(
        id="qwen-test-q4_k_m", path="/models/qwen-test-q4_k_m.gguf", backends=["llamacpp"]
    )
    registry = types.SimpleNamespace(list=lambda: [model])
    upstreams = types.SimpleNamespace(list=lambda: [])

    started = time.monotonic()
    out = await models_service.list_all(
        registry=registry, upstreams=upstreams, cache={}, update_state=None
    )
    elapsed = time.monotonic() - started

    assert elapsed < _FAST_S, f"/api/models waited on the probe ({elapsed:.2f}s)"
    assert [m["id"] for m in out["models"]] == ["qwen-test-q4_k_m"]
    ticks = await _probe_lands_while_loop_runs()
    _assert_probe_ran_off_loop(slow_probe, ticks, threading.get_ident())


async def test_capability_validation_cold_cache_does_not_wait_on_probe(
    slow_probe: _SlowProvider, tmp_hal0_home: str, tmp_path: Path
) -> None:
    """Capability POSTs validate through ``models_for_capability``, which
    reaches ``available_backends()`` via ``_backend_variants``."""
    caps = tmp_path / "capabilities.toml"
    caps.write_text("", encoding="utf-8")
    orch = CapabilityOrchestrator(slot_manager=_NoSlots(), config_path=caps)  # type: ignore[arg-type]

    started = time.monotonic()
    with pytest.raises(Hal0Error):
        orch._validate_model_in_catalog("embed", "embed", "no-such-model", "npu")
    elapsed = time.monotonic() - started

    assert elapsed < _FAST_S, f"capability validation waited on the probe ({elapsed:.2f}s)"
    ticks = await _probe_lands_while_loop_runs()
    _assert_probe_ran_off_loop(slow_probe, ticks, threading.get_ident())


@pytest.mark.parametrize("path", ["capabilities", "backends", "backend"])
async def test_direct_handlers_cold_cache_do_not_wait_on_probe(
    slow_probe: _SlowProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """The direct callers, with no thread hop of their own: still fast, and
    the next request after the probe lands advertises NPU."""
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)
    monkeypatch.setattr(catalog, "catalogs_by_slot", lambda registry=None: {})
    monkeypatch.setattr(backends_routes, "load_hardware_info", _raise)
    caps = tmp_path / "capabilities.toml"
    caps.write_text("", encoding="utf-8")
    orch = CapabilityOrchestrator(slot_manager=_NoSlots(), config_path=caps)  # type: ignore[arg-type]

    async def ids() -> list[str]:
        if path == "capabilities":
            return [b["id"] for b in (await orch.get_state())["backends"]]
        if path == "backends":
            return [r["id"] for r in await backends_routes.list_backends(None, _NoSlots())]  # type: ignore[arg-type]
        try:
            row = await backends_routes.get_backend_details("npu", None, _NoSlots())  # type: ignore[arg-type]
        except Hal0Error:
            return []
        return [row["id"]]

    started = time.monotonic()
    first = await ids()
    elapsed = time.monotonic() - started

    assert elapsed < _FAST_S, f"{path} waited on the probe ({elapsed:.2f}s)"
    assert "npu" not in first, "cold cache: NPU is under-reported until the probe lands"
    ticks = await _probe_lands_while_loop_runs()
    _assert_probe_ran_off_loop(slow_probe, ticks, threading.get_ident())
    assert "npu" in await ids(), "the landed probe must serve the next request"


# ── one probe per miss, answers cached ──────────────────────────────────────


def test_concurrent_cold_cache_callers_start_one_probe(slow_probe: _SlowProvider) -> None:
    n = 16
    barrier = threading.Barrier(n)
    results: list[bool] = []
    elapsed: list[float] = []

    def caller() -> None:
        barrier.wait()
        started = time.monotonic()
        results.append(catalog._flm_image_present())
        elapsed.append(time.monotonic() - started)

    threads = [threading.Thread(target=caller) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    _settle()

    assert results == [False] * n, "cold cache answers 'not present' without waiting"
    assert max(elapsed) < _FAST_S, "a caller waited on the probe"
    assert len(slow_probe.probe_threads) == 1


async def test_concurrent_handler_burst_starts_one_probe(
    slow_probe: _SlowProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends_routes, "load_hardware_info", _raise)

    await asyncio.gather(
        *(backends_routes.list_backends(None, _NoSlots()) for _ in range(8))  # type: ignore[arg-type]
    )
    _settle()

    assert len(slow_probe.probe_threads) == 1


def test_background_answer_serves_the_next_call_and_is_cached(slow_probe: _SlowProvider) -> None:
    assert catalog._flm_image_present() is False  # cold: probe started, not awaited
    _settle()
    assert catalog._flm_image_present() is True
    assert catalog._flm_image_present() is True
    assert len(slow_probe.probe_threads) == 1, "a definitive answer must be cached"


def test_definitive_absent_is_cached_too(slow_probe: _SlowProvider) -> None:
    slow_probe.answer = False
    catalog._flm_image_present()
    _settle()
    assert catalog._flm_image_present() is False
    _settle()
    assert len(slow_probe.probe_threads) == 1


def test_unanswerable_probe_is_retried_after_the_window(
    slow_probe: _SlowProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_probe.answer = None
    monkeypatch.setattr(catalog, "_FLM_PROBE_RETRY_S", 0.0)
    catalog._flm_image_present()
    _settle()
    slow_probe.answer = True
    assert catalog._flm_image_present() is False  # window over: re-probe started
    _settle()
    assert catalog._flm_image_present() is True
    assert len(slow_probe.probe_threads) == 2


# ── reset ───────────────────────────────────────────────────────────────────


def test_reset_during_a_probe_drops_its_answer_and_never_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe that started before an FLM pull's reset must not cache its
    (possibly pre-pull) answer, and the reset must not wait for the probe."""
    started, release = threading.Event(), threading.Event()
    calls: list[str] = []

    class _GatedProvider:
        def image_present(self, image: str) -> bool | None:
            calls.append(image)
            if len(calls) == 1:
                started.set()
                release.wait(timeout=5)
                return False
            return True

    monkeypatch.setattr(container_mod, "container_provider", lambda: _GatedProvider())
    monkeypatch.setattr(catalog, "load_hardware_info", _npu_only_hw)
    monkeypatch.setattr(catalog, "_flm_last_definitive", None)
    catalog.reset_flm_image_present_cache()
    try:
        assert catalog._flm_image_present() is False
        stale = catalog._flm_probe_thread
        assert stale is not None and started.wait(timeout=5)

        reset_began = time.monotonic()
        catalog.reset_flm_image_present_cache()
        assert time.monotonic() - reset_began < 0.1, "reset waited on the in-flight probe"
        release.set()
        stale.join(timeout=5)

        assert catalog._flm_image_present_cache is None, "stale pre-reset answer was cached"
        assert catalog._flm_image_present() is False  # starts a fresh probe
        _settle()
        assert catalog._flm_image_present() is True
        assert len(calls) == 2
    finally:
        release.set()
        _settle()
        catalog.reset_flm_image_present_cache()


def test_known_answer_is_kept_while_a_reset_re_probes(slow_probe: _SlowProvider) -> None:
    """After an FLM pull's reset, NPU must not flicker off during the re-probe."""
    catalog._flm_image_present()
    _settle()
    assert catalog._flm_image_present() is True

    catalog.reset_flm_image_present_cache()
    started = time.monotonic()
    assert catalog._flm_image_present() is True  # last definitive, while re-probing
    assert time.monotonic() - started < _FAST_S
    _settle()
    assert len(slow_probe.probe_threads) == 2


# ── boot-time start (#1974 review: confine the cold window to boot) ─────────


def _boot_ctx() -> tuple[Any, Any]:
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    ctx = types.SimpleNamespace(
        slot_manager=_NoSlots(), model_registry=None, capability_orchestrator=None
    )
    return app, ctx


def test_start_probe_is_non_blocking_on_an_npu_host(slow_probe: _SlowProvider) -> None:
    started = time.monotonic()
    catalog.start_flm_image_probe()
    assert time.monotonic() - started < _FAST_S, "start_flm_image_probe waited on the seam"
    _settle()
    assert len(slow_probe.probe_threads) == 1
    assert catalog._flm_image_present() is True, "the boot probe's answer serves request one"


def test_start_and_prime_are_no_ops_without_an_npu(
    slow_probe: _SlowProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        catalog,
        "load_hardware_info",
        lambda: types.SimpleNamespace(npu=types.SimpleNamespace(present=False), gpus=[]),
    )
    catalog.start_flm_image_probe()
    catalog.prime_flm_image_probe(timeout=5)
    assert slow_probe.probe_threads == []


async def test_boot_capabilities_phase_starts_the_probe_without_blocking(
    slow_probe: _SlowProvider, tmp_hal0_home: str
) -> None:
    from hal0.api import _boot_capabilities

    app, ctx = _boot_ctx()
    started = time.monotonic()
    await _boot_capabilities(app, ctx)
    elapsed = time.monotonic() - started

    assert elapsed < _FAST_S, f"boot waited on the FLM-image probe ({elapsed:.2f}s)"
    assert catalog._flm_probe_thread is not None, "boot did not start the probe"
    ticks = await _probe_lands_while_loop_runs()
    _assert_probe_ran_off_loop(slow_probe, ticks, threading.get_ident())
    assert catalog._flm_image_present() is True


async def test_boot_survives_a_failing_probe_start(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.api import _boot_capabilities

    def _boom() -> None:
        raise RuntimeError("probe start failed")

    monkeypatch.setattr(catalog, "start_flm_image_probe", _boom)
    app, ctx = _boot_ctx()

    await _boot_capabilities(app, ctx)

    assert app.state.capability_orchestrator is not None


# ── one response is internally consistent ───────────────────────────────────

_EMBED_FLM_TAG = {
    "tag": "embed-gemma:300m",
    "capabilities": ["embed"],
    "installed": True,
    "size_bytes": 300_000_000,
    "footprint_gb": 0.6,
    "family": "embed-gemma",
}


async def test_probe_landing_mid_get_state_yields_a_consistent_response(
    slow_probe: _SlowProvider,
    tmp_hal0_home: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_state`` reads ``available_backends()`` and then builds catalogs that
    read it again. Land the probe exactly between the two reads: the catalogs
    must equal what a settled-absent and a settled-present probe produce,
    because catalog rows never depend on NPU presence (NPU rows come from
    ``flm list``; ``host_backends`` gates only the gpu-*/cpu lanes)."""
    import hal0.agents.hermes_refresh as _hr
    import hal0.providers.flm as flm_mod

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)
    monkeypatch.setattr(flm_mod, "flm_served_models", lambda: [dict(_EMBED_FLM_TAG)])
    real_catalogs = catalog.catalogs_by_slot

    def _settled(answer: bool) -> Any:
        slow_probe.answer = answer
        catalog.reset_flm_image_present_cache()
        catalog._flm_image_present()
        _settle()
        assert catalog._flm_image_present() is answer
        return real_catalogs(registry=None)

    when_absent = _settled(False)
    when_present = _settled(True)
    assert any(
        b["id"] == "npu" for row in when_present["embed"]["embed"] for b in row["backends"]
    ), "fixture: the catalog should carry an NPU row"
    assert when_absent == when_present

    slow_probe.answer = True
    monkeypatch.setattr(catalog, "_flm_last_definitive", None)
    catalog.reset_flm_image_present_cache()
    landed_between: list[bool] = []

    def _catalogs_after_probe_lands(registry: Any = None) -> Any:
        _settle()  # the probe started by get_state's first read lands right here
        landed_between.append(catalog._flm_image_present())
        return real_catalogs(registry=registry)

    monkeypatch.setattr(catalog, "catalogs_by_slot", _catalogs_after_probe_lands)
    caps = tmp_path / "capabilities.toml"
    caps.write_text("", encoding="utf-8")
    orch = CapabilityOrchestrator(slot_manager=_NoSlots(), config_path=caps)  # type: ignore[arg-type]

    state = await orch.get_state()

    assert landed_between == [True], "the probe did not land between the two reads"
    assert "npu" not in [b["id"] for b in state["backends"]]
    assert state["catalogs"] == when_present == when_absent


# ── FLM pull path keeps the known answer across its reset ───────────────────


async def test_flm_pull_reset_keeps_the_known_answer_while_re_probing(
    slow_probe: _SlowProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_flm_pull`` → ``reset_flm_image_present_cache()`` (registry/pull.py)
    must not flicker NPU off while the re-probe runs."""
    import sys

    import hal0.providers.flm as flm_mod
    from hal0.registry import pull as pull_mod
    from hal0.registry.pull import PullJob, run_flm_pull

    catalog._flm_image_present()
    _settle()
    assert catalog._flm_image_present() is True

    host_models_dir = str(tmp_path)
    fake_pull = "import os, sys; os.makedirs(os.path.join(sys.argv[1], 'Repo'), exist_ok=True)"
    monkeypatch.setattr(
        flm_mod,
        "flm_pull_command",
        lambda tag: ([sys.executable, "-c", fake_pull, host_models_dir], host_models_dir),
    )
    monkeypatch.setattr(flm_mod, "ensure_host_flm_store_link", lambda: host_models_dir)
    monkeypatch.setattr(flm_mod, "flm_host_async_spawn", lambda argv: (argv, {}))
    monkeypatch.setattr(flm_mod, "flm_served_models", lambda: [])
    monkeypatch.setattr(flm_mod, "reset_flm_catalog_cache", lambda: None)
    monkeypatch.setattr(pull_mod, "_register_flm_pulled", lambda *a, **k: None)
    monkeypatch.setattr(pull_mod, "_flm_install_path", lambda hmd, tag: str(tmp_path / "Repo"))
    generation_before = catalog._flm_probe_generation

    job = PullJob(job_id="j1", model_id="fake:tag")
    await run_flm_pull(job, tag="fake:tag", registry=object())

    assert job.state == "completed"
    assert catalog._flm_probe_generation == generation_before + 1, "the pull did not reset"
    started = time.monotonic()
    assert catalog._flm_image_present() is True, "NPU flickered off after the FLM pull"
    assert time.monotonic() - started < _FAST_S
    _settle()
    assert len(slow_probe.probe_threads) == 2, "the pull's reset should trigger a re-probe"


# ── backends_settled: the dashboard re-polls until NPU presence is final ────


def test_settled_is_false_cold_and_true_once_the_probe_lands(slow_probe: _SlowProvider) -> None:
    assert catalog.flm_image_probe_settled() is False  # cold, nothing probed yet
    catalog._flm_image_present()
    assert catalog.flm_image_probe_settled() is False  # probe in flight
    _settle()
    assert catalog.flm_image_probe_settled() is True
    catalog.reset_flm_image_present_cache()
    assert catalog.flm_image_probe_settled() is False, "a reset (FLM pull) unsettles it"


def test_settled_stays_false_while_only_an_unanswerable_result_is_held(
    slow_probe: _SlowProvider,
) -> None:
    slow_probe.answer = None
    catalog._flm_image_present()
    _settle()
    assert catalog.flm_image_probe_settled() is False


def test_settled_is_true_without_an_npu(
    slow_probe: _SlowProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        catalog,
        "load_hardware_info",
        lambda: types.SimpleNamespace(npu=types.SimpleNamespace(present=False), gpus=[]),
    )
    assert catalog.flm_image_probe_settled() is True
    assert slow_probe.probe_threads == []


def _state_orch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CapabilityOrchestrator:
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)
    monkeypatch.setattr(catalog, "catalogs_by_slot", lambda registry=None: {})
    caps = tmp_path / "capabilities.toml"
    caps.write_text("", encoding="utf-8")
    return CapabilityOrchestrator(slot_manager=_NoSlots(), config_path=caps)  # type: ignore[arg-type]


async def test_get_state_reports_backends_settled(
    slow_probe: _SlowProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _state_orch(tmp_path, monkeypatch)

    cold = await orch.get_state()
    assert cold["backends_settled"] is False
    assert "npu" not in [b["id"] for b in cold["backends"]]

    _settle()
    warm = await orch.get_state()
    assert warm["backends_settled"] is True
    assert "npu" in [b["id"] for b in warm["backends"]]


async def test_probe_landing_mid_get_state_never_reports_a_stale_list_as_settled(
    slow_probe: _SlowProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag is read before ``backends``: if the probe lands in between, the
    response may say unsettled with a fresh list (one extra poll), but it can
    never say settled while carrying the pre-probe list (polling would stop)."""
    orch = _state_orch(tmp_path, monkeypatch)
    catalog._flm_image_present()  # probe in flight
    real_available = catalog.available_backends

    def _available_after_probe_lands() -> list[dict[str, Any]]:
        _settle()
        return real_available()

    monkeypatch.setattr(catalog, "available_backends", _available_after_probe_lands)

    state = await orch.get_state()

    npu_listed = "npu" in [b["id"] for b in state["backends"]]
    assert not (state["backends_settled"] and not npu_listed), "stale list reported as settled"
    assert state["backends_settled"] is False
    assert npu_listed
