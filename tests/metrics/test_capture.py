"""hal0.metrics.capture -- llama timings EXACT vs approx fallback parsing."""

from __future__ import annotations

from hal0.metrics.capture import (
    build_request_metric_row,
    extract_timings_fields,
    extract_usage_fields,
    parse_json_object,
    truncate_client,
)


class TestParseJsonObject:
    def test_valid_object(self) -> None:
        assert parse_json_object(b'{"a": 1}') == {"a": 1}

    def test_invalid_json_returns_none(self) -> None:
        assert parse_json_object(b"not json") is None

    def test_non_object_json_returns_none(self) -> None:
        assert parse_json_object(b"[1, 2, 3]") is None

    def test_empty_bytes_returns_none(self) -> None:
        assert parse_json_object(b"") is None


class TestTruncateClient:
    def test_truncates_to_24_chars(self) -> None:
        assert truncate_client("1" * 40) == "1" * 24

    def test_none_stays_none(self) -> None:
        assert truncate_client(None) is None

    def test_empty_string_stays_none(self) -> None:
        assert truncate_client("") is None


class TestExtractTimingsFields:
    def test_exact_llama_timings(self) -> None:
        payload = {
            "timings": {
                "prompt_per_second": 1800.0,
                "predicted_per_second": 48.5,
                "prompt_ms": 120.0,
                "cache_n": 32,
                "prompt_n": 100,
                "predicted_n": 50,
                "draft_n": 10,
                "draft_n_accepted": 6,
            }
        }
        out = extract_timings_fields(payload)
        assert out["prefill_tps"] == 1800.0
        assert out["decode_tps"] == 48.5
        assert out["ttft_ms"] == 120.0
        assert out["cache_hit"] == 1
        assert out["ctx_used"] == 150
        assert out["spec_accept_rate"] == 0.6
        assert out["tps_source"] == "exact"

    def test_missing_timings_returns_empty(self) -> None:
        assert extract_timings_fields({"usage": {}}) == {}

    def test_cache_n_zero_is_cache_miss(self) -> None:
        out = extract_timings_fields({"timings": {"cache_n": 0}})
        assert out["cache_hit"] == 0

    def test_no_draft_fields_omits_spec_accept_rate(self) -> None:
        out = extract_timings_fields({"timings": {"predicted_per_second": 10.0}})
        assert "spec_accept_rate" not in out


class TestExtractUsageFields:
    def test_openai_usage_and_finish_reason(self) -> None:
        payload = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "choices": [{"finish_reason": "stop"}],
        }
        out = extract_usage_fields(payload)
        assert out["prompt_tokens"] == 10
        assert out["completion_tokens"] == 20
        assert out["stop_reason"] == "stop"
        assert "decode_tps" not in out

    def test_flm_decoding_speed_is_exact(self) -> None:
        payload = {"usage": {"decoding_speed_tps": 33.3}}
        out = extract_usage_fields(payload)
        assert out["decode_tps"] == 33.3
        assert out["tps_source"] == "exact"

    def test_missing_usage_returns_empty(self) -> None:
        assert extract_usage_fields({}) == {}


class TestBuildRequestMetricRow:
    def test_prefers_exact_timings_over_approx(self) -> None:
        payload = {
            "usage": {"prompt_tokens": 5, "completion_tokens": 40},
            "timings": {"predicted_per_second": 55.0},
        }
        row = build_request_metric_row(
            ts="2026-01-01T00:00:00Z",
            request_id="r1",
            slot_id="primary",
            model_id="qwen3-4b",
            queue_ms=1.0,
            total_ms=900.0,
            ok=True,
            payload=payload,
            fallback_completion_tokens=40,
            fallback_elapsed_s=2.0,
        )
        assert row["decode_tps"] == 55.0
        assert row["tps_source"] == "exact"
        assert row["completion_tokens"] == 40

    def test_falls_back_to_wall_clock_approx_when_no_exact_source(self) -> None:
        row = build_request_metric_row(
            ts="2026-01-01T00:00:00Z",
            request_id="r1",
            slot_id="primary",
            model_id=None,
            queue_ms=None,
            total_ms=None,
            ok=True,
            payload=None,
            fallback_completion_tokens=40,
            fallback_elapsed_s=2.0,
        )
        assert row["decode_tps"] == 20.0
        assert row["tps_source"] == "approx"
        assert row["completion_tokens"] == 40

    def test_error_row_has_ok_zero_and_error_code(self) -> None:
        row = build_request_metric_row(
            ts="2026-01-01T00:00:00Z",
            request_id="r1",
            slot_id=None,
            model_id=None,
            queue_ms=None,
            total_ms=5.0,
            ok=False,
            error_code="dispatcher.no_route",
        )
        assert row["ok"] == 0
        assert row["error_code"] == "dispatcher.no_route"
        assert row["decode_tps"] is None

    def test_comfyui_style_no_token_metric_stays_none(self) -> None:
        """Upstreams with no token-throughput concept (image gen) get None,
        never a synthesized rate."""
        row = build_request_metric_row(
            ts="2026-01-01T00:00:00Z",
            request_id="r1",
            slot_id="img",
            model_id="sdxl",
            queue_ms=1.0,
            total_ms=4000.0,
            ok=True,
            payload={"ok": True},
        )
        assert row["decode_tps"] is None
        assert row["tps_source"] is None
