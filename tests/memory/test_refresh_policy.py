"""The knowledge-page refresh policy (ADR-0060): config, cron, reconcile, runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from pydantic import ValidationError

from hal0.config.cron import CronError, next_fire, previous_fire, validate_cron
from hal0.config.schema import Hal0Config, MemoryRefreshConfig
from hal0.memory import write_gate
from hal0.memory.refresh_policy import (
    apply_refresh_policy,
    bank_config_updates,
    desired_trigger,
    enforce_trigger,
    trigger_patch,
)
from hal0.memory.refresh_runner import RefreshPolicyRunner, provider_banks, provider_engine

# ── config ───────────────────────────────────────────────────────────────


def test_defaults_are_a_nightly_refresh_and_continuous_consolidation() -> None:
    cfg = Hal0Config().memory.refresh
    assert cfg.pages == "scheduled"
    assert cfg.schedule == "0 3 * * *"
    assert cfg.min_interval_s == 86400
    assert cfg.consolidation == "continuous"
    assert cfg.consolidation_schedule == "0 2 * * *"


@pytest.mark.parametrize(
    "field,value",
    [
        ("schedule", "0 3 * *"),
        ("schedule", "61 * * * *"),
        ("schedule", "@daily"),
        ("consolidation_schedule", "0 0 31 2 *"),
        ("pages", "sometimes"),
        ("consolidation", "never"),
        ("min_interval_s", -1),
        ("min_interval_s", 31 * 86400),
        ("unknown_key", 1),
    ],
)
def test_bad_values_are_refused(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        MemoryRefreshConfig.model_validate({field: value})


def test_a_schedule_is_stored_with_single_spaces() -> None:
    assert MemoryRefreshConfig(schedule="  0  */6 * * * ").schedule == "0 */6 * * *"


# ── cron ─────────────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 15, 14, 7, tzinfo=UTC)  # a Sunday


@pytest.mark.parametrize(
    "expr,nxt,prev",
    [
        ("0 3 * * *", datetime(2026, 11, 16, 3, 0), datetime(2026, 11, 15, 3, 0)),
        ("*/15 * * * *", datetime(2026, 11, 15, 14, 15), datetime(2026, 11, 15, 14, 0)),
        ("0 */6 * * *", datetime(2026, 11, 15, 18, 0), datetime(2026, 11, 15, 12, 0)),
        ("30 4 * * 1", datetime(2026, 11, 16, 4, 30), datetime(2026, 11, 9, 4, 30)),
        ("0 4 * * 7", datetime(2026, 11, 22, 4, 0), datetime(2026, 11, 15, 4, 0)),
        ("0 0 1 1 *", datetime(2027, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 0)),
        # Day of month and day of week both set: either matches.
        ("0 9 13 * 5", datetime(2026, 11, 20, 9, 0), datetime(2026, 11, 13, 9, 0)),
        ("5,35 1-2 * * *", datetime(2026, 11, 16, 1, 5), datetime(2026, 11, 15, 2, 35)),
    ],
)
def test_next_and_previous_fire(expr: str, nxt: datetime, prev: datetime) -> None:
    assert next_fire(expr, NOW) == nxt.replace(tzinfo=UTC)
    assert previous_fire(expr, NOW) == prev.replace(tzinfo=UTC)


def test_previous_fire_includes_the_current_minute_and_next_excludes_it() -> None:
    at = datetime(2026, 11, 15, 3, 0, 30, tzinfo=UTC)
    assert previous_fire("0 3 * * *", at) == datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
    assert next_fire("0 3 * * *", at) == datetime(2026, 11, 16, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "bad", ["", "* * * *", "a * * * *", "*/0 * * * *", "5-1 * * * *", "0 24 * * *", "0 0 * * 8"]
)
def test_validate_cron_refuses(bad: str) -> None:
    with pytest.raises(CronError):
        validate_cron(bad)


# ── the pure policy ──────────────────────────────────────────────────────


def _policy(**over: Any) -> MemoryRefreshConfig:
    return MemoryRefreshConfig(**over)


def test_desired_trigger_per_mode() -> None:
    assert desired_trigger(_policy()) == {
        "refresh_after_consolidation": False,
        "refresh_cron": "0 3 * * *",
        "min_refresh_interval_seconds": 86400,
    }
    assert desired_trigger(_policy(pages="after_consolidation", min_interval_s=3600)) == {
        "refresh_after_consolidation": True,
        "refresh_cron": None,
        "min_refresh_interval_seconds": 3600,
    }
    assert desired_trigger(_policy(pages="manual")) == {
        "refresh_after_consolidation": False,
        "refresh_cron": None,
        "min_refresh_interval_seconds": 86400,
    }


def test_enforce_trigger_keeps_the_writers_other_fields_and_names_what_it_replaced() -> None:
    sent = {
        "mode": "delta",
        "budget": "low",
        "tags_match": "all",
        "refresh_after_consolidation": True,
        "min_refresh_interval_seconds": 86400,
    }
    out, replaced = enforce_trigger(sent, _policy())
    assert out == {
        "mode": "delta",
        "budget": "low",
        "tags_match": "all",
        "refresh_after_consolidation": False,
        "refresh_cron": "0 3 * * *",
        "min_refresh_interval_seconds": 86400,
    }
    # Same value: not reported. Left out: filled in, not reported.
    assert replaced == ["trigger.refresh_after_consolidation"]
    assert enforce_trigger(None, _policy())[1] == []


def test_trigger_patch_is_none_when_the_timing_already_matches() -> None:
    stored = {
        "mode": "delta",
        "tag_groups": [{"tags": ["project:x"]}],
        "refresh_after_consolidation": False,
        "refresh_cron": "0 3 * * *",
        "min_refresh_interval_seconds": 86400,
    }
    assert trigger_patch(stored, _policy()) is None
    patch = trigger_patch({**stored, "refresh_cron": None}, _policy())
    # Only the timing fields are sent; the engine merges them over the rest.
    assert patch == desired_trigger(_policy())
    assert trigger_patch(None, _policy()) == desired_trigger(_policy())


def test_bank_config_updates() -> None:
    current = {
        "config": {
            "mental_model_min_refresh_interval_seconds": 1800,
            "knowledge_page_default_trigger": {
                "mode": "delta",
                "budget": "low",
                "refresh_after_consolidation": True,
                "min_refresh_interval_seconds": 1800,
            },
            "enable_auto_consolidation": True,
        },
        "overrides": {},
    }
    updates = bank_config_updates(_policy(), current)
    assert updates == {
        "mental_model_min_refresh_interval_seconds": 86400,
        "knowledge_page_default_trigger": {
            "mode": "delta",
            "budget": "low",
            "refresh_after_consolidation": False,
            "refresh_cron": "0 3 * * *",
            "min_refresh_interval_seconds": 86400,
        },
    }
    settled = {
        "config": {
            "mental_model_min_refresh_interval_seconds": 86400,
            "knowledge_page_default_trigger": updates["knowledge_page_default_trigger"],
        },
        "overrides": {},
    }
    assert bank_config_updates(_policy(), settled) == {}
    # Scheduled consolidation turns the bank's own off ...
    assert bank_config_updates(_policy(consolidation="scheduled"), settled) == {
        "enable_auto_consolidation": False
    }
    off = {**settled, "overrides": {"enable_auto_consolidation": False}}
    assert bank_config_updates(_policy(consolidation="scheduled"), off) == {}
    # ... and continuous clears the override (null) only when hal0 set it, never sets true.
    assert bank_config_updates(_policy(), off, owns_auto_off=True) == {
        "enable_auto_consolidation": None
    }
    assert bank_config_updates(_policy(), off) == {}


# ── the reconcile against a fake engine ──────────────────────────────────


class FakeEngine:
    """The engine endpoints the reconcile calls, recording every write."""

    def __init__(self, banks: dict[str, list[dict[str, Any]]]) -> None:
        self.models = {b: {m["id"]: m for m in ms} for b, ms in banks.items()}
        self.config: dict[str, dict[str, Any]] = {b: {} for b in banks}
        self.overrides: dict[str, dict[str, Any]] = {b: {} for b in banks}
        self.writes: list[tuple[str, str, Any]] = []
        self.fail_patch: set[str] = set()

    async def __call__(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: Any = None
    ) -> Any:
        parts = path.split("/")
        if path == "/v1/default/banks":
            q = (params or {}).get("q")
            return {"banks": [{"bank_id": b} for b in self.models if b == q]}
        bank = unquote(parts[4])
        if parts[5] == "mental-models" and method == "GET":
            assert params and params["detail"] == "content"
            rows = list(self.models[bank].values())
            off, lim = params["offset"], params["limit"]
            return {"items": rows[off : off + lim], "total": len(rows)}
        if parts[5] == "mental-models" and method == "PATCH":
            mid = unquote(parts[6])
            self.writes.append((method, path, json_body))
            if mid in self.fail_patch:
                request = httpx.Request("PATCH", "http://engine" + path)
                raise httpx.HTTPStatusError(
                    "boom", request=request, response=httpx.Response(400, request=request)
                )
            # The engine merges a PATCHed trigger over the stored one.
            model = self.models[bank][mid]
            model["trigger"] = {**(model.get("trigger") or {}), **json_body["trigger"]}
            return model
        if parts[5] == "config" and method == "GET":
            return {"config": dict(self.config[bank]), "overrides": dict(self.overrides[bank])}
        if parts[5] == "config" and method == "PATCH":
            self.writes.append((method, path, json_body))
            for k, v in json_body["updates"].items():
                if v is None:
                    self.overrides[bank].pop(k, None)
                    self.config[bank].pop(k, None)
                else:
                    self.overrides[bank][k] = v
                    self.config[bank][k] = v
            return {}
        raise AssertionError(f"unexpected {method} {path}")


def _model(mid: str, **trigger: Any) -> dict[str, Any]:
    base = {
        "mode": "delta",
        "budget": "low",
        "tag_groups": [{"tags": ["project:hal0"], "match": "all_strict"}],
        "exclude_mental_models": True,
        "fact_types": ["observation"],
        "full_refresh_cron": "0 4 * * 0",
    }
    return {"id": mid, "name": mid, "trigger": {**base, **trigger}}


@pytest.mark.asyncio
async def test_reconcile_patches_only_the_pages_that_differ_and_keeps_their_other_fields() -> None:
    wanted = desired_trigger(_policy())
    engine = FakeEngine(
        {
            "hal0:shared": [
                _model("old", refresh_after_consolidation=True, min_refresh_interval_seconds=1800),
                _model("ok", **wanted),
            ]
        }
    )
    report = await apply_refresh_policy(engine, ["hal0:shared"], _policy(), check_write_gate=False)
    assert report.ok
    bank = report.banks[0]
    assert bank.models_seen == 2
    assert bank.models_patched == ["old"]
    assert bank.models_unchanged == 1
    patched = engine.models["hal0:shared"]["old"]["trigger"]
    assert patched == {**_model("old")["trigger"], **wanted}
    # Every write sends only the timing fields.
    model_writes = [w for w in engine.writes if "/mental-models/" in w[1]]
    assert [w[2] for w in model_writes] == [{"trigger": wanted}]
    assert set(bank.config_updates) == {
        "mental_model_min_refresh_interval_seconds",
        "knowledge_page_default_trigger",
    }

    # A second run writes nothing at all.
    engine.writes.clear()
    again = await apply_refresh_policy(engine, ["hal0:shared"], _policy(), check_write_gate=False)
    assert engine.writes == []
    assert again.banks[0].models_patched == []
    assert again.banks[0].config_updates == {}


@pytest.mark.asyncio
async def test_reconcile_pages_through_the_list_and_reports_a_failed_page() -> None:
    models = [_model(f"m{i:03d}", refresh_after_consolidation=True) for i in range(250)]
    engine = FakeEngine({"hal0:shared": models})
    engine.fail_patch = {"m007"}
    report = await apply_refresh_policy(engine, ["hal0:shared"], _policy(), check_write_gate=False)
    bank = report.banks[0]
    assert bank.models_seen == 250
    assert len(bank.models_patched) == 249
    assert [e["id"] for e in bank.model_errors] == ["m007"]
    assert "HTTP 400" in bank.model_errors[0]["error"]
    assert not report.ok
    # The bank config still went through.
    assert bank.config_updates and bank.config_error is None


@pytest.mark.asyncio
async def test_dry_run_writes_nothing() -> None:
    engine = FakeEngine({"hal0:shared": [_model("old", refresh_after_consolidation=True)]})
    report = await apply_refresh_policy(engine, ["hal0:shared"], _policy(), dry_run=True)
    assert engine.writes == []
    assert report.banks[0].models_patched == ["old"]
    assert report.banks[0].config_updates


@pytest.mark.asyncio
async def test_a_bank_the_engine_does_not_list_is_skipped_not_created() -> None:
    engine = FakeEngine({"hal0:shared": []})
    report = await apply_refresh_policy(
        engine, ["hal0:shared", "hal0:private"], _policy(), check_write_gate=False
    )
    assert [b.bank for b in report.banks] == ["hal0:shared", "hal0:private"]
    assert report.banks[1].skipped == "the engine has no such bank"
    assert all("hal0%3Aprivate" not in w[1] for w in engine.writes)


@pytest.mark.asyncio
async def test_an_unreachable_engine_is_reported_not_raised() -> None:
    async def down(*_a: Any, **_k: Any) -> Any:
        raise httpx.ConnectError("refused")

    report = await apply_refresh_policy(down, ["hal0:shared"], _policy(), check_write_gate=False)
    assert not report.ok
    assert report.banks[0].skipped and "ConnectError" in report.banks[0].skipped


@pytest.mark.asyncio
async def test_nothing_is_written_while_the_write_gate_is_set() -> None:
    write_gate.engage_gate()
    try:
        engine = FakeEngine({"hal0:shared": [_model("old", refresh_after_consolidation=True)]})
        report = await apply_refresh_policy(engine, ["hal0:shared"], _policy())
    finally:
        write_gate.lift_gate()
    assert report.deferred and "paused" in report.deferred
    assert engine.writes == [] and report.banks == []


# ── the hal0-api runner ──────────────────────────────────────────────────


class Clock:
    def __init__(self, at: datetime) -> None:
        self.t = at.timestamp()

    def __call__(self) -> float:
        return self.t

    def set(self, at: datetime) -> None:
        self.t = at.timestamp()


class Consolidations:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, method: str, path: str, **_k: Any) -> Any:
        if path.endswith("/consolidate"):
            self.calls.append(path)
            return {"operation_id": f"op-{len(self.calls)}"}
        if path == "/v1/default/banks":
            return {"banks": []}
        raise AssertionError(path)


def _runner(tmp_path: Path, policy: MemoryRefreshConfig, clock: Clock, engine: Any) -> Any:
    holder = {"policy": policy}
    runner = RefreshPolicyRunner(
        policy=lambda: holder["policy"],
        engine=lambda: engine,
        banks=lambda: ["hal0:shared", "hal0:private"],
        state_file=tmp_path / "sched.json",
        clock=clock,
    )
    return runner, holder


@pytest.mark.asyncio
async def test_scheduled_consolidation_runs_once_per_fire(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 15, 14, 0, tzinfo=UTC))
    engine = Consolidations()
    runner, _ = _runner(tmp_path, _policy(consolidation="scheduled"), clock, engine)
    await runner.step()  # starts the clock, runs nothing
    assert engine.calls == []
    assert json.loads((tmp_path / "sched.json").read_text())["last_run_at"] == clock.t
    clock.set(datetime(2026, 11, 16, 1, 59, tzinfo=UTC))
    await runner.step()
    assert engine.calls == []
    clock.set(datetime(2026, 11, 16, 2, 0, 30, tzinfo=UTC))
    await runner.step()
    assert engine.calls == [
        "/v1/default/banks/hal0%3Ashared/consolidate",
        "/v1/default/banks/hal0%3Aprivate/consolidate",
    ]
    clock.set(datetime(2026, 11, 16, 2, 5, tzinfo=UTC))
    await runner.step()
    assert len(engine.calls) == 2
    assert runner.last_consolidation["banks"]["hal0:shared"]["operation_id"] == "op-1"


@pytest.mark.asyncio
async def test_a_run_missed_while_down_runs_at_the_next_start(tmp_path: Path) -> None:
    (tmp_path / "sched.json").write_text(
        json.dumps({"last_run_at": datetime(2026, 11, 14, 2, 0, tzinfo=UTC).timestamp()})
    )
    clock = Clock(datetime(2026, 11, 16, 9, 0, tzinfo=UTC))
    engine = Consolidations()
    runner, _ = _runner(tmp_path, _policy(consolidation="scheduled"), clock, engine)
    await runner.step()
    assert len(engine.calls) == 2  # once, not once per missed day


@pytest.mark.asyncio
async def test_continuous_runs_nothing_and_switching_to_scheduled_starts_the_clock(
    tmp_path: Path,
) -> None:
    (tmp_path / "sched.json").write_text(json.dumps({"last_run_at": 0}))
    clock = Clock(datetime(2026, 11, 15, 14, 0, tzinfo=UTC))
    engine = Consolidations()
    runner, holder = _runner(tmp_path, _policy(), clock, engine)
    await runner.step()
    assert engine.calls == []
    holder["policy"] = _policy(consolidation="scheduled")
    await runner.step()
    # The old run time would make 02:00 today due; the switch restarts the clock instead.
    assert engine.calls == []
    clock.set(datetime(2026, 11, 16, 2, 1, tzinfo=UTC))
    await runner.step()
    assert len(engine.calls) == 2


@pytest.mark.asyncio
async def test_kick_runs_the_reconcile_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.memory import refresh_runner

    runs: list[Any] = []

    async def fake_apply(call: Any, banks: Any, policy: Any) -> Any:
        runs.append(policy)
        from hal0.memory.refresh_policy import PolicyReport

        return PolicyReport(dry_run=False, policy={}, trigger={})

    monkeypatch.setattr(refresh_runner, "apply_refresh_policy", fake_apply)
    clock = Clock(datetime(2026, 11, 15, 14, 0, tzinfo=UTC))
    runner, _ = _runner(tmp_path, _policy(), clock, Consolidations())
    await runner.step()
    await runner.step()
    assert len(runs) == 1  # not again until kicked or the period is up
    runner.kick()
    await runner.step()
    assert len(runs) == 2


@pytest.mark.asyncio
async def test_no_engine_means_no_reconcile_yet(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 15, 14, 0, tzinfo=UTC))
    runner = RefreshPolicyRunner(
        policy=lambda: _policy(),
        engine=lambda: None,
        banks=lambda: [],
        state_file=tmp_path / "s.json",
        clock=clock,
    )
    await runner.step()
    assert runner.last_report is None


def test_provider_helpers() -> None:
    assert provider_engine(None) is None
    assert provider_engine(object()) is None
    assert provider_banks(None) == []

    class P:
        platform_bank = "hal0:shared"
        private_bank = "hal0:private"
        two_bank = True
        hindsight_client = type("C", (), {"request_json": lambda *a, **k: None})()

    assert provider_banks(P()) == ["hal0:shared", "hal0:private"]
    assert provider_engine(P()) is not None


# ── the spec apply ───────────────────────────────────────────────────────


def test_the_spec_templates_get_the_policy_timing() -> None:
    from hal0.memory import spec_apply as sa

    policy = _policy(pages="after_consolidation", min_interval_s=7200)
    manifest = sa._with_policy(sa.load_template(None, "shared"), policy)
    assert manifest["bank"]["mental_model_min_refresh_interval_seconds"] == 7200
    default = manifest["bank"]["knowledge_page_default_trigger"]
    assert default["mode"] == "delta" and default["budget"] == "low"
    assert default["refresh_after_consolidation"] is True and default["refresh_cron"] is None
    assert manifest["mental_models"]
    for model in manifest["mental_models"]:
        assert {k: model["trigger"][k] for k in desired_trigger(policy)} == desired_trigger(policy)
        assert model["trigger"]["mode"] == "delta"


def test_the_shipped_seeds_match_the_default_policy() -> None:
    """The packaged templates say what a box gets with no [memory.refresh]."""
    from hal0.memory import spec_apply as sa

    wanted = desired_trigger(_policy())
    spec = sa.load_spec(None)
    for page in spec["knowledge"]["platform"]["pages"]:
        assert trigger_patch(page["trigger"], _policy()) is None, page["name"]
    for name in ("shared", "private", "coding-agent"):
        template = sa.load_template(None, name)
        for model in template.get("mental_models") or []:
            assert trigger_patch(model["trigger"], _policy()) is None, (name, model["id"])
        floor = (template.get("bank") or {}).get("mental_model_min_refresh_interval_seconds")
        assert floor in (None, wanted["min_refresh_interval_seconds"]), name


def test_stale_grace_is_one_schedule_interval_plus_the_margin() -> None:
    from hal0.memory.refresh_policy import STALE_MARGIN_S, stale_grace_s

    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    daily = MemoryRefreshConfig(pages="scheduled", schedule="0 3 * * *", min_interval_s=0)
    assert stale_grace_s(daily, now) == 86400.0 + STALE_MARGIN_S
    hourly = MemoryRefreshConfig(pages="scheduled", schedule="0 * * * *", min_interval_s=0)
    assert stale_grace_s(hourly, now) == 3600.0 + STALE_MARGIN_S
    # The floor holds a refresh back, so it stretches the wait when it is longer.
    floored = MemoryRefreshConfig(pages="scheduled", schedule="0 * * * *", min_interval_s=7200)
    assert stale_grace_s(floored, now) == 7200.0 + STALE_MARGIN_S


@pytest.mark.parametrize("mode", ["after_consolidation", "manual"])
def test_stale_grace_is_off_outside_a_schedule(mode: str) -> None:
    from hal0.memory.refresh_policy import stale_grace_s

    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    assert stale_grace_s(MemoryRefreshConfig(pages=mode), now) is None
