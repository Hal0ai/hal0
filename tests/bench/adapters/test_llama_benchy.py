"""test_llama_benchy.py — llama-benchy adapter (Bench Phase 3, Track B).

Every fixture in ``fixtures/llama_benchy/`` is either a REAL capture of the
pinned tool (tag v0.4.0 = sha 446dd42fde2ebbaa1d68a0dfe9dc1e5b833f95ad) —
``happy_single_depth.json`` / ``happy_multi_depth.json`` / the connection-
refused stderr tail — or a deliberately hand-authored synthetic edge case
(``empty_benchmarks.json``, ``malformed.json``); see
``capture_llama_benchy.py``'s module docstring for exactly which and why. No
test here touches the real tool, a subprocess, or the network — every
:func:`run_llama_benchy` call injects a fake ``runner``.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from hal0.bench.adapters.llama_benchy import (
    LLAMA_BENCHY_BIN,
    LlamaBenchyRequest,
    build_argv,
    parse_benchy,
    run_llama_benchy,
)
from hal0.bench.schema import Outcome

FIXTURES = Path(__file__).parent / "fixtures" / "llama_benchy"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# build_argv — exact CLI shape.
# --------------------------------------------------------------------------- #


def _request(**overrides) -> LlamaBenchyRequest:
    base = dict(
        endpoint="http://127.0.0.1:8080/v1",
        model="my-model",
        pp=512,
        tg=128,
        result_path=Path("/tmp/does-not-matter.json"),
        depths=(0,),
        reps=3,
    )
    base.update(overrides)
    return LlamaBenchyRequest(**base)


def test_build_argv_minimal_shape():
    argv = build_argv(_request())
    assert argv[0] == LLAMA_BENCHY_BIN
    assert argv == [
        "llama-benchy",
        "--base-url",
        "http://127.0.0.1:8080/v1",
        "--api-key",
        "EMPTY",
        "--model",
        "my-model",
        "--served-model-name",
        "my-model",
        "--pp",
        "512",
        "--tg",
        "128",
        "--depth",
        "0",
        "--runs",
        "3",
        "--no-warmup",
        "--no-adapt-prompt",
        "--skip-coherence",
        "--latency-mode",
        "none",
        "--format",
        "json",
        "--save-result",
        "/tmp/does-not-matter.json",
    ]


def test_build_argv_multi_depth_preserves_order():
    argv = build_argv(_request(depths=(0, 128, 4096)))
    depth_idx = argv.index("--depth")
    assert argv[depth_idx + 1 : depth_idx + 4] == ["0", "128", "4096"]


def test_build_argv_reps_maps_to_runs_flag():
    argv = build_argv(_request(reps=7))
    assert argv[argv.index("--runs") + 1] == "7"


def test_build_argv_no_warmup_and_no_adapt_prompt_both_present():
    """Both flags are required to actually skip warmup on the installed
    v0.4.0 CLI (see build_argv's docstring) — a regression that drops either
    one silently re-enables a warmup exchange against the live endpoint."""
    argv = build_argv(_request())
    assert "--no-warmup" in argv
    assert "--no-adapt-prompt" in argv


def test_build_argv_optional_tokenizer_and_book_url_omitted_by_default():
    argv = build_argv(_request())
    assert "--tokenizer" not in argv
    assert "--book-url" not in argv


def test_build_argv_optional_tokenizer_and_book_url_included_when_set():
    argv = build_argv(_request(tokenizer="gpt2", book_url="http://x/book"))
    assert argv[argv.index("--tokenizer") + 1] == "gpt2"
    assert argv[argv.index("--book-url") + 1] == "http://x/book"


def test_build_argv_exact_tg_flag():
    assert "--exact-tg" not in build_argv(_request(exact_tg=False))
    assert "--exact-tg" in build_argv(_request(exact_tg=True))


def test_build_argv_extra_args_appended_last():
    argv = build_argv(_request(extra_args=("--enable-prefix-caching",)))
    assert argv[-1] == "--enable-prefix-caching"


# --------------------------------------------------------------------------- #
# run_llama_benchy — injectable runner, Outcome classification.
# --------------------------------------------------------------------------- #


def test_run_llama_benchy_ok_reads_result_file(tmp_path):
    result_path = tmp_path / "report.json"
    result_path.write_text(json.dumps(_load("happy_single_depth.json")))

    def fake_runner(argv, timeout_s):
        assert result_path.name in argv[argv.index("--save-result") + 1]
        return 0, "stdout banner\n", ""

    result = run_llama_benchy(_request(result_path=result_path), runner=fake_runner)
    assert result.outcome is Outcome.OK
    assert result.rc == 0
    assert result.doc is not None
    assert result.doc["benchmarks"]


def test_run_llama_benchy_nonzero_exit_is_failed(tmp_path):
    result_path = tmp_path / "report.json"  # never written

    def fake_runner(argv, timeout_s):
        return 1, "", (FIXTURES / "error_connection_refused.stderr.txt").read_text()

    result = run_llama_benchy(_request(result_path=result_path), runner=fake_runner)
    assert result.outcome is Outcome.FAILED
    assert result.doc is None
    assert "Cannot connect" in result.tail


def test_run_llama_benchy_timeout_is_hang(tmp_path):
    result_path = tmp_path / "report.json"

    def fake_runner(argv, timeout_s):
        return -9, "partial output", "watchdog-timeout"

    result = run_llama_benchy(_request(result_path=result_path), runner=fake_runner)
    assert result.outcome is Outcome.HANG


def test_run_llama_benchy_zero_exit_but_missing_file_is_failed(tmp_path):
    """rc==0 with the --save-result file never actually written (e.g. the
    tool exited via an early SystemExit path this adapter has not seen) must
    not be reported OK — no file means no measurement."""
    result_path = tmp_path / "never-written.json"

    def fake_runner(argv, timeout_s):
        return 0, "", ""

    result = run_llama_benchy(_request(result_path=result_path), runner=fake_runner)
    assert result.outcome is Outcome.FAILED
    assert result.doc is None


def test_run_llama_benchy_zero_exit_empty_benchmarks_is_failed(tmp_path):
    result_path = tmp_path / "report.json"
    result_path.write_text(json.dumps(_load("empty_benchmarks.json")))

    def fake_runner(argv, timeout_s):
        return 0, "", ""

    result = run_llama_benchy(_request(result_path=result_path), runner=fake_runner)
    assert result.outcome is Outcome.FAILED
    assert result.doc is not None  # readable, just empty — kept for the note


def test_run_llama_benchy_zero_exit_malformed_json_is_failed(tmp_path):
    result_path = tmp_path / "report.json"
    result_path.write_text((FIXTURES / "malformed.json").read_text())

    def fake_runner(argv, timeout_s):
        return 0, "", ""

    result = run_llama_benchy(_request(result_path=result_path), runner=fake_runner)
    assert result.outcome is Outcome.FAILED
    assert result.doc is None


def test_run_llama_benchy_passes_timeout_through_to_runner(tmp_path):
    result_path = tmp_path / "report.json"
    result_path.write_text(json.dumps(_load("happy_single_depth.json")))
    seen = {}

    def fake_runner(argv, timeout_s):
        seen["timeout_s"] = timeout_s
        return 0, "", ""

    run_llama_benchy(_request(result_path=result_path, timeout_s=120.0), runner=fake_runner)
    assert seen["timeout_s"] == 120.0


# --------------------------------------------------------------------------- #
# parse_benchy — from committed fixtures only, depth-aware.
# --------------------------------------------------------------------------- #


def test_parse_benchy_rejects_bad_kind():
    with pytest.raises(ValueError, match="kind"):
        parse_benchy(_load("happy_single_depth.json"), kind="chat")


def test_parse_benchy_pp_reps_and_summary_single_depth():
    doc = _load("happy_single_depth.json")
    parsed = parse_benchy(doc, kind="pp", depth=0)

    row = doc["benchmarks"][0]
    expected_values = row["pp_throughput"]["values"]
    assert len(parsed.reps) == len(expected_values)
    for rep, val in zip(parsed.reps, expected_values, strict=True):
        assert rep.prefill_ts == pytest.approx(val)
        assert rep.decode_ts is None
        assert rep.t_s is not None  # derived from prompt_size / t/s
    assert parsed.summary.prefill_ts_med is not None
    assert parsed.summary.decode_ts_med is None


def test_parse_benchy_tg_reps_and_summary_single_depth():
    doc = _load("happy_single_depth.json")
    parsed = parse_benchy(doc, kind="tg", depth=0)

    row = doc["benchmarks"][0]
    expected_values = row["tg_throughput"]["values"]
    assert len(parsed.reps) == len(expected_values)
    for rep, val in zip(parsed.reps, expected_values, strict=True):
        assert rep.decode_ts == pytest.approx(val)
        assert rep.prefill_ts is None
    assert parsed.summary.decode_ts_med is not None
    assert parsed.summary.prefill_ts_med is None
    # engine's own std preferred when present (rounded to 4dp by parse_benchy).
    assert parsed.summary.decode_ts_stddev == pytest.approx(row["tg_throughput"]["std"], abs=1e-3)


def test_parse_benchy_ttft_from_e2e_ttft():
    doc = _load("happy_single_depth.json")
    parsed = parse_benchy(doc, kind="tg", depth=0)
    row = doc["benchmarks"][0]
    if row.get("e2e_ttft"):
        assert parsed.reps[0].ttft_ms == pytest.approx(row["e2e_ttft"]["values"][0])
        assert parsed.summary.ttft_ms_p50 is not None
        assert parsed.summary.ttft_ms_p95 is not None


def test_parse_benchy_is_depth_aware_multi_depth():
    doc = _load("happy_multi_depth.json")
    depths = sorted({b["context_size"] for b in doc["benchmarks"]})
    assert len(depths) >= 2  # fixture actually sweeps more than one depth

    for depth in depths:
        parsed = parse_benchy(doc, kind="tg", depth=depth)
        assert parsed.config_observed is not None
        assert parsed.config_observed.ctx == depth
        assert f"--depth {depth}" in " ".join(parsed.config_observed.argv)


def test_parse_benchy_engine_observed_stamps_version():
    doc = _load("happy_single_depth.json")
    parsed = parse_benchy(doc, kind="pp", depth=0)
    assert parsed.engine_observed.kind == "llama-benchy"
    assert parsed.engine_observed.tool_version == doc["version"]


def test_parse_benchy_no_matching_depth_falls_back_to_last_row():
    doc = _load("happy_multi_depth.json")
    parsed = parse_benchy(doc, kind="tg", depth=999999)
    last_row = doc["benchmarks"][-1]
    assert parsed.summary.decode_ts_med == pytest.approx(
        __import__("statistics").median(last_row["tg_throughput"]["values"])
    )


def test_parse_benchy_none_doc_returns_empty_parsed():
    parsed = parse_benchy(None, kind="pp", depth=0)
    assert parsed.reps == []
    assert parsed.summary.prefill_ts_med is None
    assert parsed.config_observed is None


def test_parse_benchy_empty_benchmarks_returns_empty_parsed():
    parsed = parse_benchy(_load("empty_benchmarks.json"), kind="tg", depth=0)
    assert parsed.reps == []
    assert parsed.summary.decode_ts_med is None


# --------------------------------------------------------------------------- #
# Fixtures validate against the upstream v0.4.0 JSON schema.
# --------------------------------------------------------------------------- #

_UPSTREAM_SCHEMA = json.loads(
    (FIXTURES / "_upstream_benchmark_report_schema.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("name", ["happy_single_depth.json", "happy_multi_depth.json"])
def test_captured_fixture_matches_upstream_schema(name):
    jsonschema.validate(_load(name), _UPSTREAM_SCHEMA)


def test_synthetic_empty_fixture_matches_upstream_schema():
    """empty_benchmarks.json is hand-authored (see capture script docstring)
    but must still be a STRUCTURALLY valid report — only ``benchmarks`` is
    empty, nothing else is malformed."""
    jsonschema.validate(_load("empty_benchmarks.json"), _UPSTREAM_SCHEMA)


def test_synthetic_malformed_fixture_is_not_valid_json():
    with pytest.raises(json.JSONDecodeError):
        _load("malformed.json")
