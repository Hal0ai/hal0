"""hal0 bench eval's result display (#1775): tool-eval-bench (Bench Phase 3)
replaced the old hand-rolled Hermes-driven eval scorer, but the CLI's result
table kept printing that scorer's columns (``got``/``want``/``wall``/
``tools``/``tok_out``/``steps``) — fields tool-eval-bench's JSON envelope
never produces (see ``adapters/tool_eval.py``'s module docstring for its real
shape). This locks the display down to the vocabulary the tool actually
reports: scenario id, status, points, duration, and the run's final score."""

from __future__ import annotations

from hal0.bench import evalrun
from hal0.bench.cli import _format_eval_row


def _passing_record() -> evalrun.EvalRecord:
    return evalrun.EvalRecord(
        run_id="run-1",
        suite="agentic",
        task_id="TC-68",
        kind="A",
        model="chadrock-35b-ace-saber-rocmfp4-mtp",
        outcome="ok",
        score=1.0,
        correct=True,
        expected="Use get_weather.",
        answer="Correctly used the tool.",
        checkpoints_hit=["get_weather(Berlin)"],
        checkpoints_total=0,
        metrics={
            "duration_seconds": 6.22,
            "points": 2,
            "final_score": 100,
            "wall_s": 6.22,
            "tool_calls": 1,
        },
        note="",
        tool_version="2.5.0",
        scoring_era="hardened-2026-08",
    )


def test_format_eval_row_uses_tool_eval_bench_vocabulary():
    line = _format_eval_row(_passing_record())
    assert "TC-68" in line
    assert "status=ok" in line
    assert "points=2" in line
    assert "duration=6.22s" in line
    assert "final_score=100" in line


def test_format_eval_row_never_prints_the_retired_hand_rolled_columns():
    line = _format_eval_row(_passing_record())
    for retired in ("got=", "want=", "wall=", "tools=", "tok_out=", "steps="):
        assert retired not in line, f"retired hand-rolled column {retired!r} still printed"


def test_format_eval_row_never_prints_score_zero_for_a_passing_scenario():
    """The #1775 symptom: a real pass (final_score 100) rendered as
    ``score=0.0``. The old format string is gone entirely now, but pin down
    the underlying invariant too — a correct, full-points record's line
    never claims a zero final_score."""
    line = _format_eval_row(_passing_record())
    assert "final_score=0 " not in line
    assert not line.rstrip().endswith("final_score=0")
