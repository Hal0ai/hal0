"""#2353: warn when the extraction slot cannot finish one extraction in time.

The context-window floor (#1903) says whether the extraction prompt fits the
slot at all. It cannot see the #1834 shape: a CPU-only slot at ~12 prompt
tok/s that fits the prompt fine but needs ~270 s to prefill it, so every
attempt against the 300 s ``[memory.graph].llm_timeout_s`` times out and the
operator learns about it from an empty bank.

These tests drive :func:`measure_extraction_throughput` against a fake slot
(an ``httpx.MockTransport`` playing llama-server) at a slow and a fast rate.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hal0.memory.extraction_env import (
    EXTRACTION_PROMPT_TOKENS,
    PROBE_DECODE_TOKENS,
    PROBE_PROMPT_TOKENS,
    PROBE_TIMEOUT_S,
    ExtractionThroughput,
    measure_extraction_throughput,
    parse_throughput_metrics,
)


class FakeSlot:
    """A llama-server stand-in: ``/metrics`` plus a timed ``/v1/completions``."""

    def __init__(
        self,
        *,
        prompt_tps: float,
        decode_tps: float,
        metrics: str | None = None,
        timeout: bool = False,
        status: int = 200,
        timings: bool = True,
    ) -> None:
        self.prompt_tps = prompt_tps
        self.decode_tps = decode_tps
        self.metrics = metrics
        self.timeout = timeout
        self.status = status
        self.timings = timings
        self.requests: list[httpx.Request] = []
        self.probe_bodies: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/metrics":
            if self.metrics is None:
                return httpx.Response(501, text="metrics disabled")
            return httpx.Response(200, text=self.metrics)
        if request.url.path == "/v1/completions":
            self.probe_bodies.append(json.loads(request.content))
            if self.timeout:
                raise httpx.ReadTimeout("probe timed out", request=request)
            if self.status != 200:
                return httpx.Response(self.status, json={"error": "no model"})
            body: dict[str, Any] = {
                "choices": [{"text": "x" * PROBE_DECODE_TOKENS}],
                "usage": {"prompt_tokens": 450, "completion_tokens": PROBE_DECODE_TOKENS},
            }
            if self.timings:
                body["timings"] = {
                    "prompt_n": 450,
                    "prompt_per_second": self.prompt_tps,
                    "predicted_n": PROBE_DECODE_TOKENS,
                    "predicted_per_second": self.decode_tps,
                }
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


# The #1834 box: ~12 prompt tok/s on CPU, a few decode tok/s.
SLOW = {"prompt_tps": 12.0, "decode_tps": 4.0}
# A GPU slot: prefill in the hundreds, decode in the tens.
FAST = {"prompt_tps": 800.0, "decode_tps": 45.0}


async def _measure(slot: FakeSlot, **kw: Any) -> ExtractionThroughput:
    kw.setdefault("timeout_s", 300)
    kw.setdefault("max_tokens", 4096)
    return await measure_extraction_throughput("utility", 8081, transport=slot.transport, **kw)


async def test_slow_slot_is_flagged_too_slow_with_the_measured_rates() -> None:
    est = await _measure(FakeSlot(**SLOW))

    assert est.source == "probe"
    assert est.verdict == "too_slow"
    # ~3500 / 12 + 4096 / 4 — the formula on the issue.
    assert est.projected_s == pytest.approx(EXTRACTION_PROMPT_TOKENS / 12.0 + 4096 / 4.0)
    msg = est.message()
    assert "12.0 prompt tok/s" in msg
    assert "4.0 decode tok/s" in msg
    assert "llm_timeout_s = 300 s" in msg
    assert "'utility'" in msg


async def test_fast_slot_passes() -> None:
    est = await _measure(FakeSlot(**FAST))

    assert est.verdict == "ok"
    assert est.projected_s is not None and est.projected_s < 300
    assert "inside [memory.graph].llm_timeout_s = 300 s" in est.message()


async def test_prefill_alone_over_the_timeout_is_too_slow_even_with_a_small_completion_cap() -> (
    None
):
    """The #1834 numbers: prefill ~290 s already, so any decode tips it over."""
    est = await _measure(FakeSlot(prompt_tps=12.0, decode_tps=40.0), max_tokens=512)

    assert est.verdict == "too_slow"


async def test_metrics_reading_skips_the_probe() -> None:
    metrics = (
        "# HELP llamacpp:prompt_tokens_seconds Average prompt throughput in tokens/s.\n"
        "llamacpp:prompt_tokens_seconds 650.5\n"
        "llamacpp:predicted_tokens_seconds 38.25\n"
        "llamacpp:requests_processing 0\n"
    )
    slot = FakeSlot(**SLOW, metrics=metrics)

    est = await _measure(slot)

    assert est.source == "metrics"
    assert (est.prompt_tps, est.decode_tps) == (650.5, 38.25)
    assert slot.probe_bodies == []


async def test_zero_metrics_fall_back_to_one_bounded_probe() -> None:
    metrics = "llamacpp:prompt_tokens_seconds 0\nllamacpp:predicted_tokens_seconds 0\n"
    slot = FakeSlot(**SLOW, metrics=metrics)

    est = await _measure(slot)

    assert est.source == "probe"
    assert len(slot.probe_bodies) == 1
    body = slot.probe_bodies[0]
    assert body["max_tokens"] <= PROBE_DECODE_TOKENS
    # Under the 512-token prompt cap at English's ~4 characters a token.
    assert len(body["prompt"]) // 4 <= PROBE_PROMPT_TOKENS
    # A prompt cached by an earlier probe would read as a fast slot.
    assert body["cache_prompt"] is False


async def test_probe_timeout_reports_the_upper_bound_rate() -> None:
    est = await _measure(FakeSlot(**SLOW, timeout=True))

    assert est.probe_timed_out is True
    assert est.prompt_tps == pytest.approx(PROBE_PROMPT_TOKENS / PROBE_TIMEOUT_S)
    # 3500 / 17.1 ≈ 205 s: under a 300 s timeout that is not proof either way.
    assert est.verdict == "unknown"
    assert "did not finish" in est.message()


async def test_probe_timeout_is_too_slow_when_prefill_alone_overruns() -> None:
    est = await _measure(FakeSlot(**SLOW, timeout=True), timeout_s=120)

    assert est.verdict == "too_slow"
    assert "llm_timeout_s = 120 s" in est.message()


async def test_model_less_slot_is_unknown_not_a_failure() -> None:
    est = await _measure(FakeSlot(**FAST, status=404))

    assert est.verdict == "unknown"
    assert est.projected_s is None
    assert "HTTP 404" in est.message()


async def test_unreachable_slot_is_unknown() -> None:
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    est = await measure_extraction_throughput(
        "utility", 8081, timeout_s=300, max_tokens=4096, transport=httpx.MockTransport(_refuse)
    )

    assert est.verdict == "unknown"
    assert "ConnectError" in est.detail


async def test_portless_slot_never_issues_a_request() -> None:
    slot = FakeSlot(**FAST)

    est = await measure_extraction_throughput("utility", 0, transport=slot.transport)

    assert est.verdict == "unknown"
    assert slot.requests == []


async def test_wall_clock_fallback_without_timings() -> None:
    ticks = iter([100.0, 110.0])
    slot = FakeSlot(**FAST, timings=False)

    est = await _measure(slot, clock=lambda: next(ticks))

    # 450 prompt + 32 completion tokens over 10 s, each charged the whole span.
    assert est.prompt_tps == pytest.approx(45.0)
    assert est.decode_tps == pytest.approx(3.2)


def test_reproject_keeps_the_measurement() -> None:
    est = ExtractionThroughput(
        slot="utility", timeout_s=300, max_tokens=4096, prompt_tps=100.0, decode_tps=20.0
    )
    assert est.verdict == "ok"  # 35 + 204.8

    tighter = est.reproject(timeout_s=200, max_tokens=4096)

    assert tighter.verdict == "too_slow"
    assert (tighter.prompt_tps, tighter.decode_tps) == (100.0, 20.0)


def test_as_dict_carries_verdict_and_message() -> None:
    est = ExtractionThroughput(
        slot="utility", timeout_s=300, max_tokens=4096, prompt_tps=12.0, decode_tps=4.0
    )
    out = est.as_dict()
    assert out["verdict"] == "too_slow"
    assert out["projected_s"] == pytest.approx(round(3500 / 12 + 4096 / 4, 1))
    assert out["message"] == est.message()


def test_parse_throughput_metrics_ignores_missing_and_zero() -> None:
    assert parse_throughput_metrics("") == (None, None)
    assert parse_throughput_metrics("llamacpp:prompt_tokens_seconds 0\n") == (None, None)
    assert parse_throughput_metrics(
        "llamacpp:prompt_tokens_seconds 12.5\nllamacpp:predicted_tokens_seconds nan-ish\n"
    ) == (12.5, None)
