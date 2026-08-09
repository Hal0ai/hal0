"""test_evalrun.py — the agentic-eval shim over the tool-eval-bench adapter.

Bench Phase 3 (docs/superpowers/plans/2026-08-09-bench-phase3-oss-adapters.md,
design decision 4) replaced the hand-rolled Hermes-driven scorer with the
pinned OSS harness ``tool-eval-bench``. These tests exercise evalrun.py's thin
integration layer — task-catalogue fetching, argv composition, and
result-shape translation — entirely against injected fakes: nothing here
shells out to a real tool-eval-bench binary (CI has none installed).
"""

from __future__ import annotations

import json
from pathlib import Path

from hal0.bench import evalrun
from hal0.bench.adapters.tool_eval import ERA_HARDENED

_TOOL_EVAL_FIXTURES = Path(__file__).resolve().parent / "adapters" / "fixtures" / "tool_eval"


def _scenario_doc(ids: list[str]) -> str:
    return json.dumps(
        {
            "total_scenarios": len(ids),
            "categories": ["A"],
            "scenarios": [{"id": i, "category": "A", "difficulty": "easy"} for i in ids],
        }
    )


class TestListTasks:
    def test_parses_dry_run_scenario_listing(self):
        def fake_runner(argv, timeout_s):
            assert "--dry-run" in argv and "--json" in argv
            return 0, _scenario_doc(["s1", "s2"]), ""

        tasks = evalrun.list_tasks(runner=fake_runner)
        assert [t.id for t in tasks] == ["s1", "s2"]
        assert all(t.kind == "A" for t in tasks)

    def test_never_raises_on_a_broken_tool(self):
        def boom(argv, timeout_s):
            raise OSError("no such file")

        assert evalrun.list_tasks(runner=boom) == []

    def test_nonzero_exit_degrades_to_empty_list(self):
        assert evalrun.list_tasks(runner=lambda a, t: (2, "", "boom")) == []

    def test_malformed_json_degrades_to_empty_list(self):
        assert evalrun.list_tasks(runner=lambda a, t: (0, "not json", "")) == []


class TestEnsureTasks:
    def test_skips_the_fetch_when_already_populated(self, monkeypatch):
        monkeypatch.setattr(evalrun, "TASKS", [evalrun.Task(id="existing")])

        def boom(*a, **k):
            raise AssertionError("list_tasks must not run when TASKS is already set")

        monkeypatch.setattr(evalrun, "list_tasks", boom)
        assert evalrun.ensure_tasks() == [evalrun.Task(id="existing")]

    def test_skips_the_fetch_when_the_tool_is_missing(self, monkeypatch):
        monkeypatch.setattr(evalrun, "TASKS", [])
        monkeypatch.setattr(evalrun, "tool_eval_missing", lambda: "not installed")

        def boom(*a, **k):
            raise AssertionError("list_tasks must not run when the tool is missing")

        monkeypatch.setattr(evalrun, "list_tasks", boom)
        assert evalrun.ensure_tasks() == []


def test_tool_eval_missing_checks_importability(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    msg = evalrun.tool_eval_missing()
    assert msg and "tool_eval_bench" in msg

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert evalrun.tool_eval_missing() is None


def test_tool_eval_cmd_shape():
    task = evalrun.Task(id="scenario-1", kind="A")
    cmd = evalrun.tool_eval_cmd(
        task, "some-model", "http://127.0.0.1:8080", python_exe="/usr/bin/python3"
    )
    assert cmd[0] == "/usr/bin/python3"
    assert "run" in cmd
    assert cmd[cmd.index("--model") + 1] == "some-model"
    assert cmd[cmd.index("--base-url") + 1] == "http://127.0.0.1:8080/v1"
    assert cmd[cmd.index("--scenarios") + 1] == "scenario-1"


class TestRunTask:
    def _happy_doc(self, scenario_id="s1", points=2):
        return {
            "status": "completed",
            "run_id": "r1",
            "tool_eval_bench_version": "2.5.0",
            "final_score": points,
            "total_scenarios": 1,
            "scores": {
                "scenario_results": [
                    {
                        "scenario_id": scenario_id,
                        "status": "pass" if points == 2 else "fail",
                        "points": points,
                        "summary": "did the thing",
                        "expected_behavior": "do the thing",
                        "tool_calls_made": ["a", "b"],
                        "duration_seconds": 12.5,
                        "turn_count": 3,
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                    }
                ]
            },
        }

    def test_ok_scenario_translates_to_an_eval_record(self, tmp_path):
        task = evalrun.Task(id="s1", kind="A")

        def fake_runner(argv, timeout_s):
            out_path = argv[argv.index("--json-file") + 1]
            with open(out_path, "w") as fh:
                json.dump(self._happy_doc(), fh)
            return 0, "", ""

        rec = evalrun.run_task(task, "m1", "run-1", "http://x:8080", tmp_path, runner=fake_runner)

        assert rec.outcome == "ok"
        assert rec.correct is True
        assert rec.score == 1.0
        assert rec.tool_version == "2.5.0"
        assert rec.scoring_era == ERA_HARDENED
        # legacy metric names are still populated (design decision 4: no
        # shape change for downstream consumers of evals.jsonl)
        assert rec.metrics["wall_s"] == 12.5
        assert rec.metrics["tool_calls"] == 2
        assert rec.metrics["tokens_out"] == 50
        assert rec.metrics["duration_seconds"] == 12.5  # raw name also present

    def test_failed_scenario_translates_to_a_failed_record(self, tmp_path):
        task = evalrun.Task(id="s1", kind="A")

        def fake_runner(argv, timeout_s):
            out_path = argv[argv.index("--json-file") + 1]
            with open(out_path, "w") as fh:
                json.dump(self._happy_doc(points=0), fh)
            return 0, "", ""

        rec = evalrun.run_task(task, "m1", "run-1", "http://x:8080", tmp_path, runner=fake_runner)
        assert rec.outcome == "failed"
        assert rec.correct is False
        assert rec.score == 0.0

    def test_real_shaped_v2_5_0_passing_document_never_maps_to_score_zero(self, tmp_path):
        """Regression for #1775: ``hal0 bench eval`` mapped a passing
        tool-eval-bench run (real ``final_score: 100``) to ``score=0.0``,
        printed with the retired hand-rolled table columns (``got/want/
        wall/tools/tok_out/steps``, all empty/None — the ``_failed_record``
        defaults). This feeds ``run_task`` a document shaped exactly like a
        real v2.5.0 ``--json-file`` envelope (see
        ``tests/bench/adapters/fixtures/tool_eval/passing_run.json``, and
        ``adapters/tool_eval.py``'s module docstring for the real shape) and
        asserts the score survives the full evalrun -> adapter round trip —
        not just the adapter's own parser (already covered by
        ``test_tool_eval.py::test_parse_scores_passing_run_never_maps_to_score_zero``)."""
        doc = json.loads((_TOOL_EVAL_FIXTURES / "passing_run.json").read_text())
        task = evalrun.Task(id="TC-68", kind="A")

        def fake_runner(argv, timeout_s):
            out_path = argv[argv.index("--json-file") + 1]
            with open(out_path, "w") as fh:
                json.dump(doc, fh)
            return 0, "", ""

        rec = evalrun.run_task(
            task,
            "chadrock-35b-ace-saber-rocmfp4-mtp",
            "run-1",
            "http://127.0.0.1:8080",
            tmp_path,
            runner=fake_runner,
        )

        assert rec.outcome == "ok"
        assert rec.correct is True
        assert rec.score == 1.0
        assert rec.score != 0.0
        assert rec.answer  # not '' like the _failed_record default
        assert rec.expected  # not '' like the _failed_record default
        assert rec.metrics["duration_seconds"] == 6.22
        assert rec.metrics["wall_s"] == 6.22  # legacy alias still populated
        assert rec.metrics["points"] == 2
        assert rec.metrics["final_score"] == 100

    def test_connection_failure_never_raises(self, tmp_path):
        """A pre-flight connection failure (adapters/tool_eval.py's real
        behaviour): rc!=0, no --json-file written. Must return a FAILED
        record, never an exception."""
        task = evalrun.Task(id="s1", kind="A")
        rec = evalrun.run_task(
            task,
            "m1",
            "run-1",
            "http://x:8080",
            tmp_path,
            runner=lambda a, t: (2, "", "conn refused"),
        )
        assert rec.outcome == "failed"
        assert rec.score == 0.0

    def test_timeout_never_raises(self, tmp_path):
        import subprocess

        task = evalrun.Task(id="s1", kind="A")

        def timeout_runner(argv, timeout_s):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout_s)

        rec = evalrun.run_task(
            task, "m1", "run-1", "http://x:8080", tmp_path, runner=timeout_runner
        )
        assert rec.outcome == "hang"

    def test_scenario_missing_from_output_is_a_failed_record_not_a_crash(self, tmp_path):
        """An unknown --scenarios id filters down to total_scenarios: 0 with
        no error (adapters/tool_eval.py module docstring's "missing task"
        case) — the row this task expects just isn't there."""
        task = evalrun.Task(id="does-not-exist", kind="")

        def fake_runner(argv, timeout_s):
            out_path = argv[argv.index("--json-file") + 1]
            with open(out_path, "w") as fh:
                json.dump(self._happy_doc(scenario_id="s1"), fh)
            return 0, "", ""

        rec = evalrun.run_task(task, "m1", "run-1", "http://x:8080", tmp_path, runner=fake_runner)
        assert rec.outcome == "failed"
        assert "missing" in rec.note


def test_never_invokes_hermes(tmp_path, monkeypatch):
    """Design decision 4: the new path must not invoke hermes at all. There
    is no HERMES constant or hermes_missing() left to accidentally call."""
    assert not hasattr(evalrun, "HERMES")
    assert not hasattr(evalrun, "hermes_missing")
    assert not hasattr(evalrun, "hermes_cmd")
