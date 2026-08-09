"""evalrun.py — agentic-scenario eval (the quality tier).

benchlab's cells measure SPEED. This module measures whether a model actually
completes tool-calling scenarios — correctly, safely, and without fabricating
data. Bench Phase 3 (docs/superpowers/plans/2026-08-09-bench-phase3-oss-
adapters.md, design decision 4) replaced the hand-rolled Hermes-driven scorer
that lived here with the pinned OSS harness ``tool-eval-bench`` (tag v2.5.0,
offline/deterministic, scoring hardened ~2026-08-03): this module is now a
thin shim over ``hal0.bench.adapters.tool_eval`` that keeps the CLI/worker
surface (``hal0 bench eval --models --task --dry-run --force``), the
politeness gate, and the store path (``<state root>/evals.jsonl``) evalrun
has always used, so nothing downstream (the ``/api/benchmarks/evals`` route,
bundle export) has to change shape.

Everything Hermes-specific from the old implementation is gone: there is no
``HERMES`` binary, no headless ``-z`` invocation, no hand-written task
catalogue with derive-from-fixture expected answers, no exact-match scorer.
tool-eval-bench IS the scorer (rubric-graded, its own scenario contracts) and
IS the task catalogue (``list_tasks`` reads it via its own ``--dry-run
--json`` listing, never a hardcoded table that could drift from a future
tool-eval-bench release). This path never invokes hermes.

Records land in ``evals.jsonl`` (separate from throughput records.jsonl —
different shape: a score, not a t/s), one row per (model, scenario), now also
stamped with ``tool_version`` + ``scoring_era`` (the adapter's "never compare
scores across the hardening boundary" contract).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .adapters import TOOL_EVAL_BENCH_PIN, tool_eval
from .schema import Outcome
from .store import state_root

#: The console-script entry point (``[project.scripts]`` in tool-eval-bench's
#: own pyproject.toml) — checked at plan time, same PATH-lookup pattern as
#: every other engine binary this codebase shells out to (mirrors
#: ``planner._ADAPTER_TOOL_FOR_KIND`` for the guidellm/llama-benchy kinds;
#: eval has no planner, so cmd_eval/cmd_worker check this directly).
TOOL_EVAL_BENCH_BIN = "tool-eval-bench"

# Sized to the SLOWEST of the retired hand-rolled tasks (420s): an agentic
# scenario against a large local model on this thermal-limited APU legitimately
# runs for minutes, and a timeout here records a failure a re-run won't fix.
_DEFAULT_TASK_TIMEOUT_S = 420.0


def tool_eval_missing() -> str | None:
    """An actionable reason string when tool-eval-bench can't run (None when
    it can). Checked up-front by the CLI and worker so a box without it gets
    one clean message instead of a per-scenario traceback (the same #1526
    rule the old ``hermes_missing`` enforced, now for the pinned tool)."""
    import importlib.util

    if importlib.util.find_spec("tool_eval_bench") is None:
        return (
            f"tool_eval_bench is not importable by this interpreter (pin: {TOOL_EVAL_BENCH_PIN}) — "
            "agentic eval needs tool-eval-bench installed in hal0's venv"
        )
    return None


# --------------------------------------------------------------------------- #
# task catalogue — sourced from tool-eval-bench's own scenario listing
# --------------------------------------------------------------------------- #


@dataclass
class Task:
    """One tool-eval-bench scenario, as much as the CLI/worker need: an id
    (passed straight through to ``--scenarios``) and its category letter for
    display. Deliberately NOT the old hand-rolled ``Task`` (no prompt/
    checkpoints/expected_fn/fixture) — tool-eval-bench owns the scenario
    content now; this is just enough to select and label one."""

    id: str
    kind: str = ""


#: The live catalogue, populated by :func:`ensure_tasks` on first use. A
#: plain module-level list (not a function call) so callers/tests can
#: monkeypatch it directly, same seam the old hermes-driven ``TASKS`` gave.
TASKS: list[Task] = []


def _default_dry_run_runner(argv: list[str], timeout_s: float) -> tuple[int, str, str]:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    return proc.returncode, proc.stdout, proc.stderr


def list_tasks(
    *,
    python_exe: str = sys.executable,
    runner: Callable[[list[str], float], tuple[int, str, str]] | None = None,
) -> list[Task]:
    """The real scenario catalogue via ``python -m tool_eval_bench run
    --dry-run --json`` (needs no server or model at all — see
    ``adapters/tool_eval.py``'s module docstring). Never raises: a
    missing/broken tool degrades to an empty list, same "never crash the CLI"
    rule as the rest of this module — the caller surfaces
    :func:`tool_eval_missing` separately for the actionable message."""
    argv = [python_exe, "-m", tool_eval.MODULE, "run", "--dry-run", "--json"]
    run = runner or _default_dry_run_runner
    try:
        rc, out, _err = run(argv, 30.0)
    except (OSError, subprocess.SubprocessError):
        return []
    if rc != 0:
        return []
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        return []
    scenarios = doc.get("scenarios") if isinstance(doc, dict) else None
    if not isinstance(scenarios, list):
        return []
    return [
        Task(id=str(s["id"]), kind=str(s.get("category") or ""))
        for s in scenarios
        if isinstance(s, dict) and s.get("id")
    ]


def ensure_tasks(
    *,
    python_exe: str = sys.executable,
    runner: Callable[[list[str], float], tuple[int, str, str]] | None = None,
) -> list[Task]:
    """Populate :data:`TASKS` from the real tool on first use; a no-op if
    already populated (a test's ``monkeypatch.setattr(evalrun, "TASKS", …)``
    is never overwritten) or if the tool isn't installed (``TASKS`` stays
    empty — the caller checks :func:`tool_eval_missing` for why)."""
    global TASKS
    if TASKS:
        return TASKS
    if tool_eval_missing():
        return TASKS
    TASKS = list_tasks(python_exe=python_exe, runner=runner)
    return TASKS


def get_task(task_id: str) -> Task | None:
    return next((t for t in TASKS if t.id == task_id), None)


# --------------------------------------------------------------------------- #
# argv — pure, used by the runner and by `eval --dry-run`
# --------------------------------------------------------------------------- #


def tool_eval_cmd(
    task: Task,
    model: str,
    api: str,
    *,
    python_exe: str = sys.executable,
    output_path: Path | str = "<out>",
) -> list[str]:
    """The exact tool-eval-bench argv for one scenario against one model
    (pure — used by ``eval --dry-run`` and mirrored by :func:`run_task`).
    Routes through hal0's own OpenAI-compatible gateway (``{api}/v1``), the
    same endpoint the old Hermes ``--provider custom`` path resolved a model
    id through — so this needs no separate slot/port lookup."""
    request = tool_eval.ToolEvalRequest(
        python_exe=python_exe,
        base_url=f"{api.rstrip('/')}/v1",
        model=model,
        output_path=Path(output_path),
        scenarios=(task.id,),
        timeout_s=_DEFAULT_TASK_TIMEOUT_S,
    )
    return tool_eval.build_argv(request)


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass
class EvalRecord:
    run_id: str
    suite: str
    task_id: str
    kind: str
    model: str
    outcome: str  # "ok" | "failed" | "hang" — schema.Outcome vocabulary
    score: float
    correct: bool
    expected: str
    answer: str
    checkpoints_hit: list[str]
    checkpoints_total: int
    metrics: dict[str, Any]
    note: str = ""
    # Bench Phase 3: which tool-eval-bench build produced this score, and
    # which side of the ~2026-08-03 scoring-hardening boundary it falls on
    # (adapters/tool_eval.py) — a score from before that boundary is not
    # comparable to one from after it. Empty for pre-Phase-3 records.
    tool_version: str = ""
    scoring_era: str = ""
    schema: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _evals_path() -> Path:
    return state_root() / "evals.jsonl"


def append_eval(rec: EvalRecord) -> None:
    p = _evals_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec.to_dict(), separators=(",", ":"), ensure_ascii=False) + "\n")


def read_evals() -> list[dict[str, Any]]:
    p = _evals_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# --------------------------------------------------------------------------- #
# run — one scenario, through the tool-eval-bench adapter
# --------------------------------------------------------------------------- #


def _failed_record(
    task: Task, model: str, run_id: str, outcome: str, note: str, tool_version: str = ""
) -> EvalRecord:
    return EvalRecord(
        run_id=run_id,
        suite="agentic",
        task_id=task.id,
        kind=task.kind,
        model=model,
        outcome=outcome,
        score=0.0,
        correct=False,
        expected="",
        answer="",
        checkpoints_hit=[],
        checkpoints_total=0,
        metrics={},
        note=note,
        tool_version=tool_version,
        scoring_era=tool_eval.classify_scoring_era(tool_version),
    )


def _normalize_metrics(raw: dict[str, Any], checkpoints_hit: list[str]) -> dict[str, Any]:
    """tool-eval-bench's own metric names (``duration_seconds``/
    ``completion_tokens``/``prompt_tokens``) PLUS the pre-Phase-3 names
    (``wall_s``/``tool_calls``/``tokens_out``/``tokens_in``) the CLI table
    and the ``/api/benchmarks/evals`` route already read — so neither
    downstream consumer needed a shape change for Phase 3 (design decision
    4: "Records keep the store path evalrun uses today")."""
    metrics = dict(raw)
    metrics.setdefault("wall_s", raw.get("duration_seconds"))
    metrics.setdefault("tool_calls", len(checkpoints_hit))
    metrics.setdefault("tokens_out", raw.get("completion_tokens"))
    metrics.setdefault("tokens_in", raw.get("prompt_tokens"))
    return metrics


def run_task(
    task: Task,
    model: str,
    run_id: str,
    api: str,
    workroot: Path,
    *,
    runner: Callable[[list[str], float], tuple[int, str, str]] | None = None,
    python_exe: str = sys.executable,
) -> EvalRecord:
    """Drive ONE tool-eval-bench scenario for one model, then translate its
    result into evalrun's :class:`EvalRecord` shape. Never raises on a bad
    run — a timeout, a missing tool, a connection failure, or a scenario the
    tool doesn't recognize all produce a returned FAILED/HANG record, not an
    exception (mirrors ``harness.run_cell`` / the old hermes-driven
    ``run_task``)."""
    out_path = workroot / f"{task.id}-{run_id[-6:]}.json"
    request = tool_eval.ToolEvalRequest(
        python_exe=python_exe,
        base_url=f"{api.rstrip('/')}/v1",
        model=model,
        output_path=out_path,
        scenarios=(task.id,),
        timeout_s=_DEFAULT_TASK_TIMEOUT_S,
    )
    result = tool_eval.run_tool_eval(request, runner=runner)
    if result.doc is None:
        outcome = "hang" if result.outcome is Outcome.HANG else "failed"
        return _failed_record(task, model, run_id, outcome, result.note or f"rc={result.rc}")

    suite_rec = tool_eval.parse_scores(result.doc)
    row = next((t for t in suite_rec.tasks if t.task_id == task.id), None)
    if row is None:
        return _failed_record(
            task,
            model,
            run_id,
            "failed",
            "scenario missing from tool-eval-bench output (unknown --scenarios id?)",
            suite_rec.tool_version,
        )

    return EvalRecord(
        run_id=run_id,
        suite="agentic",
        task_id=row.task_id,
        kind=row.kind or task.kind,
        model=model,
        outcome=row.outcome,
        score=row.score,
        correct=row.correct,
        expected=row.expected,
        answer=row.answer,
        checkpoints_hit=row.checkpoints_hit,
        checkpoints_total=row.checkpoints_total,
        metrics=_normalize_metrics(row.metrics, row.checkpoints_hit),
        note=row.note,
        tool_version=row.tool_version,
        scoring_era=row.scoring_era,
    )
