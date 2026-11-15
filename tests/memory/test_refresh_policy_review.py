"""ADR-0060 follow-ups from review of PR #3701.

* Scheduled consolidation tracks each bank against the fire it is for, sends
  a bank that is done never again, retries only what failed before it was
  sent, with a cap and a backoff, and does not loop once a minute when the
  state file cannot be written.
* The reconcile clears an ``enable_auto_consolidation`` override only when
  hal0 wrote it, and reports one it did not.
* A listed model with ``trigger: null`` is read on its own before it is judged.
* A new consolidation mode or schedule restarts the clock, also across a restart.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from hal0.config.schema import MemoryRefreshConfig
from hal0.memory import refresh_runner
from hal0.memory.refresh_policy import (
    apply_refresh_policy,
    desired_trigger,
    load_owned_auto_off,
    save_owned_auto_off,
)
from hal0.memory.refresh_runner import CONSOLIDATE_ATTEMPTS, RefreshPolicyRunner

BANKS = ["hal0:shared", "hal0:private"]


def _policy(**over: Any) -> MemoryRefreshConfig:
    return MemoryRefreshConfig(**over)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.t = at.timestamp()

    def __call__(self) -> float:
        return self.t

    def set(self, at: datetime) -> None:
        self.t = at.timestamp()

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://engine/x")
    return httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(status, request=request)
    )


class Engine:
    """``POST .../consolidate`` that answers per bank from a script of outcomes:
    ``"ok"`` or an exception to raise. The last outcome repeats."""

    def __init__(self, script: dict[str, list[Any]]) -> None:
        self.script = script
        self.posts: list[str] = []

    async def __call__(self, method: str, path: str, **_k: Any) -> Any:
        assert method == "POST" and path.endswith("/consolidate"), path
        bank = unquote(path.split("/")[4])
        self.posts.append(bank)
        steps = self.script.get(bank, ["ok"])
        outcome = steps.pop(0) if len(steps) > 1 else steps[0]
        if outcome == "ok":
            return {"operation_id": f"op-{len(self.posts)}"}
        raise outcome


def _runner(tmp_path: Path, clock: Clock, engine: Any, policy: Any = None) -> Any:
    holder = {"policy": policy or _policy(consolidation="scheduled")}
    runner = RefreshPolicyRunner(
        policy=lambda: holder["policy"],
        engine=lambda: engine,
        banks=lambda: list(BANKS),
        state_file=tmp_path / "sched.json",
        clock=clock,
    )
    return runner, holder


async def _to_due(runner: Any, clock: Clock) -> None:
    """Start the clock at 14:00 and move to just after the 02:00 fire."""
    clock.set(datetime(2026, 11, 15, 14, 0, tzinfo=UTC))
    await runner.step()
    clock.set(datetime(2026, 11, 16, 2, 0, 30, tzinfo=UTC))


# ── 1. scheduled consolidation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_bank_that_is_done_is_never_sent_again(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 15, tzinfo=UTC))
    engine = Engine({"hal0:private": [httpx.ConnectError("refused"), "ok"]})
    runner, _ = _runner(tmp_path, clock, engine)
    await _to_due(runner, clock)
    await runner.step()
    assert engine.posts == ["hal0:shared", "hal0:private"]
    # The retry waits for its backoff, not the next minute.
    clock.advance(30)
    await runner.step()
    assert engine.posts == ["hal0:shared", "hal0:private"]
    clock.advance(60)
    await runner.step()
    assert engine.posts == ["hal0:shared", "hal0:private", "hal0:private"]
    # Done: nothing more for this fire.
    for _ in range(5):
        clock.advance(600)
        await runner.step()
    assert engine.posts.count("hal0:shared") == 1
    assert engine.posts.count("hal0:private") == 2
    state = json.loads((tmp_path / "sched.json").read_text())
    assert state["due_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [_status_error(503), _status_error(429), httpx.ConnectError("refused")]
)
async def test_retries_are_bounded_and_backed_off(tmp_path: Path, error: Exception) -> None:
    clock = Clock(datetime(2026, 11, 15, tzinfo=UTC))
    engine = Engine({"hal0:shared": [error]})
    runner, _ = _runner(tmp_path, clock, engine)
    await _to_due(runner, clock)
    for _ in range(24 * 60):  # a minute at a time, for most of a day
        await runner.step()
        clock.advance(60)
        if clock.t >= datetime(2026, 11, 17, 1, 59, tzinfo=UTC).timestamp():
            break
    assert engine.posts.count("hal0:shared") == CONSOLIDATE_ATTEMPTS
    assert engine.posts.count("hal0:private") == 1
    assert runner.last_consolidation["failed"] == ["hal0:shared"]


@pytest.mark.asyncio
async def test_a_timeout_is_retried_once_then_taken_as_sent(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 15, tzinfo=UTC))
    engine = Engine({"hal0:shared": [httpx.ReadTimeout("slow")]})
    runner, _ = _runner(tmp_path, clock, engine)
    await _to_due(runner, clock)
    for _ in range(120):
        await runner.step()
        clock.advance(60)
    assert engine.posts.count("hal0:shared") == 2
    assert runner.last_consolidation["pending"] == []


@pytest.mark.asyncio
async def test_a_refused_request_is_given_up_at_once(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 15, tzinfo=UTC))
    engine = Engine({"hal0:shared": [_status_error(400)]})
    runner, _ = _runner(tmp_path, clock, engine)
    await _to_due(runner, clock)
    for _ in range(30):
        await runner.step()
        clock.advance(60)
    assert engine.posts.count("hal0:shared") == 1


@pytest.mark.asyncio
async def test_an_unwritable_state_file_does_not_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(datetime(2026, 11, 15, tzinfo=UTC))
    engine = Engine({})
    runner, _ = _runner(tmp_path, clock, engine)
    await _to_due(runner, clock)

    def fail(*_a: Any, **_k: Any) -> Any:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "write_text", fail)
    for _ in range(30):
        await runner.step()
        clock.advance(60)
    assert engine.posts == BANKS  # once each, then nothing


# ── 5. a new mode or schedule restarts the clock ─────────────────────────


@pytest.mark.asyncio
async def test_a_new_schedule_restarts_the_clock(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 15, 14, 0, tzinfo=UTC))
    engine = Engine({})
    runner, holder = _runner(tmp_path, clock, engine)
    await runner.step()  # clock started for 0 2 * * *
    # At 14:05 the new schedule's 14:00 fire is after the clock start, so it would
    # be due at once if the change did not restart the clock.
    clock.set(datetime(2026, 11, 15, 14, 5, tzinfo=UTC))
    holder["policy"] = _policy(consolidation="scheduled", consolidation_schedule="0 14 * * *")
    await runner.step()
    assert engine.posts == []
    clock.set(datetime(2026, 11, 16, 14, 0, 30, tzinfo=UTC))
    await runner.step()
    assert engine.posts == BANKS


@pytest.mark.asyncio
async def test_a_change_made_while_down_restarts_the_clock(tmp_path: Path) -> None:
    # A clock started for continuous long ago, then hal0.toml edited to scheduled.
    (tmp_path / "sched.json").write_text(
        json.dumps({"last_run_at": 0, "mode": "continuous", "schedule": "0 2 * * *"})
    )
    clock = Clock(datetime(2026, 11, 16, 9, 0, tzinfo=UTC))
    engine = Engine({})
    runner, _ = _runner(tmp_path, clock, engine)
    await runner.step()
    assert engine.posts == []
    # And a schedule edited while down, too.
    (tmp_path / "sched.json").write_text(
        json.dumps({"last_run_at": 0, "mode": "scheduled", "schedule": "0 4 * * *"})
    )
    runner2, _ = _runner(tmp_path, clock, engine)
    await runner2.step()
    assert engine.posts == []


# ── 2. an auto-consolidation override hal0 did not set ──────────────────


class ConfigEngine:
    def __init__(self, overrides: dict[str, dict[str, Any]]) -> None:
        self.overrides = overrides
        self.patches: list[tuple[str, dict[str, Any]]] = []

    async def __call__(
        self, method: str, path: str, *, params: Any = None, json_body: Any = None
    ) -> Any:
        if path == "/v1/default/banks":
            return {"banks": [{"bank_id": b} for b in self.overrides if b == params["q"]]}
        parts = path.split("/")
        bank = unquote(parts[4])
        if parts[5] == "mental-models":
            return {"items": [], "total": 0}
        if method == "GET":
            wanted = desired_trigger(_policy())
            return {
                "config": {
                    "mental_model_min_refresh_interval_seconds": 86400,
                    "knowledge_page_default_trigger": wanted,
                    **self.overrides[bank],
                },
                "overrides": dict(self.overrides[bank]),
            }
        self.patches.append((bank, json_body["updates"]))
        for k, v in json_body["updates"].items():
            if v is None:
                self.overrides[bank].pop(k, None)
            else:
                self.overrides[bank][k] = v
        return {}


@pytest.mark.asyncio
async def test_a_foreign_override_is_left_alone_and_reported(tmp_path: Path) -> None:
    engine = ConfigEngine({"hal0:shared": {"enable_auto_consolidation": False}})
    owned = tmp_path / "owned.json"
    for _ in range(3):  # boot, the 6 h pass, a save: never cleared
        report = await apply_refresh_policy(
            engine, ["hal0:shared"], _policy(), check_write_gate=False, owned_path=owned
        )
    assert engine.patches == []
    assert engine.overrides["hal0:shared"] == {"enable_auto_consolidation": False}
    assert report.banks[0].notes == [
        "auto-consolidation is off on hal0:shared by an override hal0 did not set; "
        "hal0 leaves it alone"
    ]


@pytest.mark.asyncio
async def test_an_override_hal0_set_is_cleared_when_going_continuous(tmp_path: Path) -> None:
    engine = ConfigEngine({"hal0:shared": {}})
    owned = tmp_path / "owned.json"
    await apply_refresh_policy(
        engine,
        ["hal0:shared"],
        _policy(consolidation="scheduled"),
        check_write_gate=False,
        owned_path=owned,
    )
    assert engine.patches == [("hal0:shared", {"enable_auto_consolidation": False})]
    assert load_owned_auto_off(owned) == {"hal0:shared"}
    report = await apply_refresh_policy(
        engine, ["hal0:shared"], _policy(), check_write_gate=False, owned_path=owned
    )
    assert engine.patches[-1] == ("hal0:shared", {"enable_auto_consolidation": None})
    assert engine.overrides["hal0:shared"] == {}
    assert load_owned_auto_off(owned) == set()
    assert report.banks[0].notes == []


@pytest.mark.asyncio
async def test_an_operator_false_kept_under_scheduled_stays_theirs(tmp_path: Path) -> None:
    """Already false when hal0 went scheduled: hal0 wrote nothing, so going
    back to continuous leaves the operator's override where it was."""
    engine = ConfigEngine({"hal0:shared": {"enable_auto_consolidation": False}})
    owned = tmp_path / "owned.json"
    await apply_refresh_policy(
        engine,
        ["hal0:shared"],
        _policy(consolidation="scheduled"),
        check_write_gate=False,
        owned_path=owned,
    )
    await apply_refresh_policy(
        engine, ["hal0:shared"], _policy(), check_write_gate=False, owned_path=owned
    )
    assert engine.patches == []
    assert engine.overrides["hal0:shared"] == {"enable_auto_consolidation": False}


def test_owned_state_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "x" / "owned.json"
    assert load_owned_auto_off(path) == set()
    save_owned_auto_off({"b", "a"}, path)
    assert load_owned_auto_off(path) == {"a", "b"}


# ── 3. a listing with a null trigger ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_null_trigger_row_is_read_on_its_own(tmp_path: Path) -> None:
    wanted = desired_trigger(_policy())
    calls: list[tuple[str, str]] = []

    async def engine(method: str, path: str, *, params: Any = None, json_body: Any = None) -> Any:
        calls.append((method, path))
        if path == "/v1/default/banks":
            return {"banks": [{"bank_id": "hal0:shared"}]}
        if path.endswith("/mental-models"):
            return {
                "items": [{"id": "settled", "trigger": None}, {"id": "stale", "trigger": None}],
                "total": 2,
            }
        if path.endswith("/mental-models/settled"):
            return {"id": "settled", "trigger": {"mode": "delta", **wanted}}
        if path.endswith("/mental-models/stale"):
            if method == "GET":
                return {"id": "stale", "trigger": {"refresh_after_consolidation": True}}
            return {}
        if path.endswith("/config"):
            if method == "GET":
                return {
                    "config": {
                        "mental_model_min_refresh_interval_seconds": 86400,
                        "knowledge_page_default_trigger": wanted,
                    },
                    "overrides": {},
                }
            return {}
        raise AssertionError(path)

    report = await apply_refresh_policy(
        engine, ["hal0:shared"], _policy(), check_write_gate=False, owned_path=tmp_path / "o.json"
    )
    assert report.banks[0].models_patched == ["stale"]
    patches = [p for m, p in calls if m == "PATCH"]
    assert patches == ["/v1/default/banks/hal0%3Ashared/mental-models/stale"]


def test_backoff_constants_are_sane() -> None:
    assert refresh_runner.CONSOLIDATE_ATTEMPTS >= 2
    assert refresh_runner.CONSOLIDATE_BACKOFF_CAP_S <= 3600
