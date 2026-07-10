"""test_evalrun.py — the agentic-eval scorer (pure, no live agent).

The scorer is the trust boundary of the quality tier: a task passes only if the
DERIVED expected value actually appears in the agent's final answer, and partial
credit tracks how many hidden checkpoints the trace reached. These exercise both
against mock transcripts so scoring is verified without a GPU.
"""

from __future__ import annotations

from hal0.bench.evalrun import (
    extract_answer,
    get_task,
    hermes_cmd,
    score_task,
)


def _codebase_task():
    return get_task("codebase-combine")


def test_extract_answer_last_nonempty_line():
    assert extract_answer("thinking...\n\n  8549  \n") == "8549"
    assert extract_answer("") == ""


def test_correct_final_answer_scores_one():
    task = _codebase_task()
    transcript = "read src/config.py -> 8412\nread lib/util.py -> 137\nsum is:\n8549"
    sc = score_task(task, transcript, expected="8549")
    assert sc.correct is True
    assert sc.score == 1.0
    assert set(sc.checkpoints_hit) == {"8412", "137"}


def test_wrong_answer_but_reached_one_checkpoint_gets_partial():
    task = _codebase_task()
    # found BASE_PORT but never the offset, and answered wrong
    transcript = "found BASE_PORT 8412\nI think the port is 8412"
    sc = score_task(task, transcript, expected="8549")
    assert sc.correct is False
    assert sc.checkpoints_hit == ["8412"]
    assert sc.score == 0.25  # 0.5 * (1/2), capped below a pass


def test_wrong_answer_no_checkpoints_scores_zero():
    task = _codebase_task()
    sc = score_task(task, "I could not find the files.\nunknown", expected="8549")
    assert sc.correct is False
    assert sc.score == 0.0


def test_answer_may_be_wrapped_in_prose():
    task = _codebase_task()
    # lenient: expected value present in the last line even amid words
    sc = score_task(task, "8412\n137\nThe health-check port is 8549.", expected="8549")
    assert sc.correct is True


def test_checkpoints_scanned_in_trace_not_just_stdout():
    # hermes -z prints only the final answer to stdout; the intermediate hidden
    # values live in the exported tool-call trace. Correct answer on stdout,
    # checkpoints only in the trace -> still full credit + both checkpoints hit.
    task = _codebase_task()
    stdout = "8549"
    trace = '[{"role":"tool","content":"config.py: BASE_PORT=8412"},{"content":"util: 137"}]'
    sc = score_task(task, stdout, expected="8549", trace=trace)
    assert sc.correct and sc.score == 1.0
    assert set(sc.checkpoints_hit) == {"8412", "137"}


def test_fixture_derived_answers_are_stable():
    # the derive-from-fixture helpers must stay in sync with the checked-in
    # fixtures — a drift (edited fixture, broken cipher) fails here, not silently
    # mis-scores a live run.
    from hal0.bench.evalrun import (
        _cipher_answer,
        _dep_answer,
        _grep_answer,
        _loop_answer,
        _recurrence_answer,
    )

    assert _cipher_answer() == "7391"
    assert _loop_answer() == "465"
    assert _dep_answer() == "4471"
    assert _grep_answer() == "144"
    assert _recurrence_answer() == "87"


def test_hermes_cmd_shape():
    task = _codebase_task()
    cmd = hermes_cmd(task, "some-model", "http://127.0.0.1:8080")
    assert cmd[1] == "-z" and cmd[2] == task.prompt
    assert "--yolo" in cmd and "--provider" in cmd and "custom" in cmd
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "some-model"
    assert cmd[cmd.index("-t") + 1] == task.toolsets
