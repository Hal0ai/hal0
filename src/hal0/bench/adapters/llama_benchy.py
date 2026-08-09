"""llama_benchy.py — adapter for eugr/llama-benchy (Bench Phase 3, Track B).

llama-benchy speaks llama-bench's pp/tg vocabulary (``--pp``/``--tg``/``--depth``)
but drives it over an OpenAI-compatible HTTP endpoint instead of an in-process
llama-bench binary — so it measures a live ``llama-server`` slot the same way
``server_ab.py`` does, but with llama-bench's simpler prompt-processing /
token-generation x depth axis instead of chat-completion sessions. It fills the
gap between ``parsers.parse_llama_bench`` (Tier A, podman-exec'd llama-bench
binary) and ``parsers.parse_server_ab`` (Tier B, chat/reuse/embed/rerank):
pp/tg x depth measured THROUGH the HTTP seam, no podman/GPU-device plumbing
needed at all — the endpoint is just a slot's already-running OpenAI API.

Pin: tag v0.4.0 = sha 446dd42fde2ebbaa1d68a0dfe9dc1e5b833f95ad (git-only
install; MIT). Verified against the ACTUAL installed CLI (``--help``) and the
upstream formal JSON schema (``schemas/benchmark_report_schema.json`` at that
sha), not assumed — see ``tests/bench/adapters/capture_llama_benchy.py`` for
how the fixtures under ``tests/bench/adapters/fixtures/llama_benchy/`` were
captured from the REAL pinned tool against a stdlib fake OpenAI endpoint.

Architecture (Bench Phase 3 plan, "Architecture" section): this module owns
exactly three things — (1) :func:`build_argv`, the tool's CLI for one cell-
shaped request; (2) :func:`run_llama_benchy`, running it via an injectable
``runner`` callable (nothing here ever shells out in a test); (3)
:func:`parse_benchy`, turning its output into the same ``parsers.Parsed``
shape the rest of the pipeline already knows how to fold into a
:class:`~hal0.bench.schema.Record` via ``runner._assemble``. It does not touch
``runner.py``/``planner.py``/``cli.py``/``pyproject.toml`` — integration
(wiring a new cell kind to this adapter) is the orchestrator's follow-up.

Why one llama-benchy invocation covers BOTH pp and tg for a depth: unlike
llama-bench (which emits one JSON row per pp-only or tg-only test), a single
llama-benchy ``BenchmarkRun`` row carries pp_throughput AND tg_throughput for
the SAME prompt/response/depth/concurrency combination (it always runs a
prefill-then-generate exchange per rep) — see the schema's ``BenchmarkRun``
type. :func:`parse_benchy` is therefore called twice per cell-pair (once with
``kind="pp"``, once with ``kind="tg"``) against the SAME parsed doc/row, the
same way a caller reads two different fields off one measurement rather than
locating two different rows.

Engine provenance: ``kind="llama-benchy"`` (the adapter tool name — see
``schema.Engine.kind``'s docstring, which explicitly allows an adapter tool
name alongside ``"llama-bench"``/``"llama-server"``) and ``tool_version`` is
the tool's own self-reported version string, stamped verbatim.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import statistics
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..parsers import Parsed
from ..schema import Config, Engine, Outcome, Rep, Summary

__all__ = [
    "LLAMA_BENCHY_BIN",
    "BenchyRunResult",
    "LlamaBenchyRequest",
    "build_argv",
    "parse_benchy",
    "run_llama_benchy",
]

#: The console-script entry point name (``[project.scripts]`` in the tool's
#: own ``pyproject.toml``), resolved via PATH like every other engine binary
#: this codebase shells out to (``podman``, ``sudo``) — never hardcoded to an
#: absolute venv path, so the orchestrator's dependency wiring controls which
#: interpreter/venv actually provides it.
LLAMA_BENCHY_BIN = "llama-benchy"


@dataclass(frozen=True)
class LlamaBenchyRequest:
    """One cell-shaped request: an already-running OpenAI-compatible slot,
    the pp/tg sizes and depths to sweep, and where the tool's JSON report
    lands. ``result_path`` is REQUIRED (not a tempfile the adapter invents)
    for the same reason ``harness.run_cell`` takes an explicit ``log_path`` —
    the caller owns the run's on-disk footprint and cleanup.
    """

    endpoint: str  # OpenAI-compatible base URL, e.g. "http://127.0.0.1:8080/v1"
    model: str  # model label; sent as both --model and --served-model-name
    pp: int
    tg: int
    result_path: Path
    depths: Sequence[int] = (0,)
    reps: int = 3
    api_key: str = "EMPTY"  # the tool's own CLI default (config.py)
    tokenizer: str | None = None
    book_url: str | None = None  # None -> let the tool use its own default
    exact_tg: bool = False
    timeout_s: float | None = None
    extra_args: tuple[str, ...] = ()


def build_argv(request: LlamaBenchyRequest) -> list[str]:
    """The exact ``llama-benchy`` argv for one request (verified against the
    v0.4.0 ``--help`` — see module docstring for the capture provenance).

    Flags chosen deliberately:
      * ``--no-warmup --no-adapt-prompt`` — the INSTALLED v0.4.0 CLI forces a
        warmup exchange whenever ``--no-adapt-prompt`` is absent (its
        ``adapt_prompt`` default is ``True`` and ``run_suite`` ORs it into
        ``should_warmup`` regardless of ``--no-warmup``); passing both is the
        only combination that actually skips it, confirmed by a real capture
        run showing "Warming up..." was NOT emitted only with both flags set.
      * ``--skip-coherence`` — the coherence probe gates the whole run on the
        model replying "Paris" verbatim; that is a correctness check
        (tool-eval-bench's job), not this adapter's throughput measurement,
        so a slow/quantized model failing coherence must never turn a valid
        perf run into a hard failure here.
      * ``--latency-mode none`` — the separate GET-based latency probe
        answers a different question (bare connection RTT) than pp/tg
        throughput; skip it rather than report a number nobody asked for.
      * ``--format json --save-result <result_path>`` — machine-readable
        output at a KNOWN path (deliverable #1): with no ``--save-result``
        the tool interleaves the JSON with human-readable banner lines on
        stdout (``results.py`` ``save_report``), which is not a stable
        parse target; the file is.
    """
    argv = [
        LLAMA_BENCHY_BIN,
        "--base-url",
        request.endpoint,
        "--api-key",
        request.api_key,
        "--model",
        request.model,
        "--served-model-name",
        request.model,
        "--pp",
        str(request.pp),
        "--tg",
        str(request.tg),
        "--depth",
        *[str(d) for d in request.depths],
        "--runs",
        str(request.reps),
        "--no-warmup",
        "--no-adapt-prompt",
        "--skip-coherence",
        "--latency-mode",
        "none",
        "--format",
        "json",
        "--save-result",
        str(request.result_path),
    ]
    if request.tokenizer:
        argv += ["--tokenizer", request.tokenizer]
    if request.book_url:
        argv += ["--book-url", request.book_url]
    if request.exact_tg:
        argv.append("--exact-tg")
    argv += list(request.extra_args)
    return argv


@dataclass
class BenchyRunResult:
    """One executed llama-benchy invocation: the parsed report doc (``None``
    on any failure — never a partial/guessed value), the process's exit code,
    its last 4000 chars of combined output for a failed record's ``note``,
    and the classified :class:`~hal0.bench.schema.Outcome`."""

    doc: dict[str, Any] | None
    rc: int
    tail: str
    outcome: Outcome


def _default_runner(argv: list[str], timeout_s: float | None) -> tuple[int, str, str]:
    """Bare subprocess runner with a process-group watchdog, mirroring
    ``runner.py``'s ``_run_subprocess``: the child gets its own session so a
    timeout kills the whole process group (llama-benchy's own asyncio event
    loop and any child it spawns), not just the immediate PID. Returns
    ``(-9, stdout_so_far, "watchdog-timeout")`` on a timeout — never raises,
    so :func:`run_llama_benchy` can classify it as :attr:`Outcome.HANG`
    without a try/except at the call site.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return proc.returncode, out or "", err or ""
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(proc.pid, sig)
            with contextlib.suppress(subprocess.TimeoutExpired):
                out, err = proc.communicate(timeout=10)
                return -9, out or "", "watchdog-timeout"
        return -9, "", "watchdog-timeout"


def _classify(rc: int, doc: dict[str, Any] | None) -> Outcome:
    """(timeout / nonzero / empty) classification, same shape as
    ``runner._classify``: a watchdog timeout is always :attr:`Outcome.HANG`;
    any other nonzero exit (connection refused, warmup failure, coherence
    failure — all observed via real capture, see
    ``tests/bench/adapters/capture_llama_benchy.py``) is :attr:`Outcome.FAILED`;
    a zero exit whose report is missing/unreadable or carries an EMPTY
    ``benchmarks`` array is ALSO :attr:`Outcome.FAILED` — a report with no
    rows measured nothing, regardless of what the process exit code claims."""
    if rc == -9:
        return Outcome.HANG
    if rc != 0:
        return Outcome.FAILED
    if not doc or not doc.get("benchmarks"):
        return Outcome.FAILED
    return Outcome.OK


def run_llama_benchy(
    request: LlamaBenchyRequest,
    runner: Callable[[list[str], float | None], tuple[int, str, str]] | None = None,
) -> BenchyRunResult:
    """Run one llama-benchy invocation through the injectable ``runner``
    (tests inject a fake — nothing here shells out in a test), read back its
    ``--save-result`` JSON, and classify the outcome. Never raises on a
    failed run — a bad run is a :class:`BenchyRunResult` with ``doc=None``
    and a non-OK outcome, so the caller's session can record it and
    continue, exactly like ``harness.run_cell``.
    """
    run = runner or _default_runner
    argv = build_argv(request)
    rc, stdout, stderr = run(argv, request.timeout_s)
    tail_source = stderr if stderr.strip() else stdout
    tail = tail_source[-4000:]

    doc: dict[str, Any] | None = None
    if rc == 0:
        with contextlib.suppress(OSError, json.JSONDecodeError):
            doc = json.loads(request.result_path.read_text(encoding="utf-8"))

    return BenchyRunResult(doc=doc, rc=rc, tail=tail, outcome=_classify(rc, doc))


# --------------------------------------------------------------------------- #
# Parsing — doc -> parsers.Parsed, depth-aware.
# --------------------------------------------------------------------------- #


def _med(values: Sequence[float]) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(vals), 4) if vals else None


def _stdev(values: Sequence[float]) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.stdev(vals), 4) if len(vals) >= 2 else None


def _p95(values: Sequence[float]) -> float | None:
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    idx = min(len(vals) - 1, max(0, round(0.95 * (len(vals) - 1))))
    return round(vals[idx], 4)


def _select_row(benchmarks: list[dict[str, Any]], depth: int | None) -> dict[str, Any] | None:
    """Pick the ``BenchmarkRun`` for this cell's depth. One llama-benchy
    invocation can sweep several depths (``--depth 0 128 ...``); prefer an
    exact ``context_size`` match, else the LAST (newest) row — same
    precedence as ``parsers._select_row`` for llama-bench rows."""
    if not benchmarks:
        return None
    if depth is not None:
        exact = [b for b in benchmarks if (b.get("context_size") or 0) == depth]
        if exact:
            return exact[-1]
    return benchmarks[-1]


def _metric_values(row: dict[str, Any], key: str) -> list[float]:
    metric = row.get(key)
    if not isinstance(metric, dict):
        return []
    return [v for v in (metric.get("values") or []) if isinstance(v, (int, float))]


def _ttft_values(row: dict[str, Any]) -> list[float]:
    """End-to-end TTFT when present, else the "Time to First Response" proxy
    (both are ``BenchmarkMetric`` blocks of ms values per rep) — mirrors
    ``parsers._sa_run_to_rep``'s "prompt_ms as TTFT proxy" fallback."""
    vals = _metric_values(row, "e2e_ttft")
    return vals if vals else _metric_values(row, "ttfr")


def _resolved_config_from_row(row: dict[str, Any]) -> Config:
    """DISPLAY-only resolved config for the drawer — the cell shape actually
    measured (prompt/response sizes, depth, concurrency), NOT llama.cpp
    flags (llama-benchy drives an HTTP endpoint; there is no local argv to
    observe). Never feeds cell_key (see ``schema.py``'s module docstring)."""
    argv = [
        "--pp",
        str(row.get("prompt_size", "")),
        "--tg",
        str(row.get("response_size", "")),
        "--depth",
        str(row.get("context_size", 0)),
    ]
    return Config(argv=argv, env={}, kv={}, ctx=int(row.get("context_size") or 0))


def parse_benchy(doc: dict[str, Any] | None, kind: str, depth: int | None = None) -> Parsed:
    """Parse one llama-benchy report doc into a cell's results (schema-2
    shape). ``kind`` is "pp" or "tg" — see module docstring for why ONE row
    answers both. ``reps[]`` is built from the row's per-rep ``values``
    arrays (``pp_throughput``/``tg_throughput`` — never the tool's own
    mean/std alone, so nothing measured is discarded, same rule as
    ``parsers.parse_llama_bench``)."""
    if kind not in ("pp", "tg"):
        raise ValueError(f"parse_benchy: kind must be 'pp' or 'tg', got {kind!r}")

    benchmarks = (doc or {}).get("benchmarks") or []
    row = _select_row(benchmarks, depth) or {}
    is_pp = kind == "pp"

    throughput_key = "pp_throughput" if is_pp else "tg_throughput"
    tp_values = _metric_values(row, throughput_key)
    ttft_values = _ttft_values(row)
    size_key = "prompt_size" if is_pp else "response_size"
    n_tokens = row.get(size_key) or 0

    reps: list[Rep] = []
    for i, tps in enumerate(tp_values):
        # tokens / (tokens/sec) -> seconds; a legitimate derived duration
        # from the tool's own reported rate, not a fabricated value (the
        # tool itself never reports per-rep wall-clock directly).
        t_s = round(n_tokens / tps, 4) if tps else None
        ttft_ms = ttft_values[i] if i < len(ttft_values) else None
        timings_raw: dict[str, Any] = {"sample_index": i, throughput_key: tps}
        if ttft_ms is not None:
            timings_raw["ttft_ms"] = ttft_ms
        reps.append(
            Rep(
                t_s=t_s,
                prefill_ts=tps if is_pp else None,
                decode_ts=None if is_pp else tps,
                ttft_ms=ttft_ms,
                timings_raw=timings_raw,
            )
        )

    summary = Summary()
    if tp_values:
        med = _med(tp_values)
        if is_pp:
            summary.prefill_ts_med = med
        else:
            summary.decode_ts_med = med
            # prefer the tool's own reported std; fall back to computing it.
            metric = row.get(throughput_key) or {}
            std = metric.get("std")
            summary.decode_ts_stddev = (
                round(std, 4) if isinstance(std, (int, float)) else _stdev(tp_values)
            )
    if ttft_values:
        summary.ttft_ms_p50 = _med(ttft_values)
        summary.ttft_ms_p95 = _p95(ttft_values)

    version = (doc or {}).get("version", "") if doc else ""
    engine = Engine(kind="llama-benchy", image="", tool_version=str(version or ""))
    return Parsed(
        reps=reps,
        summary=summary,
        engine_observed=engine,
        config_observed=_resolved_config_from_row(row) if row else None,
    )
