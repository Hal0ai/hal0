"""#1224 part 2 — a load must converge the unit onto the slot's current config.

``load()`` short-circuits on a slot already in READY/SERVING/IDLE and returns
the current snapshot without re-rendering the unit. The sequence that bites:

    PUT /api/slots/ops/config {"port": 8091}   # TOML now says 8091
    hal0 slot load ops                         # returns the OLD snapshot
    # ...unit still running --port 8089; nothing listening on either port
    # after the next implicit reload cycles warming → error.

Recovery required ``systemctl reset-failed`` + a second load *from the error
state* — the only path that regenerates.

The fix reuses the drift comparator that already knows how to compare the
running argv against what a restart would render: an explicit load on a live
slot converges when they disagree, and stays a no-op when they don't.

That comparator watches six argv flags, which is only part of the effective
launch config. The follow-up gap: a changed runner **image** (``slot.binary`` /
``image_pin`` / ``RUNNER_IMAGES``), a changed **mount** (the model-file dir the
O25 heal adds), and changed ``[server].env`` are not argv at all, so the same
short-circuit swallowed those edits too — the same bug through a different
field. ``_should_converge`` now ORs in a whole-unit comparison: the on-disk unit
against a fresh, non-persisting render. See ``tests/providers/test_container.py
::TestUnitDrifted`` for the field-by-field proof that those three changes move
the rendered unit while leaving argv byte-identical.

Also covered here: ``terminate`` must be bounded. The stop runs as a blocking
call in an executor thread; without a timeout a wedged ``systemctl stop``
never returns and the caller (``restart``'s best-effort cleanup, the HTTP
request behind it) hangs forever — the original #1224 symptom. A bounded stop
cannot kill the thread, but it must hand control back so the caller can make
forward progress.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


async def _load_to_ready(sm: SlotManager, stub: FakeContainerProvider) -> None:
    await sm.load("chat")
    assert sm._current_state("chat") == SlotState.READY
    stub.load_calls.clear()
    stub.unload_calls.clear()


# ── load() convergence ───────────────────────────────────────────────────────


async def test_load_on_live_slot_converges_when_argv_drifted(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The headline bug: an edited config must reach the running unit."""
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    # The unit is running the old port; the TOML now renders a new one.
    container_stub.running_argv_by_slot["chat"] = ["--port", "8089", "--ctx-size", "4096"]
    container_stub.expected_argv_by_slot["chat"] = ["--port", "8091", "--ctx-size", "4096"]

    await sm.load("chat")

    assert container_stub.unload_calls, "drifted slot must be torn down"
    assert container_stub.load_calls, "drifted slot must be re-spawned on the new config"
    assert sm._current_state("chat") == SlotState.READY


async def test_load_on_live_slot_is_a_no_op_without_drift(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Idempotency must not regress — an unchanged slot is not restarted."""
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    argv = ["--port", "8089", "--ctx-size", "4096"]
    container_stub.running_argv_by_slot["chat"] = list(argv)
    container_stub.expected_argv_by_slot["chat"] = list(argv)

    await sm.load("chat")

    assert container_stub.unload_calls == [], "unchanged slot must not be torn down"
    assert container_stub.load_calls == [], "unchanged slot must not be re-spawned"
    assert sm._current_state("chat") == SlotState.READY


async def test_load_on_live_slot_no_ops_when_drift_is_unknowable(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """No argv on either side → the comparator returns None. Absence of
    evidence is not drift: keep the existing no-op rather than bouncing a
    healthy container on every load."""
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    container_stub.running_argv_by_slot["chat"] = None
    container_stub.expected_argv_by_slot["chat"] = None

    await sm.load("chat")

    assert container_stub.unload_calls == []
    assert container_stub.load_calls == []
    assert sm._current_state("chat") == SlotState.READY


async def test_converging_load_survives_a_failing_terminate(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The teardown half is best-effort; the re-spawn must still run."""
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    container_stub.running_argv_by_slot["chat"] = ["--port", "8089"]
    container_stub.expected_argv_by_slot["chat"] = ["--port", "8091"]
    monkeypatch.setattr(
        container_stub,
        "unload_sync",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("systemctl stop wedged")),
    )

    await sm.load("chat")

    assert container_stub.load_calls, "re-spawn must run even if teardown failed"
    assert sm._current_state("chat") == SlotState.READY


# ── load() convergence on the argv-invisible fields (image / mounts / env) ───


async def test_load_converges_when_only_the_rendered_unit_moved(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The gap: argv is byte-identical, but the rendered unit is not.

    A changed runner image / mount / env moves the unit and nothing else. Under
    the six-key argv comparison alone this load short-circuits to a snapshot and
    the operator's edit never reaches the container.
    """
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    argv = ["--port", "8089", "--ctx-size", "4096"]
    container_stub.running_argv_by_slot["chat"] = list(argv)
    container_stub.expected_argv_by_slot["chat"] = list(argv)
    container_stub.unit_drifted_by_slot["chat"] = True

    await sm.load("chat")

    assert container_stub.unload_calls, "unit drift must tear the slot down"
    assert container_stub.load_calls, "unit drift must re-spawn on the new render"
    assert sm._current_state("chat") == SlotState.READY


async def test_load_converges_on_unit_drift_even_when_argv_is_unknowable(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The two comparators are independent: a provider that cannot read the
    running argv (inspect failed) must not mask a unit that plainly moved."""
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    container_stub.running_argv_by_slot["chat"] = None
    container_stub.expected_argv_by_slot["chat"] = None
    container_stub.unit_drifted_by_slot["chat"] = True

    await sm.load("chat")

    assert container_stub.load_calls, "unit drift must still converge"
    assert sm._current_state("chat") == SlotState.READY


async def test_load_no_ops_when_the_unit_matches_the_render(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Idempotency across the WIDE check too: an unchanged unit is not a restart.

    This is also the adopted-container case — a slot with no unit on disk has
    nothing to converge onto, and the probe reports it the same way.
    """
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    argv = ["--port", "8089", "--ctx-size", "4096"]
    container_stub.running_argv_by_slot["chat"] = list(argv)
    container_stub.expected_argv_by_slot["chat"] = list(argv)
    container_stub.unit_drifted_by_slot["chat"] = False

    await sm.load("chat")

    assert container_stub.unload_calls == []
    assert container_stub.load_calls == []
    assert sm._current_state("chat") == SlotState.READY


async def test_load_no_ops_when_the_unit_render_fails(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A render (or a unit read) that raises is not evidence of drift.

    A convergence check must never be able to fail a ``load()`` that would
    otherwise succeed — so an exploding probe degrades to the fast no-op, not to
    a restart and not to an error.
    """
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    argv = ["--port", "8089"]
    container_stub.running_argv_by_slot["chat"] = list(argv)
    container_stub.expected_argv_by_slot["chat"] = list(argv)
    container_stub.fail_unit_drifted = RuntimeError("profile resolution blew up")

    await sm.load("chat")

    assert container_stub.unload_calls == []
    assert container_stub.load_calls == []
    assert sm._current_state("chat") == SlotState.READY


async def test_load_no_ops_when_provider_has_no_unit_probe(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that does not implement the probe keeps the old behaviour."""
    monkeypatch.delattr(type(container_stub), "unit_drifted")
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    argv = ["--port", "8089"]
    container_stub.running_argv_by_slot["chat"] = list(argv)
    container_stub.expected_argv_by_slot["chat"] = list(argv)

    await sm.load("chat")

    assert container_stub.load_calls == []
    assert sm._current_state("chat") == SlotState.READY


async def test_npu_trio_shadow_never_converges_on_unit_drift(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A trio shadow has no unit of its own — the wide check must not give it
    one to bounce. Its load stays the READY short-circuit."""
    (slot_root / "embed.toml").write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8093",
                'device = "npu"',
                'type = "embedding"',
                "enabled = true",
                "[model]",
                'default = "embed-gemma:300m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    sm = SlotManager()
    container_stub.unit_drifted_by_slot["embed"] = True

    await sm.load("embed")
    container_stub.load_calls.clear()
    container_stub.unload_calls.clear()

    await sm.load("embed")

    assert container_stub.unload_calls == [], "a trio shadow must never be bounced"
    assert container_stub.load_calls == []


async def test_changed_image_converges_through_the_real_renderer(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end over the seam, with the REAL unit renderer.

    The stub above proves the manager acts on the probe; this proves the probe
    a real ContainerProvider computes says "drifted" for the concrete operator
    edit in the issue — a re-pinned runner image — while argv is unchanged.
    No podman, no systemd: only the render + the unit file are real.
    """
    from hal0.providers.container import ContainerProvider

    unit_file = tmp_path / "hal0-slot@chat.container"
    real = ContainerProvider()
    monkeypatch.setattr(real, "_unit_path", lambda token: unit_file)
    monkeypatch.setattr(
        type(container_stub),
        "unit_drifted",
        lambda self, cfg, info: real.unit_drifted(cfg, info),
    )

    def _write_chat_toml(image_pin: str) -> None:
        (slot_root / "chat.toml").write_text(
            "\n".join(
                [
                    'name = "chat"',
                    "port = 8081",
                    'backend = "vulkan"',
                    'provider = "llama-server"',
                    f'image_pin = "{image_pin}"',
                    "enabled = true",
                    "[model]",
                    'default = "qwen3-4b-q4_k_m"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

    _write_chat_toml("ghcr.io/hal0ai/toolboxes:old")

    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    # Seed the unit the way a real load would have — same cfg, same model_info
    # resolution the manager threads into the check.
    cfg = await sm._load_slot_config("chat")
    assert cfg.get("image_pin") == "ghcr.io/hal0ai/toolboxes:old"
    model_info = await sm._resolve_model_info("qwen3-4b-q4_k_m")
    unit_file.write_text(real._render_quadlet_text(cfg, model_info))
    argv_before = real.expected_argv(cfg, model_info)
    assert argv_before, "the renderer must produce an argv for this slot"

    # The operator re-pins the image and reloads.
    _write_chat_toml("ghcr.io/hal0ai/toolboxes:new")
    sm._invalidate_cfg_cache("chat")
    new_cfg = await sm._load_slot_config("chat")
    assert new_cfg.get("image_pin") == "ghcr.io/hal0ai/toolboxes:new"
    assert real.expected_argv(new_cfg, model_info) == argv_before, "the image is not argv"

    await sm.load("chat")

    assert container_stub.load_calls, "a re-pinned image must reach the container"
    assert sm._current_state("chat") == SlotState.READY


# ── terminate() must be bounded ──────────────────────────────────────────────


async def test_terminate_is_bounded_when_stop_blocks(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocking stop must hand control back inside the timeout."""
    from hal0.slots.state import SlotTerminateTimeout

    release = threading.Event()
    monkeypatch.setattr(container_stub, "unload_sync", lambda cfg: release.wait(30))

    sm = SlotManager()
    try:
        with pytest.raises(SlotTerminateTimeout):
            await asyncio.wait_for(sm.terminate("chat", timeout_s=0.05), timeout=5.0)
    finally:
        release.set()  # let the executor thread retire


async def test_restart_from_error_survives_a_blocking_terminate(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original #1224 symptom, end to end: an errored slot whose stop
    wedges must still relaunch instead of hanging the caller forever."""
    sm = SlotManager()
    container_stub.fail_load = RuntimeError("spawn boom")
    with pytest.raises(RuntimeError):
        await sm.load("chat")
    assert sm._current_state("chat") == SlotState.ERROR

    release = threading.Event()
    real_unload = container_stub.unload_sync
    calls = {"n": 0}

    def _block_once(cfg: dict) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            release.wait(30)
            return
        real_unload(cfg)

    monkeypatch.setattr(container_stub, "unload_sync", _block_once)
    monkeypatch.setattr(SlotManager, "_terminate_timeout_s", 0.05, raising=False)
    container_stub.fail_load = None

    try:
        await asyncio.wait_for(sm.restart("chat"), timeout=10.0)
    finally:
        release.set()

    assert sm._current_state("chat") == SlotState.READY
