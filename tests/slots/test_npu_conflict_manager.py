"""The NPU per-op conflict guard's manager seam (#3666).

The pure rule lives in :mod:`hal0.slots.npu_conflict`
(tests/slots/test_npu_conflict.py); this file covers
``SlotManager._admit_npu`` as ``load``/``start``/``restart`` drive it: a
refused load leaves the slot OFFLINE with no load failure, the opt-in unloads
the holder (pinned or not) before the spawn, two opposite loads at once admit
exactly one, a refused restart touches nothing, and callers that do not pass
the flag never unload anything. Modelled on
tests/slots/test_fabric_clock_guard_manager.py.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotNpuConflict, SlotState
from tests.slots.conftest import FakeContainerProvider

pytestmark = pytest.mark.usefixtures("tmp_hal0_home", "container_stub")


def _write(home: str, name: str, lines: list[str]) -> None:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _flm(home: str, name: str = "flm", port: int = 8082) -> None:
    _write(
        home,
        name,
        [
            f'name = "{name}"',
            f"port = {port}",
            'device = "npu"',
            'type = "llm"',
            "[model]",
            'default = "gemma3-4b-FLM"',
        ],
    )


def _flm_shadow(home: str, anchor: str, suffix: str, slot_type: str, port: int) -> None:
    _write(
        home,
        f"{anchor}-{suffix}",
        [
            f'name = "{anchor}-{suffix}"',
            f"port = {port}",
            'device = "npu"',
            f'type = "{slot_type}"',
            f'served_by = "{anchor}"',
            "[model]",
            'default = "whisper-v3"',
        ],
    )


def _xdna(home: str, name: str, port: int, *, per_op: bool = True) -> None:
    lines = [
        f'name = "{name}"',
        f"port = {port}",
        'device = "npu"',
        'type = "llm"',
        'binary = "xdna"',
        "[model]",
        'default = "qwen3-1.7b"',
    ]
    if per_op:
        # GGML_XDNA_FUSED_LAYER=0 forces per-op through the launch env, so the
        # verdict needs no model file on disk.
        lines += ["[xdna.env]", 'GGML_XDNA_FUSED_LAYER = "0"']
    _write(home, name, lines)


@pytest.fixture
def events(
    monkeypatch: pytest.MonkeyPatch, container_stub: FakeContainerProvider
) -> list[tuple[str, str]]:
    """Record every unit start/stop in order; no real load preflight or eviction."""
    log: list[tuple[str, str]] = []
    real_load, real_unload = container_stub.load_sync, container_stub.unload_sync

    def load_sync(cfg: dict[str, Any], model_info: dict[str, Any]) -> None:
        log.append(("spawn", str(cfg.get("name"))))
        real_load(cfg, model_info)

    def unload_sync(cfg: dict[str, Any]) -> None:
        log.append(("stop", str(cfg.get("name"))))
        real_unload(cfg)

    monkeypatch.setattr(container_stub, "load_sync", load_sync)
    monkeypatch.setattr(container_stub, "unload_sync", unload_sync)

    async def no_check(self: SlotManager, *a: Any, **k: Any) -> None:
        return None

    @contextlib.asynccontextmanager
    async def admit(*_a: Any, **_k: Any):  # type: ignore[no-untyped-def]
        yield

    async def ready(self: SlotManager, *a: Any, **k: Any) -> SlotState:
        return SlotState.READY

    monkeypatch.setattr(SlotManager, "_check_load", no_check)
    monkeypatch.setattr(SlotManager, "_await_ready", ready)
    monkeypatch.setattr("hal0.slots.manager._preload_admit", admit)
    return log


def _failures(sm: SlotManager, name: str) -> Any:
    rec = sm._states.get(sm._key(name))
    return (rec.extra or {}).get("load_failures") if rec else None


def _spawns(events: list[tuple[str, str]]) -> list[str]:
    return [n for kind, n in events if kind == "spawn"]


def _stops(events: list[tuple[str, str]]) -> list[str]:
    return [n for kind, n in events if kind == "stop"]


async def test_load_perop_while_flm_ready_raises_npu_conflict_and_stays_offline(
    tmp_hal0_home: str, events: list[tuple[str, str]]
) -> None:
    _flm(tmp_hal0_home)
    _flm_shadow(tmp_hal0_home, "flm", "stt", "transcription", 8084)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    await sm.load("flm")

    with pytest.raises(SlotNpuConflict) as exc:
        await sm.load("px")

    err = exc.value
    assert err.code == "slot.npu_conflict"
    assert err.status == 409
    assert set(err.details) == {
        "slot",
        "holders",
        "holder_engine",
        "unloads",
        "pinned",
        "pending",
        "reason",
    }
    assert err.details["slot"] == "px"
    assert err.details["holders"] == ["flm"]
    assert err.details["holder_engine"] == "flm"
    assert err.details["unloads"] == ["flm", "flm-stt"]
    assert err.details["pinned"] is False
    assert err.details["pending"] is False
    assert sm.state("px") == SlotState.OFFLINE
    assert not _failures(sm, "px")
    assert sm.state("flm") == SlotState.READY
    assert _spawns(events) == ["flm"]
    assert sm._npu_pending == {}


async def test_load_flm_while_perop_ready_raises(
    tmp_hal0_home: str, events: list[tuple[str, str]]
) -> None:
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    await sm.load("px")

    with pytest.raises(SlotNpuConflict) as exc:
        await sm.load("flm")

    assert exc.value.details["holders"] == ["px"]
    assert exc.value.details["holder_engine"] == "xdna"
    assert sm.state("flm") == SlotState.OFFLINE
    assert not _failures(sm, "flm")
    assert sm.state("px") == SlotState.READY
    assert _spawns(events) == ["px"]


async def test_unload_npu_holder_unloads_flm_then_loads(
    tmp_hal0_home: str, events: list[tuple[str, str]]
) -> None:
    _flm(tmp_hal0_home)
    _flm_shadow(tmp_hal0_home, "flm", "stt", "transcription", 8084)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    await sm.load("flm")
    await sm.load("flm-stt")
    events.clear()

    snap = await sm.load("px", unload_npu_holder=True)

    assert snap.state == SlotState.READY
    assert sm.state("flm") == SlotState.OFFLINE
    assert sm.state("flm-stt") == SlotState.OFFLINE
    assert events.index(("stop", "flm")) < events.index(("spawn", "px"))
    assert sm._npu_pending == {}


async def test_unload_npu_holder_unloads_pinned_anchor(
    tmp_hal0_home: str, events: list[tuple[str, str]]
) -> None:
    # ``npu`` is a default-pinned anchor name; its TOML carries no pinned key.
    _flm(tmp_hal0_home, "npu")
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    await sm.load("npu")
    assert await sm.is_pinned("npu")

    with pytest.raises(SlotNpuConflict) as exc:
        await sm.load("px")
    assert exc.value.details["pinned"] is True

    snap = await sm.load("px", unload_npu_holder=True)
    assert snap.state == SlotState.READY
    assert sm.state("npu") == SlotState.OFFLINE
    assert ("stop", "npu") in events


async def test_concurrent_opposite_loads_one_wins_other_pending(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two opposite cold loads at once: exactly one runs, the other is 409 pending.

    The pre-STARTING step is slowed so both loads overlap in the window between
    admission and the STARTING transition. A guard that only reads slot states
    (no reservation) admits both there; so does one checked inside the
    per-slot lock, since the two slots' locks are different.
    """
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()

    async def slow_check(self: SlotManager, *a: Any, **k: Any) -> None:
        await asyncio.sleep(0.05)

    monkeypatch.setattr(SlotManager, "_check_load", slow_check)

    async def attempt(name: str) -> object:
        try:
            return await sm.load(name, unload_npu_holder=True)
        except SlotNpuConflict as exc:
            return exc

    results = await asyncio.wait_for(asyncio.gather(attempt("flm"), attempt("px")), timeout=5.0)
    refused = [r for r in results if isinstance(r, SlotNpuConflict)]
    assert len(refused) == 1, results
    assert refused[0].details["pending"] is True
    loser = refused[0].details["slot"]
    winner = "px" if loser == "flm" else "flm"
    assert sm.state(winner) == SlotState.READY
    assert sm.state(loser) == SlotState.OFFLINE
    assert _spawns(events) == [winner]
    assert _stops(events) == []
    assert sm._npu_pending == {}


async def test_restart_flm_while_perop_ready_refuses_without_unloading_flm(
    tmp_hal0_home: str, events: list[tuple[str, str]]
) -> None:
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    await sm.load("px")
    # The overlap the guard exists to prevent, as an older install could leave it.
    await sm._transition("flm", SlotState.READY, model_id="gemma3-4b-FLM", force=True)
    events.clear()

    with pytest.raises(SlotNpuConflict):
        await sm.restart("flm")

    assert sm.state("flm") == SlotState.READY
    assert sm.state("px") == SlotState.READY
    assert events == []


async def test_fused_and_flm_load_together_unchanged(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "fx", 8091, per_op=False)
    monkeypatch.setattr(
        "hal0.slots.npu_conflict.effective_xdna_path", lambda env, model_path: "fused"
    )
    sm = SlotManager()

    await sm.load("flm")
    snap = await sm.load("fx")

    assert snap.state == SlotState.READY
    assert sm.state("flm") == SlotState.READY
    assert _spawns(events) == ["flm", "fx"]


async def test_dispatcher_style_load_never_unloads(
    tmp_hal0_home: str, events: list[tuple[str, str]]
) -> None:
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    await sm.load("flm")

    with pytest.raises(SlotNpuConflict):
        await sm.start("px")

    assert sm.state("flm") == SlotState.READY
    assert _stops(events) == []


def _error_flm_listing(monkeypatch: pytest.MonkeyPatch, sm: SlotManager, *, active: bool) -> None:
    """``list()`` reports FLM in ERROR; ``_is_active`` answers for its unit.

    The snapshot is pinned because ``status()`` adopts an ERROR slot whose unit
    is active back to READY, which would hide the branch under test.
    """
    from hal0.slots.manager import Slot

    flm = Slot("flm", state=SlotState.ERROR, model_id="gemma3-4b-FLM")

    async def listing(*, exclude: str | None = None) -> list[Slot]:
        return [s for s in (flm,) if s.name != exclude]

    real_is_active = sm._is_active

    async def is_active(name: str) -> bool:
        return active if name == "flm" else await real_is_active(name)

    monkeypatch.setattr(sm, "list", listing)
    monkeypatch.setattr(sm, "_is_active", is_active)


async def test_error_flm_with_its_unit_still_running_holds_the_npu(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An FLM anchor in ERROR whose unit runs (a crash loop) still takes the NPU."""
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    _error_flm_listing(monkeypatch, sm, active=True)

    with pytest.raises(SlotNpuConflict) as exc:
        await sm.load("px")
    assert exc.value.details["holders"] == ["flm"]
    assert _spawns(events) == []


async def test_error_flm_whose_unit_is_gone_does_not_block(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    _error_flm_listing(monkeypatch, sm, active=False)

    snap = await sm.load("px")
    assert snap.state == SlotState.READY


# ── fix round 1 ───────────────────────────────────────────────────────────────


async def test_duplicate_load_leaving_early_keeps_the_siblings_reservation(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two loads of px are admitted; L1 leaves early, L2 is still before STARTING.

    L1's exit must not drop L2's reservation: an FLM load arriving while L2 sits
    in ``_check_load`` has to see px as pending, not as an OFFLINE slot.
    """
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    entered, gate, both_admitted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    abandoned = iter([True])
    admissions: list[str] = []
    real_admit = sm._admit_npu_for_load

    async def admit(slot_name: str, *a: Any, **k: Any) -> None:
        await real_admit(slot_name, *a, **k)
        admissions.append(slot_name)
        if admissions.count("px") == 2:
            both_admitted.set()

    async def is_abandoned() -> bool:
        # L1 leaves only once L2 is admitted too, so L2 holds a reservation.
        if next(abandoned, False):
            await both_admitted.wait()
            return True
        return False

    async def gated_check(self: SlotManager, cfg: Any, *a: Any, **k: Any) -> None:
        if cfg.get("name") == "px":
            entered.set()
            await gate.wait()

    monkeypatch.setattr("hal0.slots.manager.is_abandoned", is_abandoned)
    monkeypatch.setattr(SlotManager, "_check_load", gated_check)
    monkeypatch.setattr(sm, "_admit_npu_for_load", admit)

    async def scenario() -> None:
        l1 = asyncio.create_task(sm.load("px"))
        l2 = asyncio.create_task(sm.load("px"))
        await entered.wait()
        assert l1.done() and sm.state("px") == SlotState.OFFLINE
        with pytest.raises(SlotNpuConflict) as exc:
            await sm.load("flm", unload_npu_holder=True)
        assert exc.value.details["pending"] is True
        gate.set()
        await asyncio.gather(l1, l2)

    await asyncio.wait_for(scenario(), timeout=5.0)
    assert sm.state("px") == SlotState.READY
    assert sm.state("flm") == SlotState.OFFLINE
    assert _spawns(events) == ["px"]
    assert sm._npu_pending == {}


async def test_opt_in_never_unloads_a_holder_still_warming(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    warming, gate = asyncio.Event(), asyncio.Event()

    async def gated_ready(self: SlotManager, slot_name: str, *a: Any, **k: Any) -> SlotState:
        if slot_name == "flm":
            warming.set()
            await gate.wait()
        return SlotState.READY

    monkeypatch.setattr(SlotManager, "_await_ready", gated_ready)

    async def scenario() -> None:
        flm = asyncio.create_task(sm.load("flm"))
        await warming.wait()
        assert sm.state("flm") == SlotState.WARMING
        with pytest.raises(SlotNpuConflict) as exc:
            await sm.load("px", unload_npu_holder=True)
        assert exc.value.details["pending"] is True
        assert exc.value.details["holders"] == ["flm"]
        gate.set()
        await flm

    await asyncio.wait_for(scenario(), timeout=5.0)
    assert sm.state("flm") == SlotState.READY
    assert sm.state("px") == SlotState.OFFLINE
    assert _stops(events) == []


async def test_slot_that_went_down_while_queued_is_rechecked_under_the_lock(
    tmp_hal0_home: str, events: list[tuple[str, str]], container_stub: FakeContainerProvider
) -> None:
    """px was READY when its load checked, then went down and FLM came up meanwhile."""
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    await sm.load("px")
    events.clear()

    lock = sm._lock("px")
    await lock.acquire()
    task = asyncio.create_task(sm.load("px", unload_npu_holder=True))
    for _ in range(20):
        await asyncio.sleep(0)
    assert not task.done()
    await sm._transition("px", SlotState.OFFLINE, force=True)
    container_stub.active.discard("px")
    await sm._transition("flm", SlotState.READY, model_id="gemma3-4b-FLM", force=True)
    container_stub.active.add("flm")
    lock.release()

    with pytest.raises(SlotNpuConflict):
        await asyncio.wait_for(task, timeout=5.0)
    assert sm.state("px") == SlotState.OFFLINE
    assert sm.state("flm") == SlotState.READY
    assert events == []
    assert sm._npu_pending == {}


async def test_swap_fused_to_per_op_beside_flm_is_refused_before_anything_changes(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "fx", 8091, per_op=False)

    async def path_by_model(self: SlotManager, cfg: Any, model_id: str | None) -> str:
        return "per-op" if model_id == "perop-m" else "fused"

    monkeypatch.setattr(SlotManager, "_npu_xdna_path", path_by_model)
    sm = SlotManager()
    await sm.load("flm")
    await sm.load("fx")
    events.clear()
    persisted: list[str] = []

    async def persist(self: SlotManager, slot_name: str, model_id: str) -> None:
        persisted.append(model_id)

    monkeypatch.setattr(SlotManager, "_persist_model_default", persist)

    with pytest.raises(SlotNpuConflict):
        await sm.swap("fx", "perop-m")

    assert persisted == []
    cfg = await sm._load_slot_config("fx")
    assert cfg["model"]["default"] == "qwen3-1.7b"
    assert sm.state("fx") == SlotState.READY
    assert sm.state("flm") == SlotState.READY
    assert events == []


async def test_failed_holder_unload_releases_the_reservation(
    tmp_hal0_home: str, events: list[tuple[str, str]], container_stub: FakeContainerProvider
) -> None:
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    await sm.load("flm")
    container_stub.fail_unload = RuntimeError("systemctl stop wedged")

    with pytest.raises(RuntimeError, match="stop wedged"):
        await sm.load("px", unload_npu_holder=True)

    assert sm._npu_pending == {}
    assert sm.state("px") == SlotState.OFFLINE
    assert not _failures(sm, "px")
    assert "px" not in _spawns(events)


async def test_peer_whose_model_lookup_raises_is_judged_per_op(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad peer lookup yields a verdict (per-op, the safe side), not a raw error."""
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090, per_op=False)
    sm = SlotManager()
    await sm.load("px")

    async def broken(self: SlotManager, *a: Any, **k: Any) -> dict[str, Any]:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(SlotManager, "_resolve_model_info", broken)

    with pytest.raises(SlotNpuConflict) as exc:
        await sm.load("flm")
    assert exc.value.details["holders"] == ["px"]


async def test_incoming_slot_whose_model_lookup_raises_is_judged_per_op(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slot being loaded fails safe to per-op too, instead of escaping admission."""
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "px", 8090, per_op=False)
    sm = SlotManager()
    await sm.load("flm")

    async def broken(self: SlotManager, *a: Any, **k: Any) -> dict[str, Any]:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(SlotManager, "_resolve_model_info", broken)

    with pytest.raises(SlotNpuConflict) as exc:
        await sm.load("px")
    assert exc.value.details["holders"] == ["flm"]
    assert sm.state("px") == SlotState.OFFLINE


async def test_refusal_messages_tell_the_operator_what_to_do(
    tmp_hal0_home: str, events: list[tuple[str, str]]
) -> None:
    _flm(tmp_hal0_home)
    _flm_shadow(tmp_hal0_home, "flm", "stt", "transcription", 8084)
    _xdna(tmp_hal0_home, "px", 8090)
    sm = SlotManager()
    await sm.load("flm")

    with pytest.raises(SlotNpuConflict) as settled:
        await sm.load("px")
    msg = str(settled.value)
    assert "flm" in msg and "unload_npu_holder" in msg
    assert "flm-stt" in msg
    # Read on every surface (dashboard toasts too): the operator's choices
    # come first, the API flag last.
    assert "Stop flm, flm-stt first, or start px from the NPU tab" in msg
    assert msg.endswith("(API: unload_npu_holder: true).")
    assert "Pass unload_npu_holder" not in msg
    assert settled.value.details["pending"] is False

    # A holder still loading: the opt-in cannot help, so the message says wait/stop.
    sm._npu_pending["flm"] = "flm"
    with pytest.raises(SlotNpuConflict) as pend:
        await sm.load("px", unload_npu_holder=True)
    pmsg = str(pend.value)
    assert pend.value.details["pending"] is True
    assert "still loading on the NPU" in pmsg
    assert "wait for it to finish, or stop it first" in pmsg
    assert "unload_npu_holder" not in pmsg


async def test_integer_fused_layer_zero_is_per_op_without_reading_the_model(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An int ``GGML_XDNA_FUSED_LAYER = 0`` in the TOML forces per-op, as "0" does."""
    sm = SlotManager()

    async def must_not_resolve(self: SlotManager, *a: Any, **k: Any) -> dict[str, Any]:
        raise AssertionError("the model fit must not be read when the env forces per-op")

    monkeypatch.setattr(SlotManager, "_resolve_model_info", must_not_resolve)
    cfg = {
        "name": "px",
        "device": "npu",
        "binary": "xdna",
        "xdna": {"env": {"GGML_XDNA_FUSED_LAYER": 0}},
    }
    assert await sm._npu_xdna_path(cfg, "qwen3-1.7b") == "per-op"


async def test_drifted_fused_slot_is_not_reconverged_into_a_per_op_conflict(
    tmp_hal0_home: str, events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """I1: a ready fused slot whose TOML now names a per-op model keeps serving beside FLM.

    The converge branch judges the NEW config before tearing the unit down;
    a conflict leaves the old unit up, FLM untouched, and a warning on the slot.
    """
    _flm(tmp_hal0_home)
    _xdna(tmp_hal0_home, "fx", 8091, per_op=False)

    async def path_by_model(self: SlotManager, cfg: Any, model_id: str | None) -> str:
        return "per-op" if model_id == "perop-m" else "fused"

    monkeypatch.setattr(SlotManager, "_npu_xdna_path", path_by_model)
    sm = SlotManager()
    await sm.load("flm")
    await sm.load("fx")
    assert sm.state("fx") == SlotState.READY
    events.clear()

    # The operator edits the model without restarting; the running unit drifts.
    toml = Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "fx.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('"qwen3-1.7b"', '"perop-m"'), encoding="utf-8"
    )

    async def drifted(self: SlotManager, slot_name: str, cfg: Any) -> bool:
        return slot_name == "fx"

    monkeypatch.setattr(SlotManager, "_should_converge", drifted)

    slot = await sm.load("fx")

    assert events == []  # no stop, no spawn: the fused unit keeps serving
    assert sm.state("fx") == SlotState.READY
    assert sm.state("flm") == SlotState.READY
    assert sm._npu_pending == {}
    warning = slot.metadata.get("npu_conflict_warning")
    assert warning and "flm" in warning and "edited config not applied" in warning
    assert slot.metadata.get("message") == warning

    # Once the drift is gone (TOML reverted), the warning retires.
    monkeypatch.setattr(SlotManager, "_should_converge", lambda *a, **k: _false())
    slot = await sm.load("fx")
    assert "npu_conflict_warning" not in slot.metadata
    assert events == []


async def _false() -> bool:
    return False
