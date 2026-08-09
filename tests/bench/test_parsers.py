"""test_parsers.py — the two P2 engine-output parsers against REAL captured
fixtures (DESIGN §13 P2: "both parsers get unit tests using the captured real
JSON as fixtures").

The fixtures in tests/fixtures/ are verbatim output from THIS box's llama-bench
(hal0-benchctl sweep) and server_ab.py, so these tests lock the parsers to the
real shapes, not to a guessed schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.bench.parsers import (
    llama_bench_row_kind,
    parse_llama_bench,
    parse_server_ab,
    parse_telemetry_samples,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


# --------------------------------------------------------------------------- #
# Tier A — llama-bench
# --------------------------------------------------------------------------- #


@pytest.fixture
def lb_rows():
    return _load("llama_bench_0.8b_rocm.json")


@pytest.fixture
def lb_meta():
    return _load("llama_bench_0.8b_rocm.meta.json")


def test_row_kind_classifies_pp_and_tg(lb_rows):
    kinds = [llama_bench_row_kind(r) for r in lb_rows]
    assert "pp" in kinds and "tg" in kinds  # the fixture has one of each


def test_parse_llama_bench_pp(lb_rows, lb_meta):
    p = parse_llama_bench(lb_rows, lb_meta, "pp")
    # the pp row has 2 samples -> 2 reps, each carrying prefill t/s (not decode)
    assert len(p.reps) == 2
    assert all(r.prefill_ts is not None and r.decode_ts is None for r in p.reps)
    # summary carries prefill median, and NOT a decode number (pp measures prefill)
    assert p.summary.prefill_ts_med is not None
    assert p.summary.decode_ts_med is None
    # per-rep raw t/s survive (schema v2's whole point)
    assert p.reps[0].prefill_ts == lb_rows[0]["samples_ts"][0]
    # observed engine provenance for display
    assert p.engine_observed.kind == "llama-bench"
    assert p.engine_observed.llamacpp_build.startswith("b")
    assert "rocm" in p.engine_observed.image


def test_parse_llama_bench_tg(lb_rows, lb_meta):
    p = parse_llama_bench(lb_rows, lb_meta, "tg")
    assert len(p.reps) == 2
    assert all(r.decode_ts is not None and r.prefill_ts is None for r in p.reps)
    assert p.summary.decode_ts_med is not None
    assert p.summary.prefill_ts_med is None  # tg measures decode, not prefill
    # median matches statistics.median of the two samples
    a, b = lb_rows[1]["samples_ts"]
    assert p.summary.decode_ts_med == round((a + b) / 2, 4)


def test_parse_llama_bench_resolves_argv(lb_rows, lb_meta):
    # the drawer's "resolved argv" comes from the engine row, not the (often
    # empty) plan-time config — reconstruct it from the actual run parameters
    p = parse_llama_bench(lb_rows, lb_meta, "tg")
    cfg = p.config_observed
    assert cfg is not None and cfg.argv
    assert "-ngl" in cfg.argv and "-fa" in cfg.argv  # real resolved flags
    assert cfg.kv.get("main_k") == lb_rows[0]["type_k"]  # kv from the row
    assert cfg.env == {}  # env is not reported by llama-bench — never invented


def test_parse_llama_bench_single_rep_falls_back_to_avg():
    # a -r 1 sweep may omit samples_ts; fall back to avg_ts as the single rep
    rows = [{"n_prompt": 0, "n_gen": 64, "avg_ts": 99.5, "build_number": 1, "build_commit": "abc"}]
    p = parse_llama_bench(rows, {}, "tg")
    assert len(p.reps) == 1
    assert p.reps[0].decode_ts == 99.5
    assert p.summary.decode_ts_med == 99.5


def test_parse_llama_bench_missing_kind_is_empty(lb_rows, lb_meta):
    # a pp-only sweep asked for its tg row -> no reps, never a fabricated number
    pp_only = [r for r in lb_rows if llama_bench_row_kind(r) == "pp"]
    p = parse_llama_bench(pp_only, lb_meta, "tg")
    assert p.reps == []
    assert p.summary.decode_ts_med is None


# --------------------------------------------------------------------------- #
# Tier B — server_ab
# --------------------------------------------------------------------------- #


def test_parse_server_ab_ab():
    doc = _load("server_ab_ab.json")
    p = parse_server_ab(doc, "chat")
    # first variant has 5 timed runs -> 5 reps with decode+prefill+accept
    assert len(p.reps) == 5
    r0 = p.reps[0]
    assert r0.decode_ts is not None and r0.prefill_ts is not None
    assert r0.drafted and r0.accepted is not None
    assert 0.0 <= r0.accept_rate <= 1.0
    assert p.summary.decode_ts_med is not None
    assert p.summary.accept_med is not None
    assert p.engine_observed.kind == "llama-server"


def test_parse_server_ab_reuse():
    doc = _load("server_ab_reuse.json")
    p = parse_server_ab(doc, "reuse")
    # reuse carries a single warmed second_call per variant -> 1 rep
    assert len(p.reps) == 1
    assert p.reps[0].decode_ts is not None
    assert p.summary.decode_ts_med is not None


def test_parse_server_ab_embed():
    doc = _load("server_ab_embed.json")
    p = parse_server_ab(doc, "embed")
    # embed reports latency_s only -> reps carry t_s, no decode t/s to report
    assert len(p.reps) == len(doc["results"]["latency_s"])
    assert all(r.t_s is not None and r.decode_ts is None for r in p.reps)
    assert p.summary.decode_ts_med is None  # never guessed for embed


def test_parse_server_ab_drafts_without_accepted_does_not_crash():
    # A speculative run can report a draft count (`draft_n` > 0) without a paired
    # `draft_n_accepted` (partial/older llama.cpp timings, or a null field). The
    # acceptance ratio is then unknowable — it must stay null, NOT crash the whole
    # cell parse with `None / draft_n` (regression: TypeError killed the session).
    doc = {
        "mode": "ab",
        "results": {
            "spec": {
                "extra_args": "",
                "runs": [
                    {
                        "predicted_per_second": 40.0,
                        "prompt_per_second": 300.0,
                        "draft_n": 12,
                        "draft_n_accepted": None,
                        "wall_s": 2.0,
                    }
                ],
            }
        },
    }
    p = parse_server_ab(doc, "chat")
    assert len(p.reps) == 1  # the run is still a valid throughput measurement
    assert p.reps[0].decode_ts == 40.0
    assert p.reps[0].accept_rate is None  # unknowable, never invented, never crash
    assert p.reps[0].drafted == 12


# --------------------------------------------------------------------------- #
# Telemetry (Phase 4) — hal0-benchctl's 1 Hz sampler JSONL rows
# --------------------------------------------------------------------------- #


def _sample(ts="2026-08-09T00:00:00Z", **kw):
    row = {
        "ts": ts,
        "temp_c": None,
        "power_mw": None,
        "gpu_busy_pct": None,
        "vram_b": None,
        "gtt_b": None,
        "sclk_mhz": None,
    }
    row.update(kw)
    return row


def test_telemetry_empty_samples_is_all_none():
    t = parse_telemetry_samples([])
    assert t.vram_peak_mb is None
    assert t.gtt_peak_mb is None
    assert t.gpu_edge_temp_max_c is None
    assert t.gpu_power_avg_w is None
    assert t.throttled is None


def test_telemetry_unit_conversions():
    samples = [
        _sample(
            vram_b=2 * 1024 * 1024 * 1024,
            gtt_b=512 * 1024 * 1024,
            temp_c=45000,
            power_mw=120000,
        ),
        _sample(
            vram_b=3 * 1024 * 1024 * 1024,
            gtt_b=256 * 1024 * 1024,
            temp_c=52000,
            power_mw=140000,
        ),
    ]
    t = parse_telemetry_samples(samples)
    assert t.vram_peak_mb == 3072  # bytes -> MB, peak (max) over samples
    assert t.gtt_peak_mb == 512  # peak, not the last sample
    assert t.gpu_edge_temp_max_c == 52  # millidegrees -> degrees C, max
    assert t.gpu_power_avg_w == 130  # mW -> W, MEAN not max: (120000+140000)/2/1000


def test_telemetry_missing_counter_stays_null_independently():
    # gtt_b is unreadable on every sample (debugfs locked down) but vram_b
    # isn't — one dead counter must not blank the others (§14.3).
    samples = [
        _sample(vram_b=1024 * 1024, gtt_b=None),
        _sample(vram_b=2 * 1024 * 1024, gtt_b=None),
    ]
    t = parse_telemetry_samples(samples)
    assert t.vram_peak_mb == 2
    assert t.gtt_peak_mb is None


def test_telemetry_throttle_flags_three_consecutive_drops_below_ten_percent():
    # Peak sclk 2000 MHz; threshold is 1800 MHz. Three consecutive samples
    # below that trips throttled=True.
    samples = [
        _sample(sclk_mhz=2000),
        _sample(sclk_mhz=1700),
        _sample(sclk_mhz=1600),
        _sample(sclk_mhz=1650),
    ]
    t = parse_telemetry_samples(samples)
    assert t.throttled is True


def test_telemetry_throttle_false_when_drop_never_reaches_three_consecutive():
    samples = [
        _sample(sclk_mhz=2000),
        _sample(sclk_mhz=1700),  # one drop...
        _sample(sclk_mhz=2000),  # ...then recovers, streak resets
        _sample(sclk_mhz=1700),
    ]
    t = parse_telemetry_samples(samples)
    assert t.throttled is False


def test_telemetry_throttle_false_within_ten_percent_band():
    # 2000 -> 1900 is exactly a 5% drop, inside the >10% threshold.
    samples = [_sample(sclk_mhz=2000), _sample(sclk_mhz=1900), _sample(sclk_mhz=1900)]
    t = parse_telemetry_samples(samples)
    assert t.throttled is False


def test_telemetry_throttle_none_when_sclk_never_sampled():
    samples = [_sample(vram_b=1024), _sample(vram_b=2048)]  # no sclk_mhz at all
    t = parse_telemetry_samples(samples)
    assert t.throttled is None
