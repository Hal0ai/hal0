"""tool_eval.py — the tool-eval-bench adapter (Bench Phase 3, Track E).

Replaces ``evalrun.py``'s Hermes-driven eval tier with the pinned OSS
harness ``tool-eval-bench`` (tag ``v2.5.0``, git-only install, Python >=3.11,
offline/deterministic — see
``docs/superpowers/plans/2026-08-09-bench-phase3-oss-adapters.md``). Like
``harness.run_cell``, this module owns exactly three things: (1) building the
tool's argv for one eval request (:func:`build_argv`), (2) running it via an
injectable ``runner`` callable so tests never shell out
(:func:`run_tool_eval`), and (3) parsing its JSON envelope into a shape
compatible with ``evalrun.EvalRecord`` (:func:`parse_scores`). It does NOT
touch ``runner.py``/``planner.py``/``cli.py``/``evalrun.py`` — integration is
the orchestrator's follow-up once all Phase-3 tracks land.

Real CLI discovered by installing the pin into a scratch venv and driving it
directly (``python -m tool_eval_bench run --dry-run --json`` needs no server
and lists the real scenario vocabulary; a full run needs an OpenAI-compatible
``/v1/chat/completions`` endpoint, so the fixtures here were captured against
a minimal stdlib fake server — see ``tests/bench/adapters/capture_tool_eval.py``):

* Console entry point ``tool-eval-bench`` and ``python -m tool_eval_bench``
  are equivalent; this module invokes the module form (``-m tool_eval_bench``)
  against an explicit interpreter so a scratch/venv install never needs to be
  on ``$PATH``.
* ``run`` is the tool-call-scenario subcommand. ``--dry-run --json`` prints
  ``{"total_scenarios", "categories", "scenarios": [{"id","title","category",
  "difficulty"}, ...]}`` and needs no server or model at all — pinned v2.5.0
  lists 69 scenarios across categories A-O (84 with ``--hardmode``, category
  P). An unknown ``--scenarios ID`` filters down to ``total_scenarios: 0``
  with no error (the adapter's "missing task" case).
* A real ``run`` writes one JSON envelope (``--json-file PATH``, machine
  readable; stdout carries only JSONL progress events per scenario). Top
  level: ``schema_version``, ``tool_eval_bench_version`` (the setuptools-scm
  self-reported version — see :func:`parse_tool_version`), ``final_score``,
  ``rating``, ``status`` (``"completed"`` on a clean run), ``run_id``,
  ``config`` (resolved run config + ``config_fingerprint``), ``metadata``
  (host/model/tool_version — duplicates ``tool_eval_bench_version``),
  ``safety_gate``, ``report_path`` (a sibling Markdown report), and
  ``scores`` — the block this adapter actually parses:
  ``scores.scenario_results[]`` (one dict per scenario: ``scenario_id``,
  ``status`` in ``{"pass","partial","fail"}``, ``points`` in ``{0,1,2}``,
  ``summary``, ``failure_kind`` (only on fail; ``"timeout"``/
  ``"connection_error"``/``"server_error"`` are INFRASTRUCTURE failures per
  the tool's own taxonomy — excluded from its quality score, and mapped here
  to ``hang``/``failed`` rather than a scored miss), ``tool_calls_made``,
  ``duration_seconds``, ``turn_count``, ``ttft_ms``, ``prompt_tokens``,
  ``completion_tokens``). NOTE (needed extension, not made here):
  ``scenario_results[]`` does NOT carry the scenario's category letter — only
  the run-level ``scores.category_scores[]`` aggregate does. A per-scenario
  category needs a static ``--dry-run`` lookup (see
  ``tests/bench/adapters/fixtures/tool_eval/dry_run_all_scenarios.json``,
  captured real, 84 scenarios incl. ``--hardmode``) if the UI wants it;
  hardcoding that table here would silently drift from a future tool-eval-
  bench release that adds/renumbers scenarios, so :func:`parse_scores` leaves
  ``kind`` at whatever (if anything) the row itself reports.
* A connection failure never reaches ``scores`` at all: the tool does a
  pre-flight check, writes ONE JSONL line to stderr
  (``{"event":"error","error":"connection_failed",...}``), and exits 2 with
  NO ``--json-file`` written (captured real —
  ``tests/bench/adapters/fixtures/tool_eval/connection_error.stderr.jsonl``).
  :func:`run_tool_eval` classifies this as :attr:`Outcome.FAILED` from rc +
  the missing output file, not from parsing stderr as JSON.

Domain fact this adapter exists to enforce: tool-eval-bench's scoring was
hardened ~2026-08-03 (tool-call errors no longer earn fabricated-data
credit; stricter scenario contracts) — a score from before that boundary is
NOT comparable to one from after it. Every parsed record is stamped with
both the raw ``tool_version`` (parsed leniently — a setuptools-scm dev
version like ``2.5.1.dev11+g95e2b5021`` must never crash the parser, see
:func:`parse_tool_version`) and a derived ``scoring_era`` — the store's job
(not this module's) is to refuse to compare records across eras.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..schema import Outcome

__all__ = [
    "ERA_HARDENED",
    "ERA_PRE_HARDENING",
    "ERA_UNKNOWN",
    "MODULE",
    "SCORING_ERA_CUTOFF",
    "ToolEvalRequest",
    "ToolEvalRunResult",
    "ToolEvalSuiteRecord",
    "ToolEvalTaskRecord",
    "build_argv",
    "classify_scoring_era",
    "parse_scores",
    "parse_tool_version",
    "run_tool_eval",
]

#: ``python -m <MODULE> run ...`` — the module form works from any Python
#: that can import the pinned package, so the adapter never depends on the
#: ``tool-eval-bench`` console script being on ``$PATH`` (see module
#: docstring).
MODULE = "tool_eval_bench"

# The scoring-hardening boundary (~2026-08-03, see module docstring). The
# pin (v2.5.0) is tagged AFTER the hardening, so every record produced by a
# correctly-pinned adapter is "hardened" — this cutoff exists to catch a
# stale/pre-hardening install (or a future downgrade) rather than to
# distinguish normal operation.
SCORING_ERA_CUTOFF: tuple[int, ...] = (2, 5, 0)
ERA_HARDENED = "hardened-2026-08"
ERA_PRE_HARDENING = "pre-hardening-2026-08"
ERA_UNKNOWN = "unknown"

#: tool-eval-bench's own taxonomy (``domain.scenarios.INFRASTRUCTURE_FAILURE_
#: KINDS``) for failures caused by the serving environment rather than the
#: model. ``timeout`` maps to :attr:`Outcome.HANG` (something never came
#: back); ``connection_error``/``server_error`` map to
#: :attr:`Outcome.FAILED` (the endpoint was unreachable or errored, not a
#: model miss).
_INFRA_HANG_KINDS = frozenset({"timeout"})
_INFRA_FAILED_KINDS = frozenset({"connection_error", "server_error"})

_VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)")


def parse_tool_version(raw: str | None) -> tuple[int, ...]:
    """Leniently parse a setuptools-scm version string into a comparable
    release tuple. Handles a clean tag (``"2.5.0"`` -> ``(2, 5, 0)``) and a
    dev-style version off a commit past the tag (``"2.5.1.dev11+g95e2b5021"``
    -> ``(2, 5, 1)``, dropping the ``devN`` pre-release marker and the
    ``+g<sha>`` local segment). Never raises: an unparseable/empty string
    returns ``()``, which compares less than any real release tuple."""
    if not raw:
        return ()
    base = raw.split("+", 1)[0]  # drop the +g<sha> local version segment
    m = _VERSION_RE.match(base)
    if not m:
        return ()
    try:
        return tuple(int(part) for part in m.group(1).split("."))
    except ValueError:
        return ()


def classify_scoring_era(raw_version: str | None) -> str:
    """The era marker stamped on every parsed record (see module docstring).
    ``()`` (unparseable) is reported as :data:`ERA_UNKNOWN` rather than
    guessed as either era — an unrecognisable version string is exactly the
    case where silently assuming comparability would be wrong."""
    parsed = parse_tool_version(raw_version)
    if not parsed:
        return ERA_UNKNOWN
    return ERA_HARDENED if parsed >= SCORING_ERA_CUTOFF else ERA_PRE_HARDENING


# --------------------------------------------------------------------------- #
# request -> argv
# --------------------------------------------------------------------------- #


@dataclass
class ToolEvalRequest:
    """One eval invocation: the model endpoint + task selection + where to
    write machine-readable output. Mirrors the fields ``evalrun.run_task``
    threads through (``model``, an API base) plus tool-eval-bench's own
    scenario-selection surface."""

    python_exe: str
    base_url: str
    model: str
    output_path: Path
    backend: str = "llamacpp"
    api_key: str | None = None
    scenarios: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    short: bool = False
    trials: int = 1
    parallel: int = 1
    timeout_s: float = 120.0
    max_turns: int = 8
    seed: int | None = None
    temperature: float = 0.0
    error_rate: float = 0.0
    no_warmup: bool = True
    no_probe_engine: bool = True
    no_live: bool = True
    extra_args: tuple[str, ...] = ()


def build_argv(request: ToolEvalRequest) -> list[str]:
    """The exact argv for one deterministic, offline, machine-readable run:
    ``--json --json-file <path>`` keeps stdout to JSONL progress events only
    (real behaviour, see module docstring) and the file is the authoritative
    record this adapter parses. ``--no-warmup``/``--no-probe-engine`` default
    on so the request never depends on capabilities (a ``/metrics`` probe,
    warm-up round trip) a minimal fixture endpoint doesn't implement."""
    argv = [
        request.python_exe,
        "-m",
        MODULE,
        "run",
        "--model",
        request.model,
        "--backend",
        request.backend,
        "--base-url",
        request.base_url,
    ]
    if request.api_key:
        argv += ["--api-key", request.api_key]
    if request.scenarios:
        argv += ["--scenarios", *request.scenarios]
    if request.categories:
        argv += ["--categories", *request.categories]
    if request.short:
        argv.append("--short")
    argv += [
        "--trials",
        str(request.trials),
        "--parallel",
        str(request.parallel),
        "--timeout",
        str(request.timeout_s),
        "--max-turns",
        str(request.max_turns),
        "--temperature",
        str(request.temperature),
    ]
    if request.seed is not None:
        argv += ["--seed", str(request.seed)]
    if request.error_rate:
        argv += ["--error-rate", str(request.error_rate)]
    if request.no_warmup:
        argv.append("--no-warmup")
    if request.no_probe_engine:
        argv.append("--no-probe-engine")
    if request.no_live:
        argv.append("--no-live")
    argv += ["--json", "--json-file", str(request.output_path)]
    argv += list(request.extra_args)
    # Pin the tool's working data/output dir next to the json file: with no
    # --output-dir the tool mkdirs ./data relative to the CWD, and a service
    # CWD the hal0 user cannot write (found on-box 2026-08-09: sudo kept
    # CWD=/root -> PermissionError before any scenario ran).
    argv += ["--output-dir", str(Path(request.output_path).parent)]
    return argv


# --------------------------------------------------------------------------- #
# run — injectable runner, same pattern as harness.run_cell
# --------------------------------------------------------------------------- #


@dataclass
class ToolEvalRunResult:
    """One executed eval request: the classified :class:`Outcome`, the raw
    process result, and the parsed JSON envelope (``None`` on any failure to
    obtain one — never a partial/guessed doc)."""

    outcome: Outcome
    rc: int
    stdout: str
    stderr: str
    doc: dict[str, Any] | None
    note: str = ""


def _default_runner(argv: list[str], timeout_s: float) -> tuple[int, str, str]:
    """The bare subprocess runner. Production callers should inject a
    hardened callable with their own process-group watchdog (same rationale
    as ``harness._default_runner``); tests inject a fake that runs neither
    the tool nor a network."""
    import tempfile

    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout_s, cwd=tempfile.gettempdir()
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_tool_eval(
    request: ToolEvalRequest,
    runner: Callable[[list[str], float], tuple[int, str, str]] | None = None,
) -> ToolEvalRunResult:
    """Run one eval request through tool-eval-bench and classify the
    outcome. Never raises on a bad run — mirrors ``harness.run_cell`` /
    ``evalrun.run_task``: a timeout, a missing binary, a connection failure
    before the tool ever reaches ``scores``, or malformed JSON on disk are
    all a returned :class:`ToolEvalRunResult` with ``doc=None``, not an
    exception."""
    run = runner or _default_runner
    argv = build_argv(request)

    try:
        rc, stdout, stderr = run(argv, request.timeout_s)
    except subprocess.TimeoutExpired as exc:
        return ToolEvalRunResult(
            outcome=Outcome.HANG,
            rc=124,
            stdout=(exc.stdout if isinstance(exc.stdout, str) else "") or "",
            stderr=(exc.stderr if isinstance(exc.stderr, str) else "") or "",
            doc=None,
            note="tool-eval-bench timed out",
        )
    except OSError as exc:
        # No interpreter / package at that path — same "actionable message,
        # not a raw traceback" rule as evalrun.run_task's hermes_missing.
        return ToolEvalRunResult(
            outcome=Outcome.FAILED,
            rc=127,
            stdout="",
            stderr=str(exc),
            doc=None,
            note=f"cannot execute {argv[0]}: {exc}",
        )

    stdout = stdout or ""
    stderr = stderr or ""

    doc: dict[str, Any] | None = None
    if request.output_path.exists():
        try:
            loaded = json.loads(request.output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return ToolEvalRunResult(
                outcome=Outcome.FAILED,
                rc=rc,
                stdout=stdout,
                stderr=stderr,
                doc=None,
                note=f"malformed tool-eval-bench output: {exc}",
            )
        doc = loaded if isinstance(loaded, dict) else None

    if doc is None:
        # A pre-flight connection failure (real behaviour, see module
        # docstring): rc!=0, one JSONL error event on stderr, no file at all.
        tail = (stderr.strip() or stdout.strip())[-2000:]
        return ToolEvalRunResult(
            outcome=Outcome.FAILED,
            rc=rc,
            stdout=stdout,
            stderr=stderr,
            doc=None,
            note=tail or "tool-eval-bench produced no output file",
        )

    outcome = Outcome.OK if rc == 0 and doc.get("status") == "completed" else Outcome.FAILED
    note = "" if outcome is Outcome.OK else f"status={doc.get('status')!r} rc={rc}"
    return ToolEvalRunResult(
        outcome=outcome, rc=rc, stdout=stdout, stderr=stderr, doc=doc, note=note
    )


# --------------------------------------------------------------------------- #
# parse — JSON envelope -> the evalrun.EvalRecord-compatible shape
# --------------------------------------------------------------------------- #


@dataclass
class ToolEvalTaskRecord:
    """One scenario's result, in the field vocabulary
    ``evalrun.EvalRecord`` already uses (``task_id``/``outcome``/``score``/
    ``correct``/``checkpoints_hit``/``checkpoints_total``/``metrics``/
    ``note``/``schema``) so the integration step can drop this into the same
    store path. Fields with no exact evalrun analogue are filled with
    tool-eval-bench's nearest equivalent (documented per-field below) rather
    than left absent, so a v1 reader of the shape still gets a value:

    * ``expected``/``answer`` — evalrun derives a verifiable exact-match
      value; tool-eval-bench instead grades against a rubric, so these carry
      the scenario's human-readable ``expected_behavior`` and its scoring
      ``summary`` respectively (prose, not a value to string-compare).
    * ``checkpoints_hit``/``checkpoints_total`` — evalrun's checkpoints are
      hidden intermediate values scraped from a trace; tool-eval-bench's
      nearest signal is ``tool_calls_made`` (the tools the model actually
      invoked), used as-is with ``checkpoints_total`` left at 0 (no fixed
      denominator exists) rather than fabricated.
    """

    task_id: str
    kind: str
    outcome: str  # "ok" | "failed" | "hang" — evalrun.EvalRecord vocabulary
    score: float  # points/2, normalized to evalrun's 0.0-1.0 range
    correct: bool
    expected: str
    answer: str
    checkpoints_hit: list[str]
    checkpoints_total: int
    metrics: dict[str, Any]
    note: str = ""
    tool_version: str = ""
    scoring_era: str = ""
    schema: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolEvalSuiteRecord:
    """The whole run, one ``ToolEvalTaskRecord`` per scenario plus the
    run-level score tool-eval-bench itself computed. ``tool_version`` and
    ``scoring_era`` are stamped here AND on every task record (belt-and-
    braces: a store that reads task records standalone still sees the era)."""

    run_id: str
    model: str
    outcome: str
    final_score: int | None
    total_scenarios: int
    tasks: list[ToolEvalTaskRecord] = field(default_factory=list)
    tool_version: str = ""
    scoring_era: str = ""
    note: str = ""
    suite: str = "tool_eval_bench"
    schema: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _task_outcome(status: str, failure_kind: str | None) -> str:
    if failure_kind in _INFRA_HANG_KINDS:
        return "hang"
    if failure_kind in _INFRA_FAILED_KINDS:
        return "failed"
    return "ok" if status == "pass" else "failed"


def _parse_task(row: dict[str, Any], tool_version: str, scoring_era: str) -> ToolEvalTaskRecord:
    status = str(row.get("status") or "")
    points = row.get("points")
    score = round(points / 2, 3) if isinstance(points, int | float) else 0.0
    failure_kind = row.get("failure_kind")
    tool_calls_made = row.get("tool_calls_made")
    metrics = {
        "duration_seconds": row.get("duration_seconds"),
        "turn_count": row.get("turn_count"),
        "ttft_ms": row.get("ttft_ms"),
        "prompt_tokens": row.get("prompt_tokens"),
        "completion_tokens": row.get("completion_tokens"),
    }
    return ToolEvalTaskRecord(
        task_id=str(row.get("scenario_id") or ""),
        # No per-scenario category in scores.scenario_results[] as of the
        # pin (see module docstring) — pass through if a future tool version
        # adds one, else leave unknown rather than guess.
        kind=str(row.get("category") or ""),
        outcome=_task_outcome(status, failure_kind),
        score=score,
        correct=status == "pass",
        expected=str(row.get("expected_behavior") or ""),
        answer=str(row.get("summary") or ""),
        checkpoints_hit=list(tool_calls_made) if isinstance(tool_calls_made, list) else [],
        checkpoints_total=0,
        metrics=metrics,
        note=str(failure_kind or ""),
        tool_version=tool_version,
        scoring_era=scoring_era,
    )


def parse_scores(doc: dict[str, Any]) -> ToolEvalSuiteRecord:
    """Pure parser: the tool-eval-bench JSON envelope -> the evalrun-
    compatible record shape (DESIGN parity with ``parsers.py``'s pure
    functions — no filesystem, no network, unit-tested directly against the
    captured fixtures in ``tests/bench/adapters/fixtures/tool_eval/``).
    Never invents a value: a field the tool did not report stays empty/0/None,
    matching ``parsers.py``'s "never a fabricated value" rule."""
    raw_version = doc.get("tool_eval_bench_version") or (doc.get("metadata") or {}).get(
        "tool_version"
    )
    tool_version = str(raw_version) if raw_version else ""
    scoring_era = classify_scoring_era(tool_version)

    scores = doc.get("scores") if isinstance(doc.get("scores"), dict) else {}
    scenario_results = scores.get("scenario_results")
    rows = scenario_results if isinstance(scenario_results, list) else []
    tasks = [_parse_task(row, tool_version, scoring_era) for row in rows if isinstance(row, dict)]

    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    config = doc.get("config") if isinstance(doc.get("config"), dict) else {}
    model = str(metadata.get("model") or config.get("model") or "")

    status = str(doc.get("status") or "")
    final_score = doc.get("final_score")
    total_scenarios = doc.get("total_scenarios")

    return ToolEvalSuiteRecord(
        run_id=str(doc.get("run_id") or ""),
        model=model,
        outcome="ok" if status == "completed" else "failed",
        final_score=final_score if isinstance(final_score, int) else None,
        total_scenarios=total_scenarios if isinstance(total_scenarios, int) else len(tasks),
        tasks=tasks,
        tool_version=tool_version,
        scoring_era=scoring_era,
        note="" if status == "completed" else f"status={status!r}",
    )
