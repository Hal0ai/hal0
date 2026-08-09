"""test_guidellm.py — GuideLLM adapter (bench Phase 3, Track G).

Argv-shape tests against the VERIFIED 0.7.3 CLI shape (module docstring of
``hal0.bench.adapters.guidellm``); ``run_guidellm`` tests inject a fake
runner (same injectable pattern as ``harness.run_cell`` / ``runner.py``'s
tests) — nothing here shells out to a real ``guidellm``; parser tests run
against the fixtures captured by ``capture_guidellm.py`` (a REAL guidellm run
against a stdlib http.server, see that script's docstring) plus hand-built
edge cases for shapes a healthy capture can't itself produce.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.bench.adapters.guidellm import (
    GuidellmRequest,
    build_argv,
    parse_benchmarks,
    run_guidellm,
)
from hal0.bench.schema import Outcome

FIXTURES = Path(__file__).parent / "fixtures" / "guidellm"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# --------------------------------------------------------------------------- #
# build_argv — CLI shape
# --------------------------------------------------------------------------- #


class TestBuildArgv:
    def test_constant_profile_shape(self):
        req = GuidellmRequest(
            endpoint="http://127.0.0.1:8080",
            model="my-model",
            profile_kind="constant",
            profile_options={"rate": 2},
            output_path="/tmp/out/benchmarks.json",
            max_requests=50,
        )
        argv = build_argv(req)
        assert argv[:2] == ["guidellm", "run"]
        assert "--backend" in argv
        backend = argv[argv.index("--backend") + 1]
        assert backend == "kind=openai_http,target=http://127.0.0.1:8080,model=my-model"
        profile = argv[argv.index("--profile") + 1]
        assert profile == "kind=constant,rate=2"
        data = argv[argv.index("--data") + 1]
        assert data == "kind=synthetic_text,prompt_tokens=512,output_tokens=128"
        tokenizer = argv[argv.index("--tokenizer") + 1]
        assert tokenizer == "kind=huggingface_auto,model=my-model"  # defaults to model id
        constraint = argv[argv.index("--constraint") + 1]
        assert constraint == "kind=max_requests,count=50"
        out = argv[argv.index("--output") + 1]
        assert out == "kind=json,path=/tmp/out/benchmarks.json"
        assert argv[-1] == "--disable-console"

    def test_synchronous_profile_has_no_options(self):
        req = GuidellmRequest(
            endpoint="http://x",
            model="m",
            profile_kind="synchronous",
            output_path="/tmp/o.json",
            max_requests=1,
        )
        argv = build_argv(req)
        assert argv[argv.index("--profile") + 1] == "kind=synchronous"

    def test_concurrent_streams_list_joins_with_commas(self):
        req = GuidellmRequest(
            endpoint="http://x",
            model="m",
            profile_kind="concurrent",
            profile_options={"streams": [1, 2, 4, 8]},
            output_path="/tmp/o.json",
            max_requests=1,
        )
        argv = build_argv(req)
        assert argv[argv.index("--profile") + 1] == "kind=concurrent,streams=1,2,4,8"

    def test_sweep_profile_options(self):
        req = GuidellmRequest(
            endpoint="http://x",
            model="m",
            profile_kind="sweep",
            profile_options={"sweep_size": 5, "strategy_type": "constant", "max_concurrency": 64},
            output_path="/tmp/o.json",
            max_requests=1,
        )
        argv = build_argv(req)
        assert (
            argv[argv.index("--profile") + 1]
            == "kind=sweep,sweep_size=5,strategy_type=constant,max_concurrency=64"
        )

    def test_throughput_profile_options(self):
        req = GuidellmRequest(
            endpoint="http://x",
            model="m",
            profile_kind="throughput",
            profile_options={"max_concurrency": 16},
            output_path="/tmp/o.json",
            max_seconds=30,
        )
        argv = build_argv(req)
        assert argv[argv.index("--profile") + 1] == "kind=throughput,max_concurrency=16"
        assert argv[argv.index("--constraint") + 1] == "kind=max_duration,seconds=30"

    def test_both_constraints_ride_two_flags(self):
        req = GuidellmRequest(
            endpoint="http://x",
            model="m",
            profile_kind="synchronous",
            output_path="/tmp/o.json",
            max_requests=10,
            max_seconds=60,
        )
        argv = build_argv(req)
        constraint_values = [argv[i + 1] for i, a in enumerate(argv) if a == "--constraint"]
        assert constraint_values == ["kind=max_requests,count=10", "kind=max_duration,seconds=60"]

    def test_explicit_tokenizer_overrides_model_default(self):
        req = GuidellmRequest(
            endpoint="http://x",
            model="local-gguf-slot",
            tokenizer="meta-llama/Llama-3.1-8B",
            profile_kind="synchronous",
            output_path="/tmp/o.json",
            max_requests=1,
        )
        argv = build_argv(req)
        assert (
            argv[argv.index("--tokenizer") + 1]
            == "kind=huggingface_auto,model=meta-llama/Llama-3.1-8B"
        )

    def test_rejects_unknown_profile_kind(self):
        req = GuidellmRequest(
            endpoint="http://x",
            model="m",
            profile_kind="poisson",
            output_path="/tmp/o.json",
            max_requests=1,
        )
        with pytest.raises(ValueError, match="unknown guidellm profile kind"):
            build_argv(req)

    def test_requires_output_path(self):
        req = GuidellmRequest(
            endpoint="http://x",
            model="m",
            profile_kind="synchronous",
            output_path="",
            max_requests=1,
        )
        with pytest.raises(ValueError, match="output_path"):
            build_argv(req)

    def test_requires_at_least_one_constraint(self):
        req = GuidellmRequest(
            endpoint="http://x", model="m", profile_kind="synchronous", output_path="/tmp/o.json"
        )
        with pytest.raises(ValueError, match="max_requests"):
            build_argv(req)

    def test_prompt_and_output_tokens_ride_the_data_flag(self):
        req = GuidellmRequest(
            endpoint="http://x",
            model="m",
            profile_kind="synchronous",
            output_path="/tmp/o.json",
            max_requests=1,
            prompt_tokens=2048,
            output_tokens=256,
        )
        argv = build_argv(req)
        assert (
            argv[argv.index("--data") + 1]
            == "kind=synthetic_text,prompt_tokens=2048,output_tokens=256"
        )


# --------------------------------------------------------------------------- #
# run_guidellm — injectable runner, outcome classification
# --------------------------------------------------------------------------- #


def _base_request(output_path: str) -> GuidellmRequest:
    return GuidellmRequest(
        endpoint="http://127.0.0.1:8080",
        model="m",
        profile_kind="synchronous",
        output_path=output_path,
        max_requests=1,
    )


class TestRunGuidellm:
    def test_ok_run_reads_the_output_doc(self, tmp_path):
        out = tmp_path / "benchmarks.json"
        out.write_text(json.dumps({"benchmarks": []}))
        calls = []

        def fake_runner(argv, timeout_s):
            calls.append((argv, timeout_s))
            return 0, "ok", ""

        result = run_guidellm(_base_request(str(out)), runner=fake_runner, timeout_s=30)
        assert result.outcome is Outcome.OK
        assert result.doc == {"benchmarks": []}
        assert result.rc == 0
        assert len(calls) == 1
        assert calls[0][1] == 30

    def test_nonzero_rc_is_failed(self, tmp_path):
        out = tmp_path / "benchmarks.json"

        def fake_runner(argv, timeout_s):
            return 1, "", "backend validation failed"

        result = run_guidellm(_base_request(str(out)), runner=fake_runner)
        assert result.outcome is Outcome.FAILED
        assert result.doc is None
        assert "backend validation failed" in result.tail

    def test_watchdog_sentinel_is_hang(self, tmp_path):
        out = tmp_path / "benchmarks.json"

        def fake_runner(argv, timeout_s):
            return -9, "", "[watchdog] killed after timeout"

        result = run_guidellm(_base_request(str(out)), runner=fake_runner)
        assert result.outcome is Outcome.HANG

    def test_oom_string_in_tail_is_oom(self, tmp_path):
        out = tmp_path / "benchmarks.json"

        def fake_runner(argv, timeout_s):
            return 1, "", "CUDA error: out of memory"

        result = run_guidellm(_base_request(str(out)), runner=fake_runner)
        assert result.outcome is Outcome.OOM

    def test_ok_rc_but_missing_output_file_is_failed(self, tmp_path):
        out = tmp_path / "never-written.json"  # runner claims success, no file appears

        def fake_runner(argv, timeout_s):
            return 0, "", ""

        result = run_guidellm(_base_request(str(out)), runner=fake_runner)
        assert result.outcome is Outcome.FAILED
        assert result.doc is None
        assert "no valid JSON" in result.tail

    def test_ok_rc_but_malformed_json_is_failed(self, tmp_path):
        out = tmp_path / "benchmarks.json"
        out.write_text("{not valid json")

        def fake_runner(argv, timeout_s):
            return 0, "", ""

        result = run_guidellm(_base_request(str(out)), runner=fake_runner)
        assert result.outcome is Outcome.FAILED
        assert result.doc is None

    def test_no_runner_injected_uses_default_subprocess(self, tmp_path):
        """Smoke-tests `_default_runner` itself (production path) with a
        plain, real, non-guidellm command — never the actual tool."""
        out = tmp_path / "benchmarks.json"
        out.write_text(json.dumps({"ok": True}))
        req = _base_request(str(out))
        from hal0.bench.adapters import guidellm as mod

        orig_bin = mod.GUIDELLM_BIN
        mod.GUIDELLM_BIN = "true"  # coreutils `true`: exits 0, no output
        try:
            result = run_guidellm(req, timeout_s=10)
        finally:
            mod.GUIDELLM_BIN = orig_bin
        assert result.outcome is Outcome.OK
        assert result.doc == {"ok": True}


# --------------------------------------------------------------------------- #
# parse_benchmarks — real-captured + hand-built fixtures
# --------------------------------------------------------------------------- #


class TestParseBenchmarksHappy:
    @pytest.fixture
    def doc(self):
        return _load("benchmarks_happy.json")

    def test_reps_come_from_successful_requests(self, doc):
        parsed = parse_benchmarks(doc, "constant")
        n_successful = len(doc["benchmarks"][0]["requests"]["successful"])
        assert n_successful > 0
        assert len(parsed.reps) == n_successful

    def test_rep_fields_carry_ttft_and_decode(self, doc):
        parsed = parse_benchmarks(doc, "constant")
        rep = parsed.reps[0]
        assert rep.t_s is not None
        assert rep.ttft_ms is not None
        assert rep.decode_ts is not None
        # raw distribution preserved verbatim (nothing averaged away at the
        # rep level — p99 etc. are one computation away from this)
        assert "time_to_first_token_ms" in rep.timings_raw
        assert "inter_token_latency_ms" in rep.timings_raw

    def test_summary_percentiles_and_medians(self, doc):
        parsed = parse_benchmarks(doc, "constant")
        assert parsed.summary.decode_ts_med is not None
        assert parsed.summary.ttft_ms_p50 is not None
        assert parsed.summary.ttft_ms_p95 is not None
        assert parsed.summary.ttft_ms_p95 >= parsed.summary.ttft_ms_p50

    def test_engine_observed_is_guidellm_with_version(self, doc):
        parsed = parse_benchmarks(doc, "constant")
        assert parsed.engine_observed.kind == "guidellm"
        assert parsed.engine_observed.llamacpp_build == "0.7.3"

    def test_config_observed_captures_resolved_strategy(self, doc):
        parsed = parse_benchmarks(doc, "constant")
        assert parsed.config_observed is not None
        assert parsed.config_observed.kv.get("strategy_type") == "constant"

    def test_unmatched_kind_falls_back_to_first_entry_not_empty(self, doc):
        # "concurrent" doesn't exist in this single-entry constant-profile doc
        parsed = parse_benchmarks(doc, "concurrent")
        assert len(parsed.reps) == len(doc["benchmarks"][0]["requests"]["successful"])


class TestParseBenchmarksEmpty:
    def test_zero_successful_requests_yields_no_reps(self):
        doc = _load("benchmarks_empty.json")
        parsed = parse_benchmarks(doc, "constant")
        assert parsed.reps == []
        assert parsed.summary.decode_ts_med is None

    def test_no_benchmarks_array_yields_empty_parsed(self):
        parsed = parse_benchmarks({"benchmarks": []}, "synchronous")
        assert parsed.reps == []
        assert parsed.engine_observed.kind == "guidellm"


class TestParseBenchmarksMalformed:
    def test_malformed_doc_never_raises(self):
        doc = _load("benchmarks_malformed.json")
        parsed = parse_benchmarks(doc, "constant")
        assert parsed.reps == []
        assert parsed.config_observed is None

    def test_missing_keys_entirely_never_raises(self):
        parsed = parse_benchmarks({}, "constant")
        assert parsed.reps == []


class TestParseBenchmarksMissingPercentile:
    def test_median_absent_stays_none_not_fabricated(self):
        doc = _load("benchmarks_missing_percentile.json")
        parsed = parse_benchmarks(doc, "synchronous")
        # the fixture's `metrics` block has only a bare `mean`, no `median`/
        # `percentiles` sub-keys — the parser must never invent one.
        assert parsed.summary.decode_ts_med is None
        assert parsed.summary.ttft_ms_p50 is None
        assert parsed.summary.ttft_ms_p95 is None

    def test_rep_still_built_from_the_one_successful_request(self):
        doc = _load("benchmarks_missing_percentile.json")
        parsed = parse_benchmarks(doc, "synchronous")
        assert len(parsed.reps) == 1
        assert parsed.reps[0].ttft_ms == 100.0
        assert parsed.reps[0].decode_ts == 20.0


class TestParseBenchmarksSweep:
    """Sweep combination logic: no committed fixture (a healthy capture at
    ``sweep_size=2`` was verified manually against the mock server, module
    docstring), so this constructs a minimal doc inline matching that
    verified shape — every entry's requests get folded into one reps[]."""

    def _sweep_doc(self) -> dict:
        def entry(strategy_type: str, n: int) -> dict:
            return {
                "config": {"strategy": {"type_": strategy_type}},
                "requests": {
                    "successful": [
                        {
                            "request_latency": 0.1 * i,
                            "time_to_first_token_ms": 10.0 * i,
                            "output_tokens_per_second": 5.0,
                        }
                        for i in range(1, n + 1)
                    ],
                    "errored": [],
                    "incomplete": [],
                    "total": n,
                },
                "metrics": {
                    "output_tokens_per_second": {
                        "successful": {"median": 5.0, "std_dev": 0.1, "percentiles": {"p50": 5.0}}
                    }
                },
            }

        return {
            "metadata": {"guidellm_version": "0.7.3"},
            "config": {"spec": {"backend": {}, "profile": {"kind": "sweep"}}},
            "benchmarks": [entry("synchronous", 2), entry("throughput", 3)],
        }

    def test_sweep_folds_every_entry_into_one_reps_list(self):
        parsed = parse_benchmarks(self._sweep_doc(), "sweep")
        assert len(parsed.reps) == 2 + 3

    def test_non_sweep_kind_only_takes_matching_entries(self):
        parsed = parse_benchmarks(self._sweep_doc(), "throughput")
        assert len(parsed.reps) == 3
