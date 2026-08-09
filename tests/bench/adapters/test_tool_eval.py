"""test_tool_eval.py — red-first tests for the tool-eval-bench adapter.

Every test runs from committed fixtures (``fixtures/tool_eval/``, captured
real per ``capture_tool_eval.py`` — see that script and ``tool_eval.py``'s
module docstring for provenance) or an injected fake runner. Nothing here
shells out to the real tool or touches the network — the pin does not need
to be installed for this suite to pass (Gate per the Phase-3 plan).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.bench.adapters import tool_eval
from hal0.bench.schema import Outcome

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tool_eval"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# --------------------------------------------------------------------------- #
# build_argv
# --------------------------------------------------------------------------- #


def test_build_argv_minimal() -> None:
    req = tool_eval.ToolEvalRequest(
        python_exe="/scratch/bin/python",
        base_url="http://127.0.0.1:8999",
        model="fake-fixture-model",
        output_path=Path("/tmp/out.json"),
    )
    argv = tool_eval.build_argv(req)
    assert argv[:4] == ["/scratch/bin/python", "-m", "tool_eval_bench", "run"]
    assert "--model" in argv and argv[argv.index("--model") + 1] == "fake-fixture-model"
    assert "--backend" in argv and argv[argv.index("--backend") + 1] == "llamacpp"
    assert "--base-url" in argv and argv[argv.index("--base-url") + 1] == "http://127.0.0.1:8999"
    assert "--json" in argv
    assert "--json-file" in argv and argv[argv.index("--json-file") + 1] == "/tmp/out.json"
    # deterministic/offline defaults are on unless explicitly disabled
    assert "--no-warmup" in argv
    assert "--no-probe-engine" in argv
    assert "--no-live" in argv


def test_build_argv_scenario_selection() -> None:
    req = tool_eval.ToolEvalRequest(
        python_exe="python3",
        base_url="http://x",
        model="m",
        output_path=Path("/tmp/o.json"),
        scenarios=("TC-01", "TC-04"),
    )
    argv = tool_eval.build_argv(req)
    idx = argv.index("--scenarios")
    assert argv[idx + 1 : idx + 3] == ["TC-01", "TC-04"]


def test_build_argv_categories_and_short() -> None:
    req = tool_eval.ToolEvalRequest(
        python_exe="python3",
        base_url="http://x",
        model="m",
        output_path=Path("/tmp/o.json"),
        categories=("A", "K"),
        short=True,
    )
    argv = tool_eval.build_argv(req)
    idx = argv.index("--categories")
    assert argv[idx + 1 : idx + 3] == ["A", "K"]
    assert "--short" in argv


def test_build_argv_seed_and_error_rate_omitted_when_unset() -> None:
    req = tool_eval.ToolEvalRequest(
        python_exe="python3", base_url="http://x", model="m", output_path=Path("/tmp/o.json")
    )
    argv = tool_eval.build_argv(req)
    assert "--seed" not in argv
    assert "--error-rate" not in argv


def test_build_argv_seed_and_error_rate_included_when_set() -> None:
    req = tool_eval.ToolEvalRequest(
        python_exe="python3",
        base_url="http://x",
        model="m",
        output_path=Path("/tmp/o.json"),
        seed=7,
        error_rate=0.5,
    )
    argv = tool_eval.build_argv(req)
    assert argv[argv.index("--seed") + 1] == "7"
    assert argv[argv.index("--error-rate") + 1] == "0.5"


def test_build_argv_is_deterministic() -> None:
    req = tool_eval.ToolEvalRequest(
        python_exe="python3",
        base_url="http://x",
        model="m",
        output_path=Path("/tmp/o.json"),
        scenarios=("TC-01",),
    )
    assert tool_eval.build_argv(req) == tool_eval.build_argv(req)


# --------------------------------------------------------------------------- #
# run_tool_eval — injectable runner, no subprocess/network
# --------------------------------------------------------------------------- #


def _request(tmp_path: Path, **kwargs) -> tool_eval.ToolEvalRequest:
    return tool_eval.ToolEvalRequest(
        python_exe="python3",
        base_url="http://127.0.0.1:8999",
        model="fake-fixture-model",
        output_path=tmp_path / "out.json",
        **kwargs,
    )


def test_run_tool_eval_ok(tmp_path: Path) -> None:
    req = _request(tmp_path)
    doc = _load("happy_run.json")

    def fake_runner(argv: list[str], timeout_s: float) -> tuple[int, str, str]:
        req.output_path.write_text(json.dumps(doc))
        return 0, "", ""

    result = tool_eval.run_tool_eval(req, runner=fake_runner)
    assert result.outcome is Outcome.OK
    assert result.rc == 0
    assert result.doc is not None
    assert result.doc["run_id"] == doc["run_id"]


def test_run_tool_eval_connection_failure_no_output_file(tmp_path: Path) -> None:
    req = _request(tmp_path)
    stderr = (FIXTURES / "connection_error.stderr.jsonl").read_text()

    def fake_runner(argv: list[str], timeout_s: float) -> tuple[int, str, str]:
        # real behaviour: no --json-file written at all
        return 2, "", stderr

    result = tool_eval.run_tool_eval(req, runner=fake_runner)
    assert result.outcome is Outcome.FAILED
    assert result.doc is None
    assert "connection_failed" in result.note


def test_run_tool_eval_malformed_output_is_failed_not_raised(tmp_path: Path) -> None:
    req = _request(tmp_path)
    malformed_text = (FIXTURES / "malformed.json").read_text()

    def fake_runner(argv: list[str], timeout_s: float) -> tuple[int, str, str]:
        req.output_path.write_text(malformed_text)
        return 0, "", ""

    result = tool_eval.run_tool_eval(req, runner=fake_runner)
    assert result.outcome is Outcome.FAILED
    assert result.doc is None
    assert "malformed" in result.note.lower()


def test_run_tool_eval_timeout_is_hang(tmp_path: Path) -> None:
    import subprocess

    req = _request(tmp_path)

    def fake_runner(argv: list[str], timeout_s: float) -> tuple[int, str, str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout_s)

    result = tool_eval.run_tool_eval(req, runner=fake_runner)
    assert result.outcome is Outcome.HANG


def test_run_tool_eval_missing_interpreter_is_failed_not_raised(tmp_path: Path) -> None:
    req = _request(tmp_path)

    def fake_runner(argv: list[str], timeout_s: float) -> tuple[int, str, str]:
        raise FileNotFoundError("no such file: python3")

    result = tool_eval.run_tool_eval(req, runner=fake_runner)
    assert result.outcome is Outcome.FAILED
    assert "cannot execute" in result.note


def test_run_tool_eval_nonzero_rc_with_doc_is_failed(tmp_path: Path) -> None:
    req = _request(tmp_path)
    doc = _load("happy_run.json")
    doc["status"] = "aborted"

    def fake_runner(argv: list[str], timeout_s: float) -> tuple[int, str, str]:
        req.output_path.write_text(json.dumps(doc))
        return 1, "", ""

    result = tool_eval.run_tool_eval(req, runner=fake_runner)
    assert result.outcome is Outcome.FAILED
    assert result.doc is not None


# --------------------------------------------------------------------------- #
# parse_scores — happy / malformed / dev-version / missing-task
# --------------------------------------------------------------------------- #


def test_parse_scores_happy_run() -> None:
    doc = _load("happy_run.json")
    suite = tool_eval.parse_scores(doc)

    assert suite.run_id == doc["run_id"]
    assert suite.outcome == "ok"
    assert suite.tool_version == "2.5.0"
    assert suite.scoring_era == tool_eval.ERA_HARDENED
    assert suite.total_scenarios == 3
    assert len(suite.tasks) == 3

    task = next(t for t in suite.tasks if t.task_id == "TC-01")
    assert task.outcome == "failed"  # the fake model never calls a tool
    assert task.correct is False
    assert task.score == 0.0
    assert task.tool_version == "2.5.0"
    assert task.scoring_era == tool_eval.ERA_HARDENED
    assert task.metrics["duration_seconds"] == 0.01
    assert task.metrics["turn_count"] == 1
    assert isinstance(task.checkpoints_hit, list)
    assert task.expected  # expected_behavior prose carried through
    assert task.answer  # summary prose carried through


def test_parse_scores_passing_run_never_maps_to_score_zero() -> None:
    """Regression for #1775: a real v2.5.0 document for a scenario that
    tool-eval-bench itself scored ``final_score: 100`` (pass, points=2) must
    never come out of this parser as ``score: 0.0``. ``passing_run.json`` is
    hand-crafted (not machine-captured — a genuine tool-executing PASS
    requires reproducing tool-eval-bench's internal tool-call protocol in
    the fake server, out of scope here) but mirrors ``happy_run.json``'s
    real captured key set exactly (schema_version/tool_eval_bench_version/
    final_score/rating/deployability/responsiveness/total_scenarios/run_id/
    status/config incl. config_fingerprint/scores incl. category_scores and
    scenario_results/metadata/safety_gate/report_path) — see module
    docstring and the issue for the real shape this pins down."""
    doc = _load("passing_run.json")
    suite = tool_eval.parse_scores(doc)

    assert suite.final_score == 100
    task = next(t for t in suite.tasks if t.task_id == "TC-68")
    assert task.outcome == "ok"
    assert task.correct is True
    assert task.score == 1.0
    assert task.score != 0.0
    assert task.metrics["points"] == 2
    assert task.metrics["final_score"] == 100
    assert task.metrics["duration_seconds"] == 6.22


def test_parse_scores_dev_version_stamps_lenient_version() -> None:
    doc = _load("dev_version_run.json")
    suite = tool_eval.parse_scores(doc)
    # real captured dev-style setuptools-scm version off a HEAD commit past
    # the v2.5.0 tag — must parse without raising and land in the hardened
    # era (2, 5, 1) >= (2, 5, 0).
    assert suite.tool_version.startswith("2.5.1.dev")
    assert suite.scoring_era == tool_eval.ERA_HARDENED
    assert all(t.scoring_era == tool_eval.ERA_HARDENED for t in suite.tasks)


def test_parse_scores_missing_task_dry_run_has_no_scores_block() -> None:
    # dry-run output never has a "scores" key at all — the parser must not
    # crash on a doc shaped nothing like a real run.
    doc = _load("dry_run_missing_task.json")
    suite = tool_eval.parse_scores(doc)
    assert suite.tasks == []
    assert suite.total_scenarios == 0


def test_parse_scores_missing_scores_block_entirely() -> None:
    suite = tool_eval.parse_scores({"run_id": "x", "status": "completed"})
    assert suite.tasks == []
    assert suite.tool_version == ""
    assert suite.scoring_era == tool_eval.ERA_UNKNOWN


def test_parse_scores_unparseable_version_is_unknown_era() -> None:
    doc = _load("happy_run.json")
    doc["tool_eval_bench_version"] = "not-a-version"
    doc["metadata"] = {**doc["metadata"], "tool_version": "not-a-version"}
    suite = tool_eval.parse_scores(doc)
    assert suite.scoring_era == tool_eval.ERA_UNKNOWN


def test_parse_scores_infrastructure_failure_kinds_map_to_hang_or_failed() -> None:
    doc = _load("happy_run.json")
    row = doc["scores"]["scenario_results"][0]
    row["status"] = "fail"
    row["failure_kind"] = "timeout"
    suite = tool_eval.parse_scores(doc)
    hang_task = next(t for t in suite.tasks if t.task_id == row["scenario_id"])
    assert hang_task.outcome == "hang"

    row["failure_kind"] = "connection_error"
    suite2 = tool_eval.parse_scores(doc)
    failed_task = next(t for t in suite2.tasks if t.task_id == row["scenario_id"])
    assert failed_task.outcome == "failed"


def test_parse_scores_pass_status_is_correct_and_full_score() -> None:
    doc = _load("happy_run.json")
    row = doc["scores"]["scenario_results"][0]
    row["status"] = "pass"
    row["points"] = 2
    row["failure_kind"] = None
    suite = tool_eval.parse_scores(doc)
    task = next(t for t in suite.tasks if t.task_id == row["scenario_id"])
    assert task.correct is True
    assert task.outcome == "ok"
    assert task.score == 1.0


def test_parse_scores_partial_status_scores_between_zero_and_one() -> None:
    doc = _load("happy_run.json")
    row = doc["scores"]["scenario_results"][0]
    row["status"] = "partial"
    row["points"] = 1
    row["failure_kind"] = None
    suite = tool_eval.parse_scores(doc)
    task = next(t for t in suite.tasks if t.task_id == row["scenario_id"])
    assert task.correct is False
    assert task.score == 0.5


def test_parse_scores_malformed_doc_shape_degrades_gracefully() -> None:
    # scores present but not a dict, scenario_results present but not a list
    suite = tool_eval.parse_scores({"scores": "not-a-dict", "run_id": "x"})
    assert suite.tasks == []
    suite2 = tool_eval.parse_scores({"scores": {"scenario_results": "nope"}})
    assert suite2.tasks == []


def test_to_dict_round_trips_through_json() -> None:
    doc = _load("happy_run.json")
    suite = tool_eval.parse_scores(doc)
    payload = json.dumps(suite.to_dict())
    reloaded = json.loads(payload)
    assert reloaded["run_id"] == suite.run_id
    assert len(reloaded["tasks"]) == len(suite.tasks)


# --------------------------------------------------------------------------- #
# version parsing / era classification (unit-level, exhaustive)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.5.0", (2, 5, 0)),
        ("2.5.1.dev11+g95e2b5021", (2, 5, 1)),
        ("2.5.1.dev11", (2, 5, 1)),
        ("3.0.0", (3, 0, 0)),
        ("", ()),
        (None, ()),
        ("not-a-version", ()),
        ("v2.5.0", ()),  # leading "v" is not handled — a real scm version never has one
    ],
)
def test_parse_tool_version(raw: str | None, expected: tuple[int, ...]) -> None:
    assert tool_eval.parse_tool_version(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected_era"),
    [
        ("2.5.0", tool_eval.ERA_HARDENED),
        ("2.5.1.dev11+g95e2b5021", tool_eval.ERA_HARDENED),
        ("9.9.9", tool_eval.ERA_HARDENED),
        ("2.4.9", tool_eval.ERA_PRE_HARDENING),
        ("1.0.0", tool_eval.ERA_PRE_HARDENING),
        ("", tool_eval.ERA_UNKNOWN),
        ("garbage", tool_eval.ERA_UNKNOWN),
    ],
)
def test_classify_scoring_era(raw: str, expected_era: str) -> None:
    assert tool_eval.classify_scoring_era(raw) == expected_era


def test_scoring_eras_are_never_silently_equal() -> None:
    """The whole point of stamping an era: a hardened-era record and a
    legacy-era record must produce DIFFERENT markers so a comparison can be
    refused. This is the one invariant that would make the feature a no-op
    if it broke."""
    assert tool_eval.classify_scoring_era("2.5.0") != tool_eval.classify_scoring_era("2.4.9")
    assert tool_eval.classify_scoring_era("2.5.0") != tool_eval.classify_scoring_era("garbage")


def test_argv_pins_output_dir_next_to_the_json_file(tmp_path):
    """No --output-dir means the tool mkdirs ./data relative to the CWD —
    which a service CWD may not permit (on-box PermissionError, 2026-08-09)."""
    req = tool_eval.ToolEvalRequest(
        python_exe="/usr/bin/python3",
        base_url="http://127.0.0.1:8080/v1",
        model="m",
        output_path=tmp_path / "runs" / "out.json",
        scenarios=("TC-68",),
    )
    argv = tool_eval.build_argv(req)
    i = argv.index("--output-dir")
    assert argv[i + 1] == str(tmp_path / "runs")
