"""Tests for the mutating ``hal0 slot`` verbs' HTTP timeout (#1832).

The server-side lifecycle handlers (load/unload/restart/swap) block the
HTTP response until the slot's state machine converges — up to 180s for
a cold load (``providers.container._HEALTH_TIMEOUT_S``), and longer still
for restart/swap, which unload then load. ``api_post``'s default read
timeout is 10s, an 18x mismatch: any lifecycle op over ~10s used to raise
``ReadTimeout`` and exit 1 while the operation continued and completed
successfully server-side. These call sites must pass an explicit timeout
with headroom over the server's worst-case budget.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import slot_commands

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        captured["method"] = "POST"
        captured["path"] = path
        captured["body"] = json or {}
        captured["kwargs"] = kw
        return {"state": "serving", "model_id": (json or {}).get("model_id")}

    monkeypatch.setattr(slot_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(slot_commands, "api_post", fake_post)
    return captured


#: Sequential unloads a single ``load`` is expected to survive:
#: ``preload_evict.admit`` awaits ``host.unload(candidate)`` once per evicted
#: candidate, in series, before the spawn. Two is the modest case (a box with
#: two resident slots to free); the client budget must at minimum cover it.
_MIN_EVICTION_UNLOADS = 2


def _server_worst_case_s(*, loads: int = 1, unloads: int = 1, slots: int = 1) -> float:
    """The server's own floor for this endpoint, read from the server modules.

    Deliberately computed from ``providers.container`` and ``SlotManager``
    rather than from ``hal0.slot_lifecycle_budget`` — comparing the client
    timeout against the constant it was derived from would be a tautology.
    Retuning either server bound upward makes these tests fail, which is the
    point: #1832 regressed because the client number was hand-picked.
    """
    from hal0.providers.container import _HEALTH_TIMEOUT_S
    from hal0.slots.manager import SlotManager

    terminate = float(SlotManager._terminate_timeout_s)
    per_slot = loads * (float(_HEALTH_TIMEOUT_S) + _MIN_EVICTION_UNLOADS * terminate)
    per_slot += unloads * terminate
    return per_slot * slots


def _timeout_ge_server_budget(
    kwargs: dict[str, Any], *, loads: int = 1, unloads: int = 1, slots: int = 1
) -> None:
    """The CLI's client-side read timeout must clear the server's worst case.

    Not "beat the old 10s default" and not "beat 180s": the endpoint blocks
    for the health poll *plus* the terminates it performs (its own unload, and
    the pre-load evictions ``preload_evict.admit`` runs inside the load path).
    """
    floor = _server_worst_case_s(loads=loads, unloads=unloads, slots=slots)
    assert "timeout" in kwargs, "lifecycle call site passed no explicit timeout kwarg"
    assert kwargs["timeout"] >= floor, (
        f"timeout={kwargs['timeout']} is under the server's {floor}s worst case "
        f"(loads={loads}, unloads={unloads}, slots={slots})"
    )


def test_budget_constants_track_the_server_modules() -> None:
    """The budget module mirrors the server bounds — drift must break the build.

    ``slot_lifecycle_budget`` is the single source of truth the server modules
    import from; if someone re-declares a literal in ``container.py`` or
    ``SlotManager`` this fails instead of silently under-budgeting the CLI.
    """
    from hal0.providers.container import _HEALTH_TIMEOUT_S
    from hal0.slot_lifecycle_budget import (
        EVICTION_UNLOAD_ALLOWANCE,
        HEALTH_TIMEOUT_S,
        TERMINATE_TIMEOUT_S,
    )
    from hal0.slots.manager import SlotManager

    assert _HEALTH_TIMEOUT_S == HEALTH_TIMEOUT_S
    assert SlotManager._terminate_timeout_s == TERMINATE_TIMEOUT_S
    assert EVICTION_UNLOAD_ALLOWANCE >= _MIN_EVICTION_UNLOADS


def test_slot_load_passes_lifecycle_timeout(captured: dict[str, Any]) -> None:
    result = runner.invoke(slot_commands.app, ["load", "primary"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/slots/primary/load"
    _timeout_ge_server_budget(captured["kwargs"], loads=1, unloads=0)


def test_slot_unload_passes_lifecycle_timeout(captured: dict[str, Any]) -> None:
    result = runner.invoke(slot_commands.app, ["unload", "primary"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/slots/primary/unload"
    _timeout_ge_server_budget(captured["kwargs"], loads=0, unloads=1)


def test_slot_restart_passes_lifecycle_timeout(captured: dict[str, Any]) -> None:
    result = runner.invoke(slot_commands.app, ["restart", "primary"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/slots/primary/restart"
    _timeout_ge_server_budget(captured["kwargs"])


def test_slot_swap_passes_lifecycle_timeout(captured: dict[str, Any]) -> None:
    result = runner.invoke(
        slot_commands.app, ["swap", "primary", "--model", "demo", "--no-persist"]
    )
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/slots/primary/swap"
    _timeout_ge_server_budget(captured["kwargs"])


def test_slot_delete_passes_the_unload_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE /api/slots/{name} unloads a running slot before removing it.

    ``SlotManager.delete`` awaits ``unload`` for a live slot, so the delete
    blocks on the state machine too and cannot ride api_delete's 10s default
    (#1832) — the CLI would report a failed delete while the server finishes
    stopping and removing the slot.
    """
    seen: dict[str, Any] = {}

    def fake_delete(path: str, **kw: Any) -> dict[str, Any]:
        seen["path"] = path
        seen["kwargs"] = kw
        return {}

    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(slot_commands, "api_delete", fake_delete)
    result = runner.invoke(slot_commands.app, ["delete", "primary", "--force"])
    assert result.exit_code == 0, result.output
    assert seen["path"] == "/api/slots/primary"
    _timeout_ge_server_budget(seen["kwargs"], loads=0, unloads=1)


def test_sweep_budget_scales_the_lock_wait_per_slot() -> None:
    """A fan-out sweep takes each slot's lock separately (#1832).

    ``/api/updates/restart-slots`` calls ``sm.restart`` per target, so every
    target can independently queue behind an in-flight op on that slot —
    charging one lock-wait for the whole request under-budgets the sweep.
    """
    from hal0.slot_lifecycle_budget import slot_lifecycle_timeout_s

    one = slot_lifecycle_timeout_s(loads=1, unloads=1, slots=1)
    three = slot_lifecycle_timeout_s(loads=1, unloads=1, slots=3)
    assert three >= 3 * one


def test_restart_budget_charges_both_lock_acquisitions() -> None:
    """restart is unload-then-load and takes the slot lock TWICE (#1832).

    ``SlotManager.restart`` releases the lock after ``unload`` and reacquires
    it inside ``load``, so another queued lifecycle op can win the gap and be
    waited on a second time. Charging one lock allowance for the compound verb
    under-budgets exactly the contended case the widening exists for.
    """
    from hal0.slot_lifecycle_budget import slot_lifecycle_timeout_s

    from hal0.slots.manager import SlotManager  # isort: skip

    terminate = float(SlotManager._terminate_timeout_s)
    load_phase = _server_load_lock_hold_s()
    # Two lock waits (each capped by a full load) plus the verb's own work.
    floor = 2 * load_phase + terminate + load_phase
    assert slot_lifecycle_timeout_s(loads=1, unloads=1) >= floor


def _server_load_lock_hold_s() -> float:
    """Worst wall-clock a single ``load`` can hold the slot lock, server-side.

    Summed from the server modules that actually spend it, not from
    ``slot_lifecycle_budget``'s own composite — the point is that a phase
    added to ``load`` without a matching budget edit fails here.
    """
    from hal0.providers.container import _HEALTH_TIMEOUT_S
    from hal0.slot_lifecycle_budget import OUTPUT_SANITY_CPU_TIMEOUT_S
    from hal0.slots.manager import SlotManager

    terminate = float(SlotManager._terminate_timeout_s)
    return (
        # Pre-spawn teardown: the drifted re-converge / ERROR-retry / stale
        # in-flight branches all terminate in place before re-spawning.
        terminate
        # preload_evict.admit unloads candidates in series, inside the lock.
        + _MIN_EVICTION_UNLOADS * terminate
        # The post-spawn /health poll.
        + float(_HEALTH_TIMEOUT_S)
        # The output-sanity gate (#1922): raw probe, then the chat fallback —
        # at the CPU lane's budget, which is what both requests get on a
        # device="cpu" slot (output_sanity.probe_budget_s). The client is
        # handed a slot NAME by every lifecycle verb and cannot know which
        # lane it is waiting on, so the floor has to charge the slower one.
        + 2 * float(OUTPUT_SANITY_CPU_TIMEOUT_S)
        # A failed gate stops the unit before the ERROR stamp, still in-lock.
        + terminate
    )


def test_load_budget_covers_a_contended_load_that_fails_the_gate() -> None:
    """One contender in front of you, both loads failing the gate (#1922).

    ``POST /api/slots/{name}/load`` charges one lock wait plus its own work,
    and both are now wider than they were: the output-sanity gate runs inside
    the lock and its failure path stops the unit before stamping ERROR. If the
    lock allowance is not recomputed when a phase is added to ``load``, the
    CLI/MCP client reports a timeout on a load the server is still converging
    — the #1832 shape, re-introduced from the inside.
    """
    from hal0.slot_lifecycle_budget import slot_lifecycle_timeout_s

    floor = 2 * _server_load_lock_hold_s()
    assert slot_lifecycle_timeout_s(loads=1, unloads=0) >= floor, (
        "the load budget no longer covers one queued load plus your own"
    )


def test_slot_logs_one_shot_prints_hint_when_logs_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1905 follow-up (Codex): the one-shot ``hal0 slot logs hal0`` used to
    print a generic "no logs" even when the response carried the only
    explanation in ``hint`` (synthetic composite, journalctl missing)."""
    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _u: False)
    monkeypatch.setattr(
        slot_commands,
        "api_get",
        lambda path, **kw: {
            "name": "hal0",
            "logs": "",
            "hint": "synthetic composite slot has no journal of its own",
        },
    )

    result = runner.invoke(slot_commands.app, ["logs", "hal0"])

    assert result.exit_code == 0, result.output
    assert "synthetic composite" in result.output
    assert "no logs" not in result.output


def test_slot_logs_follow_terminates_on_degraded_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1905 follow-up (Codex): the server keeps a synthetic slot's degraded
    stream open with keepalives (so the browser EventSource doesn't
    error-reconnect), which would leave ``hal0 slot logs hal0 --follow``
    hanging until Ctrl-C. The CLI must treat ``event: degraded`` as
    terminal: print the message and return."""
    from contextlib import contextmanager

    from hal0.cli import _shared

    class _FakeStream:
        status_code = 200

        def iter_lines(self):
            yield "event: degraded"
            yield 'data: {"message":"synthetic composite slot has no journal of its own"}'
            yield ""
            # If the CLI does not stop at the degraded frame it would sit
            # here forever on a real stream; bound it so the test fails
            # loudly instead of hanging.
            for _ in range(5):
                yield ": keepalive"
            raise AssertionError("CLI kept reading past the terminal degraded frame")

    @contextmanager
    def fake_api_stream(method, path, *, timeout=None, params=None):
        yield _FakeStream()

    monkeypatch.setattr(_shared, "api_stream", fake_api_stream)
    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _u: False)

    result = runner.invoke(slot_commands.app, ["logs", "hal0", "--follow"])

    assert result.exit_code == 0, result.output
    assert "synthetic composite" in result.output
