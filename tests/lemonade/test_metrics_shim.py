"""Unit tests for ``hal0.lemonade.metrics_shim`` (PR-12, plan §11 + §10.1).

Contract:

  * One poll cycle refreshes both stats + health snapshots independently.
  * Lemonade unreachable / 5xx leaves the prior snapshot in place and
    does NOT advance the staleness timestamp.
  * Snapshot exposes the most recent observation (frozen between polls).
  * ``record_flm_metrics`` only stores when FLM-native fields are
    present in the payload; missing fields → no-op.
  * KV% is recorded ONLY via FLM ingest (plan §12.1: GPU/llamacpp slots
    get ``—`` in v0.2).
  * Per-(slot, model) FLM keys stay distinct.
  * Lifecycle: start/stop is idempotent and cancellation propagates.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.metrics_shim import (
    DEFAULT_POLL_INTERVAL_S,
    FlmMetrics,
    HealthSnapshot,
    MetricsShim,
    StatsSnapshot,
)


def _mock_transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


# ── construction / lifecycle ─────────────────────────────────────────


def test_default_poll_interval_is_documented() -> None:
    """The default cadence is published as a module constant so tests +
    the lifespan wiring agree on the value without duplicating it."""
    assert DEFAULT_POLL_INTERVAL_S == 5.0


def test_zero_poll_interval_rejected() -> None:
    """Negative/zero intervals are a programmer error, not a runtime risk."""
    client = LemonadeClient()
    with pytest.raises(ValueError, match="poll_interval_s must be > 0"):
        MetricsShim(client, poll_interval_s=0.0)


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    """Calling start twice doesn't spawn a second task."""

    def h(req: httpx.Request) -> httpx.Response:
        # Drive both endpoints so the tick doesn't log warnings.
        if req.url.path == "/v1/stats":
            return httpx.Response(200, json={})
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client, poll_interval_s=DEFAULT_POLL_INTERVAL_S)
        await shim.start()
        first_task = shim._task
        await shim.start()
        assert shim._task is first_task
        await shim.stop()


@pytest.mark.asyncio
async def test_stop_without_start_is_noop() -> None:
    """Defensive shutdown path — finally blocks call stop unconditionally."""
    shim = MetricsShim(LemonadeClient())
    await shim.stop()  # must not raise


# ── /v1/stats refresh ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_refreshes_stats_with_documented_schema() -> None:
    """Top-level v0.2 schema: time_to_first_token / tokens_per_second / ..."""
    payload = {
        "time_to_first_token": 0.213,
        "tokens_per_second": 42.5,
        "input_tokens": 1024,
        "output_tokens": 256,
        "prompt_tokens": 1024,
    }

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/stats":
            return httpx.Response(200, json=payload)
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client)
        await shim.tick()
        snap = shim.snapshot()
        assert snap["stats"]["time_to_first_token"] == pytest.approx(0.213)
        assert snap["stats"]["tokens_per_second"] == pytest.approx(42.5)
        assert snap["stats"]["input_tokens"] == 1024
        assert snap["stats"]["output_tokens"] == 256
        assert snap["stats"]["prompt_tokens"] == 1024


@pytest.mark.asyncio
async def test_tick_tolerates_nested_last_request_envelope() -> None:
    """An older Lemonade returned ``{"last_request": {...}}``; accept both."""

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/stats":
            return httpx.Response(
                200,
                json={
                    "last_request": {
                        "time_to_first_token": 0.4,
                        "tokens_per_second": 30.0,
                    }
                },
            )
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client)
        await shim.tick()
        snap = shim.snapshot()
        assert snap["stats"]["time_to_first_token"] == pytest.approx(0.4)
        assert snap["stats"]["tokens_per_second"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_tick_skips_stats_fields_that_are_absent() -> None:
    """Missing fields stay absent (None) rather than zero — clearer for dashboards."""

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/stats":
            return httpx.Response(200, json={"tokens_per_second": 10.0})
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client)
        await shim.tick()
        snap = shim.snapshot()
        assert snap["stats"]["tokens_per_second"] == pytest.approx(10.0)
        assert snap["stats"]["time_to_first_token"] is None
        assert snap["stats"]["input_tokens"] is None


# ── /v1/health refresh ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_refreshes_health_loaded_and_max_models() -> None:
    """all_models_loaded[] + max_models per type are both surfaced."""

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/stats":
            return httpx.Response(200, json={})
        if req.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={
                    "all_models_loaded": [
                        {"model_name": "qwen3:4b", "backend_url": "http://127.0.0.1:9101"},
                        {"model_name": "embed-gemma", "backend_url": "http://127.0.0.1:9102"},
                    ],
                    "max_models": {"llm": 1, "embedding": 1, "reranking": 1},
                },
            )
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client)
        await shim.tick()
        snap = shim.snapshot()
        assert snap["health"]["loaded_models"] == ["qwen3:4b", "embed-gemma"]
        assert snap["health"]["max_models"] == {"llm": 1, "embedding": 1, "reranking": 1}


@pytest.mark.asyncio
async def test_tick_accepts_legacy_loaded_field_name() -> None:
    """Older Lemonade builds called the list ``loaded`` (matches idle driver)."""

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/stats":
            return httpx.Response(200, json={})
        if req.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={"loaded": [{"model_name": "legacy-name"}]},
            )
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client)
        await shim.tick()
        snap = shim.snapshot()
        assert snap["health"]["loaded_models"] == ["legacy-name"]


@pytest.mark.asyncio
async def test_tick_handles_empty_loaded_list() -> None:
    """Empty pool → no per-model metrics, no exception."""

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/stats":
            return httpx.Response(200, json={})
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"all_models_loaded": [], "max_models": {}})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client)
        await shim.tick()
        snap = shim.snapshot()
        assert snap["health"]["loaded_models"] == []
        assert snap["health"]["max_models"] == {}


# ── unreachable lemond ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_swallows_unreachable_lemond() -> None:
    """Connection refusal does not raise — prior snapshot survives."""

    def h(_req: httpx.Request) -> httpx.Response:
        # MockTransport raises ConnectError when the handler raises
        raise httpx.ConnectError("simulated")

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client)
        await shim.tick()  # must not raise
        snap = shim.snapshot()
        assert snap["stats"]["tokens_per_second"] is None
        assert snap["health"]["loaded_models"] == []
        # last_poll_ts must NOT advance when neither endpoint succeeded
        # — that's how dashboards detect staleness.
        assert snap["last_poll_ts"] is None


@pytest.mark.asyncio
async def test_tick_swallows_5xx() -> None:
    """5xx leaves the snapshot untouched and doesn't crash the driver."""

    def h(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client)
        await shim.tick()
        snap = shim.snapshot()
        assert snap["last_poll_ts"] is None


# ── snapshot freezing / monotonic timestamp ──────────────────────────


@pytest.mark.asyncio
async def test_last_poll_ts_advances_only_on_success() -> None:
    """Timestamp tracks the most recent successful poll for staleness alerts."""

    call_count = {"n": 0}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/stats":
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(200, json={"tokens_per_second": 1.0})
            # Second call: simulate lemond crash mid-test.
            return httpx.Response(503, text="down")
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    clock_values = iter([100.0, 200.0, 300.0])
    clock = lambda: next(clock_values)  # noqa: E731

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client, clock=clock)
        await shim.tick()  # success → ts = 100
        first_ts = shim.snapshot()["last_poll_ts"]
        assert first_ts == 100.0
        await shim.tick()  # stats 5xx + health ok → still updates
        second_ts = shim.snapshot()["last_poll_ts"]
        assert second_ts == 200.0


# ── FLM ingest ───────────────────────────────────────────────────────


def test_record_flm_metrics_stores_when_fields_present() -> None:
    """All five documented fields end up in the snapshot, keyed by slot+model."""
    shim = MetricsShim(LemonadeClient())
    body = {
        "id": "chat-xyz",
        "decoding_speed_tps": 40.7,
        "prefill_speed_tps": 320.1,
        "prefill_duration_ttft": 0.087,
        "kv_token_occupancy_rate_percentage": 12.5,
        "decoding_duration": 3.4,
        "choices": [],  # ignored
    }
    assert shim.record_flm_metrics("agent", "gemma3:1b", body) is True
    snap = shim.snapshot()
    flm = snap["flm"]["agent::gemma3:1b"]
    assert flm["decoding_speed_tps"] == pytest.approx(40.7)
    assert flm["prefill_speed_tps"] == pytest.approx(320.1)
    assert flm["prefill_duration_ttft"] == pytest.approx(0.087)
    assert flm["kv_token_occupancy_rate_percentage"] == pytest.approx(12.5)
    assert flm["decoding_duration"] == pytest.approx(3.4)


def test_record_flm_metrics_partial_payload_is_stored() -> None:
    """A response missing some FLM fields still records the ones that are present."""
    shim = MetricsShim(LemonadeClient())
    body = {"kv_token_occupancy_rate_percentage": 99.0}
    assert shim.record_flm_metrics("npu-chat", "model", body) is True
    flm = shim.snapshot()["flm"]["npu-chat::model"]
    assert flm["kv_token_occupancy_rate_percentage"] == pytest.approx(99.0)
    # Missing fields stay None — the renderer skips them.
    assert flm["decoding_speed_tps"] is None
    assert flm["prefill_speed_tps"] is None


def test_record_flm_metrics_skips_non_flm_payloads() -> None:
    """A llamacpp chat-completion response has none of the FLM fields →
    no-op. This is the recipe discriminator — the hook is wired
    unconditionally on every completion."""
    shim = MetricsShim(LemonadeClient())
    body = {
        "id": "chat-abc",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"completion_tokens": 5, "prompt_tokens": 10},
    }
    assert shim.record_flm_metrics("primary", "qwen3:4b", body) is False
    assert shim.snapshot()["flm"] == {}


def test_record_flm_metrics_rejects_bad_inputs() -> None:
    """Non-string slot/model + non-dict payload → False, no exception."""
    shim = MetricsShim(LemonadeClient())
    assert shim.record_flm_metrics("", "model", {"decoding_speed_tps": 1.0}) is False
    assert shim.record_flm_metrics("slot", "", {"decoding_speed_tps": 1.0}) is False
    # type: ignore on the bad-input cases mirrors real-world misuse.
    assert shim.record_flm_metrics("slot", "model", "not a dict") is False  # type: ignore[arg-type]
    assert shim.record_flm_metrics("slot", "model", None) is False
    assert shim.snapshot()["flm"] == {}


def test_record_flm_metrics_keeps_slots_distinct() -> None:
    """Same model on two slots → two distinct entries; no overwrite."""
    shim = MetricsShim(LemonadeClient())
    shim.record_flm_metrics("agent", "gemma3:1b", {"decoding_speed_tps": 40.0})
    shim.record_flm_metrics("stt-npu", "gemma3:1b", {"decoding_speed_tps": 30.0})
    flm = shim.snapshot()["flm"]
    assert flm["agent::gemma3:1b"]["decoding_speed_tps"] == pytest.approx(40.0)
    assert flm["stt-npu::gemma3:1b"]["decoding_speed_tps"] == pytest.approx(30.0)


def test_record_flm_metrics_latest_observation_wins() -> None:
    """Repeated calls for the same (slot, model) overwrite — the snapshot
    is "most recent FLM response", not a rolling history."""
    shim = MetricsShim(LemonadeClient())
    shim.record_flm_metrics("agent", "g", {"decoding_speed_tps": 1.0})
    shim.record_flm_metrics("agent", "g", {"decoding_speed_tps": 2.0})
    assert shim.snapshot()["flm"]["agent::g"]["decoding_speed_tps"] == pytest.approx(2.0)


# ── KV% behaviour (plan §12.1) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_kv_occupancy_is_absent_for_llamacpp_slots() -> None:
    """Plan §12.1: no KV% for GPU/llamacpp slots in v0.2. The /v1/stats
    payload never carries the FLM field, so the snapshot has no FLM
    entry for a llamacpp-only flow."""

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/stats":
            return httpx.Response(
                200,
                json={"time_to_first_token": 0.2, "tokens_per_second": 25.0},
            )
        if req.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={"all_models_loaded": [{"model_name": "qwen3:4b"}], "max_models": {"llm": 1}},
            )
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        shim = MetricsShim(client)
        await shim.tick()
        snap = shim.snapshot()
        # /v1/stats produced TTFT + tok/s.
        assert snap["stats"]["time_to_first_token"] is not None
        assert snap["stats"]["tokens_per_second"] is not None
        # But NO FLM entry exists → no KV% in exposition.
        assert snap["flm"] == {}


# ── dataclass parsing helpers ────────────────────────────────────────


def test_stats_snapshot_rejects_non_dict_payload() -> None:
    """Defensive: list / string / None all yield an empty snapshot."""
    assert StatsSnapshot.from_payload(None) == StatsSnapshot()
    assert StatsSnapshot.from_payload([1, 2, 3]) == StatsSnapshot()
    assert StatsSnapshot.from_payload("oops") == StatsSnapshot()


def test_health_snapshot_skips_non_string_model_names() -> None:
    """An entry without a usable model_name is silently dropped."""
    payload = {
        "all_models_loaded": [
            {"model_name": "good"},
            {"model_name": 42},  # numeric — dropped
            {"backend_url": "http://x"},  # missing model_name — dropped
            "not-a-dict",  # not a dict — dropped
            {"model_name": ""},  # empty string — dropped
        ]
    }
    snap = HealthSnapshot.from_payload(payload)
    assert snap.loaded_models == ("good",)


def test_health_snapshot_filters_bad_max_models_values() -> None:
    """max_models with bool / string values is rejected per-entry."""
    payload = {
        "max_models": {"llm": 2, "embedding": True, "extra": "not-a-number", "image": 1},
    }
    snap = HealthSnapshot.from_payload(payload)
    assert snap.max_models == {"llm": 2, "image": 1}


def test_flm_metrics_returns_none_without_fields() -> None:
    """The discriminator: no FLM key → no entry."""
    assert FlmMetrics.from_payload({"id": "x", "choices": []}) is None
    assert FlmMetrics.from_payload({}) is None
    assert FlmMetrics.from_payload("not a dict") is None


# ── _run() loop cancellation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_loop_exits_cleanly_on_stop() -> None:
    """Cancellation must propagate through _run; stop() awaits the task."""

    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        # Short poll so the loop spends most of its time in the
        # ``wait_for(stopping.wait, ...)`` sleep — cancellation must
        # come out cleanly from there.
        shim = MetricsShim(client, poll_interval_s=0.01)
        await shim.start()
        await asyncio.sleep(0.05)  # let at least one tick land
        await shim.stop()
        assert shim._task is None
