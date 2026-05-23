"""Tests for the FLM-native metric ingest hook on chat-completion responses.

PR-12 (plan §11 + memory ``hal0_lemonade_flm_npu_install``). The hook
lives in :func:`hal0.api.routes.v1._record_flm_native_metrics` and is
wired into ``_dispatch_and_forward`` for non-streaming responses. The
contract:

  * A response body carrying FLM native fields → shim records by
    (slot, model). The next /api/metrics/prometheus scrape includes them.
  * A non-FLM response (no FLM keys present) → no-op.
  * A missing shim on app.state → no-op (graceful early-boot path).
  * Unparseable body bytes → no-op (no metric collection should ever
    crash the user-visible chat path).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from hal0.api.routes.v1 import _record_flm_native_metrics
from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.metrics_shim import MetricsShim


class _FakeAppState:
    """Minimal stand-in for ``request.app.state`` — the hook only reads
    ``lemonade_metrics_shim``. A plain object lets the test inject a real
    or absent shim without spinning up the full FastAPI app."""

    lemonade_metrics_shim: MetricsShim | None


def _state(shim: MetricsShim | None) -> Any:
    s = _FakeAppState()
    s.lemonade_metrics_shim = shim
    return s


def test_flm_response_body_lands_in_shim() -> None:
    """A response carrying ``decoding_speed_tps`` + ``kv_token_occupancy_rate_percentage``
    is sniffed and recorded under (slot, model)."""
    shim = MetricsShim(LemonadeClient())
    body = json.dumps(
        {
            "id": "chat-xyz",
            "decoding_speed_tps": 40.7,
            "prefill_speed_tps": 320.1,
            "prefill_duration_ttft": 0.09,
            "kv_token_occupancy_rate_percentage": 12.5,
            "decoding_duration": 3.4,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}}],
        }
    ).encode()
    _record_flm_native_metrics(body, _state(shim), "agent", "gemma3:1b")
    flm = shim.snapshot()["flm"]
    assert "agent::gemma3:1b" in flm
    assert flm["agent::gemma3:1b"]["kv_token_occupancy_rate_percentage"] == pytest.approx(12.5)


def test_non_flm_response_body_is_noop() -> None:
    """A standard OpenAI chat-completion body (no FLM keys) records nothing."""
    shim = MetricsShim(LemonadeClient())
    body = json.dumps(
        {
            "id": "chat-abc",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
            "usage": {"completion_tokens": 5, "prompt_tokens": 10, "total_tokens": 15},
        }
    ).encode()
    _record_flm_native_metrics(body, _state(shim), "primary", "qwen3:4b")
    assert shim.snapshot()["flm"] == {}


def test_missing_shim_is_noop() -> None:
    """Lifespan-detached state (no shim attached) is fine — early-boot path."""
    body = json.dumps({"decoding_speed_tps": 1.0}).encode()
    # Must not raise even though there's no shim to record into.
    _record_flm_native_metrics(body, _state(None), "agent", "gemma3:1b")


def test_unparseable_body_is_noop() -> None:
    """Defensive: a non-JSON body (or a JSON list) is silently dropped so
    the user-visible chat response is unaffected by metric glitches."""
    shim = MetricsShim(LemonadeClient())
    _record_flm_native_metrics(b"not json", _state(shim), "agent", "g")
    _record_flm_native_metrics(b"[]", _state(shim), "agent", "g")
    _record_flm_native_metrics(b"", _state(shim), "agent", "g")
    assert shim.snapshot()["flm"] == {}


def test_missing_slot_or_model_is_noop() -> None:
    """The hook is called from the dispatcher; not every dispatch resolves
    to a slot+model (e.g. /v1/models passthrough). Empty strings → no-op."""
    shim = MetricsShim(LemonadeClient())
    body = json.dumps({"decoding_speed_tps": 1.0}).encode()
    _record_flm_native_metrics(body, _state(shim), "", "model")
    _record_flm_native_metrics(body, _state(shim), "slot", "")
    _record_flm_native_metrics(body, _state(shim), None, "model")
    _record_flm_native_metrics(body, _state(shim), "slot", None)
    assert shim.snapshot()["flm"] == {}
