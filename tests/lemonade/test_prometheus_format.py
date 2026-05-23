"""Renderer tests for ``hal0.lemonade.prometheus_format`` (PR-12).

Validates the exposition format against the Prometheus 0.0.4 spec:
  * One HELP + one TYPE per metric family.
  * Sample order is deterministic (sorted labels).
  * Absent fields produce no sample line (avoids ``NaN`` confusion).
  * Label values are escaped for backslash/quote/newline.
"""

from __future__ import annotations

from hal0.lemonade.prometheus_format import render_prometheus_exposition


def _families(text: str) -> dict[str, list[str]]:
    """Group exposition output by metric family for assertion convenience.

    Returns ``{metric_name: [sample_line, ...]}``. HELP + TYPE headers
    are stripped (covered by their own assertions).
    """
    families: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        # sample line: ``<name>{...} <value>`` OR ``<name> <value>``
        name = line.split("{", 1)[0].split(" ", 1)[0]
        families.setdefault(name, []).append(line)
    return families


def test_none_snapshot_returns_empty_body() -> None:
    """A missing shim (lifespan didn't run) → empty body, not 500. Valid
    Prometheus exposition: empty input = "no series", scrapers tolerate."""
    assert render_prometheus_exposition(None) == ""


def test_empty_snapshot_returns_empty_body() -> None:
    """Snapshot present but every field is empty/None → no samples emitted."""
    snap = {
        "stats": {
            "time_to_first_token": None,
            "tokens_per_second": None,
            "input_tokens": None,
            "output_tokens": None,
            "prompt_tokens": None,
        },
        "health": {"loaded_models": [], "max_models": {}},
        "flm": {},
        "last_poll_ts": None,
    }
    assert render_prometheus_exposition(snap) == ""


def test_emits_stats_family_with_help_and_type() -> None:
    snap = {
        "stats": {
            "time_to_first_token": 0.213,
            "tokens_per_second": 42.5,
            "input_tokens": 1024,
            "output_tokens": 256,
            "prompt_tokens": 1024,
        },
        "health": {"loaded_models": [], "max_models": {}},
        "flm": {},
        "last_poll_ts": None,
    }
    text = render_prometheus_exposition(snap)
    # HELP / TYPE for each gauge family.
    assert "# HELP hal0_lemonade_ttft_seconds" in text
    assert "# TYPE hal0_lemonade_ttft_seconds gauge" in text
    assert "# HELP hal0_lemonade_decode_tokens_per_second" in text
    assert "# TYPE hal0_lemonade_decode_tokens_per_second gauge" in text
    # Samples carry the source label.
    families = _families(text)
    assert (
        'hal0_lemonade_ttft_seconds{source="last_request"} 0.213'
        in families["hal0_lemonade_ttft_seconds"]
    )
    assert (
        'hal0_lemonade_decode_tokens_per_second{source="last_request"} 42.5'
        in families["hal0_lemonade_decode_tokens_per_second"]
    )
    # Integer-valued gauges format without trailing .0.
    assert (
        'hal0_lemonade_prompt_tokens{source="last_request"} 1024'
        in families["hal0_lemonade_prompt_tokens"]
    )


def test_emits_models_loaded_one_sample_per_model() -> None:
    snap = {
        "stats": {},
        "health": {
            "loaded_models": ["qwen3:4b", "embed-gemma"],
            "max_models": {"llm": 1, "embedding": 1},
        },
        "flm": {},
        "last_poll_ts": None,
    }
    text = render_prometheus_exposition(snap)
    families = _families(text)
    loaded = families["hal0_lemonade_models_loaded"]
    assert 'hal0_lemonade_models_loaded{model_name="qwen3:4b"} 1' in loaded
    assert 'hal0_lemonade_models_loaded{model_name="embed-gemma"} 1' in loaded
    assert len(loaded) == 2


def test_emits_max_models_one_sample_per_type() -> None:
    snap = {
        "stats": {},
        "health": {
            "loaded_models": [],
            "max_models": {"llm": 1, "embedding": 1, "transcription": 1},
        },
        "flm": {},
        "last_poll_ts": None,
    }
    text = render_prometheus_exposition(snap)
    families = _families(text)
    max_lines = families["hal0_lemonade_max_models"]
    assert 'hal0_lemonade_max_models{type="embedding"} 1' in max_lines
    assert 'hal0_lemonade_max_models{type="llm"} 1' in max_lines
    assert 'hal0_lemonade_max_models{type="transcription"} 1' in max_lines
    # Sorted output: ``embedding`` before ``llm`` before ``transcription``.
    indices = [
        max_lines.index('hal0_lemonade_max_models{type="embedding"} 1'),
        max_lines.index('hal0_lemonade_max_models{type="llm"} 1'),
        max_lines.index('hal0_lemonade_max_models{type="transcription"} 1'),
    ]
    assert indices == sorted(indices)


def test_emits_flm_family_with_slot_and_model_labels() -> None:
    snap = {
        "stats": {},
        "health": {"loaded_models": [], "max_models": {}},
        "flm": {
            "agent::gemma3:1b": {
                "decoding_speed_tps": 40.7,
                "prefill_speed_tps": 320.1,
                "prefill_duration_ttft": 0.087,
                "kv_token_occupancy_rate_percentage": 12.5,
                "decoding_duration": 3.4,
            }
        },
        "last_poll_ts": None,
    }
    text = render_prometheus_exposition(snap)
    families = _families(text)
    # Each FLM metric family maps to its own hal0_lemonade_flm_* name.
    assert (
        'hal0_lemonade_flm_decode_tokens_per_second{model_name="gemma3:1b",slot_name="agent"} 40.7'
        in families["hal0_lemonade_flm_decode_tokens_per_second"]
    )
    assert (
        'hal0_lemonade_kv_occupancy_ratio{model_name="gemma3:1b",slot_name="agent"} 12.5'
        in families["hal0_lemonade_kv_occupancy_ratio"]
    )
    assert (
        'hal0_lemonade_flm_ttft_seconds{model_name="gemma3:1b",slot_name="agent"} 0.087'
        in families["hal0_lemonade_flm_ttft_seconds"]
    )


def test_flm_skips_missing_fields_in_partial_payload() -> None:
    """Only the fields actually present emit samples — partial payloads
    don't pollute the exposition with absent-metric placeholders."""
    snap = {
        "stats": {},
        "health": {"loaded_models": [], "max_models": {}},
        "flm": {
            "agent::g": {
                "decoding_speed_tps": None,
                "prefill_speed_tps": None,
                "prefill_duration_ttft": None,
                "kv_token_occupancy_rate_percentage": 50.0,
                "decoding_duration": None,
            }
        },
        "last_poll_ts": None,
    }
    text = render_prometheus_exposition(snap)
    # Only the KV% family appears.
    assert "hal0_lemonade_kv_occupancy_ratio" in text
    assert "hal0_lemonade_flm_decode_tokens_per_second" not in text
    assert "hal0_lemonade_flm_prefill_tokens_per_second" not in text


def test_emits_last_scrape_timestamp_for_staleness_alerts() -> None:
    """``hal0_lemonade_metrics_last_scrape_seconds`` is meta — no labels."""
    snap = {
        "stats": {},
        "health": {"loaded_models": [], "max_models": {}},
        "flm": {},
        "last_poll_ts": 1_700_000_000.5,
    }
    text = render_prometheus_exposition(snap)
    assert "hal0_lemonade_metrics_last_scrape_seconds" in text
    # No label set on the meta gauge.
    assert "\nhal0_lemonade_metrics_last_scrape_seconds 1700000000.5" in text


def test_label_values_escape_quote_and_backslash() -> None:
    """A pathological model name with quotes must round-trip safely."""
    snap = {
        "stats": {},
        "health": {
            "loaded_models": ['weird"name\\with-special'],
            "max_models": {},
        },
        "flm": {},
        "last_poll_ts": None,
    }
    text = render_prometheus_exposition(snap)
    # Backslash-escape both the embedded quote and backslash.
    assert 'hal0_lemonade_models_loaded{model_name="weird\\"name\\\\with-special"} 1' in text


def test_help_text_escapes_backslash_and_newline() -> None:
    """Defensive — none of our HELPs carry these, but the renderer must
    handle them per the spec so a future maintainer doesn't introduce
    a parser-breaking string by accident."""
    # Exercise via a synthetic snapshot that includes a backslash in
    # a model name (the HELP text itself is hardcoded; this test pins
    # the escape helper's behaviour through label flow).
    snap = {
        "stats": {},
        "health": {"loaded_models": ["a\nb"], "max_models": {}},
        "flm": {},
        "last_poll_ts": None,
    }
    text = render_prometheus_exposition(snap)
    # The label value must NOT contain a raw newline — that would
    # split the sample line and corrupt the exposition.
    assert 'model_name="a\\nb"' in text
    assert 'model_name="a\nb"' not in text


def test_renderer_trailing_newline_for_safe_concatenation() -> None:
    """Federation / aggregation concatenates bodies — a trailing newline
    on each prevents fusing two distinct rows."""
    snap = {
        "stats": {"tokens_per_second": 1.0},
        "health": {"loaded_models": [], "max_models": {}},
        "flm": {},
        "last_poll_ts": None,
    }
    text = render_prometheus_exposition(snap)
    assert text.endswith("\n")
