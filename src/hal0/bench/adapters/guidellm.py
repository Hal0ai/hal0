"""guidellm.py — GuideLLM adapter for the serving-perf tier (bench Phase 3,
Track G, docs/superpowers/plans/2026-08-09-bench-phase3-oss-adapters.md).

GuideLLM (Apache-2.0, pinned ``guidellm==0.7.3``) replaces ``server_ab.py``'s
``ab`` mode for HTTP chat-style load: real TTFT/ITL/TPOT distributions and a
proper sweep-concurrency profile, instead of ``server_ab``'s hand-rolled
sequential N-timed-calls loop.

Verified CLI shape (installed the pinned wheel into a scratch venv and read
``guidellm run --help`` plus the ``ProfileArgs``/``ConstraintArgs`` pydantic
registries under ``guidellm.benchmark.profiles`` / ``guidellm.scheduler.
constraints`` on 2026-08-09 — the plan's own pin notes flagged a prior
self-disagreement over the entry point, so this was re-verified against the
real 0.7.3 wheel, not assumed):

    guidellm run
        --backend "kind=openai_http,target=<url>,model=<id>"
        --profile "kind=<sweep|constant|concurrent|synchronous|throughput>[,<opts>]"
        --data "kind=synthetic_text,prompt_tokens=<n>,output_tokens=<n>"
        --tokenizer "kind=huggingface_auto,model=<tokenizer>"
        [--constraint "kind=max_requests,count=<n>"]
        [--constraint "kind=max_duration,seconds=<n>"]
        --output "kind=json,path=<path>"
        --disable-console

``guidellm run`` is a single flat command (NOT ``guidellm bench run`` or a
scenario-file-only invocation) — the sub-options after ``kind=`` are a
comma-separated ``key=value`` list parsed by GuideLLM's own click+pydantic
CLI layer (``guidellm.utils.click_pydantic``), confirmed by successfully
running all five profile kinds against a live mock server (see
``capture_guidellm.py``). Constraint field names are NOT what the plan's own
notes guessed: ``max_requests`` takes ``count=``, ``max_duration`` takes
``seconds=`` (not ``max-requests``/``max-seconds`` as bare flags — those
strings from the task brief are the CONCEPTS, not the literal option names).
At least one constraint is required in practice (an unconstrained run has no
stopping condition), so :func:`build_argv` raises if neither is given.

``guidellm run`` always needs a tokenizer to build its synthetic dataset (it
downloads one from Hugging Face by model id if ``--tokenizer`` is omitted,
which fails hard for a local-only slot id like ``"my-gguf-model"`` — see
``capture_guidellm.py``'s capture log). Callers should pass
``GuidellmRequest.tokenizer`` as a real HF repo id (e.g. the model's base
architecture's tokenizer) when the slot's model id itself is not one.

Output: ``guidellm`` writes ONE JSON document to ``--output``'s ``path=``
(``{"metadata": ..., "config": {"spec": {...}}, "benchmarks": [...]}``).
Every profile except ``sweep`` produces exactly one ``benchmarks[]`` entry;
``sweep`` produces several (one per adaptively-chosen load point), each
tagged with its OWN concrete strategy type (``synchronous``/``throughput``/
``constant``/``poisson``) rather than the literal string ``"sweep"`` — see
:func:`_select_entries`.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..parsers import Parsed
from ..schema import Config, Engine, Outcome, Rep, Summary

__all__ = [
    "GUIDELLM_BIN",
    "PROFILE_KINDS",
    "GuidellmRequest",
    "GuidellmResult",
    "build_argv",
    "parse_benchmarks",
    "run_guidellm",
]

#: Overridable like ``runner.SERVER_AB`` / ``harness.BENCHCTL`` — tests
#: monkeypatch this to a fake script on ``PATH``, production leaves it as the
#: bare command name (guidellm installs a console-script entry point).
GUIDELLM_BIN = "guidellm"

#: The profile kinds the plan pins (DESIGN pins table): GuideLLM also ships
#: ``poisson``/``async``/``replay``, but those are out of scope here.
PROFILE_KINDS = frozenset({"sweep", "constant", "concurrent", "synchronous", "throughput"})


# --------------------------------------------------------------------------- #
# Request / argv
# --------------------------------------------------------------------------- #


@dataclass
class GuidellmRequest:
    """A cell-shaped request for one GuideLLM run: an openai-compatible
    endpoint + model id, a load profile, and a stopping constraint.

    ``profile_options`` carries the profile kind's own sub-keys, comma-joined
    onto ``--profile kind=<kind>`` in the order given:
      * ``constant``/``poisson``: ``rate`` (requests/sec)
      * ``concurrent``: ``streams`` (int, or a list rendered ``1,2,4``)
      * ``throughput``: ``max_concurrency`` (optional)
      * ``sweep``: ``sweep_size``, ``strategy_type``, ``max_concurrency``
      * ``synchronous``: none (single stream, no options)
    """

    endpoint: str
    model: str
    profile_kind: str
    output_path: str
    profile_options: dict[str, Any] = field(default_factory=dict)
    max_requests: int | None = None
    max_seconds: float | None = None
    tokenizer: str | None = None  # defaults to `model` if unset
    prompt_tokens: int = 512
    output_tokens: int = 128


def _profile_opt(request: GuidellmRequest) -> str:
    parts = [f"kind={request.profile_kind}"]
    for key, value in request.profile_options.items():
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        parts.append(f"{key}={value}")
    return ",".join(parts)


def build_argv(request: GuidellmRequest) -> list[str]:
    """The exact ``guidellm run`` argv for one cell (module docstring).

    Raises ``ValueError`` on a request that cannot produce a valid invocation:
    an unknown profile kind, no output path, or neither stopping constraint
    (an unconstrained load-gen run never terminates on its own)."""
    if request.profile_kind not in PROFILE_KINDS:
        raise ValueError(
            f"unknown guidellm profile kind {request.profile_kind!r} "
            f"(expected one of {sorted(PROFILE_KINDS)})"
        )
    if not request.output_path:
        raise ValueError("GuidellmRequest.output_path is required")
    if request.max_requests is None and request.max_seconds is None:
        raise ValueError("GuidellmRequest needs max_requests and/or max_seconds")

    argv = [
        GUIDELLM_BIN,
        "run",
        "--backend",
        f"kind=openai_http,target={request.endpoint},model={request.model}",
        "--profile",
        _profile_opt(request),
        "--data",
        f"kind=synthetic_text,prompt_tokens={request.prompt_tokens},"
        f"output_tokens={request.output_tokens}",
        "--tokenizer",
        f"kind=huggingface_auto,model={request.tokenizer or request.model}",
    ]
    if request.max_requests is not None:
        argv += ["--constraint", f"kind=max_requests,count={request.max_requests}"]
    if request.max_seconds is not None:
        argv += ["--constraint", f"kind=max_duration,seconds={request.max_seconds}"]
    argv += ["--output", f"kind=json,path={request.output_path}", "--disable-console"]
    return argv


# --------------------------------------------------------------------------- #
# Execution — injectable runner (same pattern as harness.run_cell)
# --------------------------------------------------------------------------- #


@dataclass
class GuidellmResult:
    """One executed guidellm run: its classified outcome, the parsed
    ``benchmarks.json`` document (``None`` on any failure — never a partial or
    guessed doc), the final exit code, and a log tail for the record note."""

    outcome: Outcome
    doc: dict[str, Any] | None
    rc: int
    tail: str
    argv: list[str]


def _default_runner(argv: list[str], timeout_s: float | None) -> tuple[int, str, str]:
    """Bare subprocess execution with a process-group watchdog: unlike the
    seam-fronted Tier-A path, GuideLLM has no privileged wrapper enforcing its
    own timeout, so THIS is where a hang gets killed. Own process group so a
    timeout kills guidellm's whole worker pool, not just the parent (mirrors
    ``runner._run_subprocess``). Tests inject a fake that needs no real
    ``guidellm`` on PATH at all."""
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
            try:
                out, err = proc.communicate(timeout=10)
                break
            except subprocess.TimeoutExpired:
                out, err = "", ""
        else:
            out, err = "", ""
        return -9, out, (err or "") + "\n[watchdog] killed after timeout"


def _classify(rc: int, tail: str) -> Outcome:
    """Outcome vocabulary matches ``runner._classify`` exactly (schema.
    Outcome, DESIGN §3.2): rc==-9 is the watchdog's own timeout sentinel ->
    HANG; an OOM string in the tail -> OOM (GuideLLM is an HTTP client, so
    this fires only if the SERVER's error surfaces in guidellm's own stderr,
    e.g. a 500 body it logs); any other non-zero rc -> FAILED; else OK."""
    if rc == -9:
        return Outcome.HANG
    low = tail.lower()
    if "out of memory" in low or "hiperroroutofmemory" in low.replace("_", ""):
        return Outcome.OOM
    if rc != 0:
        return Outcome.FAILED
    return Outcome.OK


def _read_output(path: str) -> dict[str, Any] | None:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def run_guidellm(
    request: GuidellmRequest,
    runner: Callable[[list[str], float | None], tuple[int, str, str]] | None = None,
    *,
    timeout_s: float | None = None,
) -> GuidellmResult:
    """Run one GuideLLM cell through the injectable ``runner`` (same pattern
    as ``harness.run_cell``: production supplies a hardened subprocess
    callable, tests inject a fake and never shell out to the real tool).

    A zero exit with an unreadable/missing ``--output`` file is downgraded to
    FAILED (mirrors ``runner._tier_a_record``'s "ran ok but no output" rule)
    — a clean exit code alone is never enough to call a cell OK."""
    argv = build_argv(request)
    run = runner or _default_runner
    rc, stdout, stderr = run(argv, timeout_s)
    tail_source = stderr if stderr.strip() else stdout
    tail = tail_source[-4000:]
    outcome = _classify(rc, tail)

    doc: dict[str, Any] | None = None
    if outcome is Outcome.OK:
        doc = _read_output(request.output_path)
        if doc is None:
            outcome = Outcome.FAILED
            tail = (tail + "\n[adapter] exit 0 but --output produced no valid JSON").strip()

    return GuidellmResult(outcome=outcome, doc=doc, rc=rc, tail=tail, argv=argv)


# --------------------------------------------------------------------------- #
# Parsing — benchmarks.json -> Parsed (parsers.py's pure result shape)
# --------------------------------------------------------------------------- #


def _select_entries(doc: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Pick the ``benchmarks[]`` entries this cell's ``kind`` describes.

    ``"sweep"``: every entry (module docstring — a sweep's whole point is the
    distribution across load points, so folding every entry's requests into
    one cell's ``reps[]`` is the only way not to average that away).

    Any other kind (``constant``/``synchronous``/``concurrent``/
    ``throughput``): the entries whose OWN ``config.strategy.type_`` matches
    (those profiles produce exactly one entry per run in practice). Falls
    back to the first entry if nothing matches rather than returning
    nothing — a bad/mismatched ``kind`` argument is a caller bug, not a
    reason to throw away real, present data (same never-invent-but-never-
    silently-drop precedent as ``parse_llama_bench``'s ``_select_row``)."""
    benchmarks = doc.get("benchmarks") or []
    if not benchmarks:
        return []
    if kind == "sweep":
        return [b for b in benchmarks if isinstance(b, dict)]
    matches = [
        b
        for b in benchmarks
        if isinstance(b, dict)
        and ((b.get("config") or {}).get("strategy") or {}).get("type_") == kind
    ]
    if matches:
        return matches
    first = benchmarks[0]
    return [first] if isinstance(first, dict) else []


def _successful_stat(metrics: dict[str, Any], field_name: str) -> dict[str, Any] | None:
    stat = ((metrics.get(field_name) or {}).get("successful")) or None
    return stat if isinstance(stat, dict) else None


def _percentile(stat: dict[str, Any] | None, key: str) -> float | None:
    if not stat:
        return None
    value = (stat.get("percentiles") or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _rep_from_request(req: dict[str, Any]) -> Rep | None:
    """One successful GuideLLM request -> a Rep. Errored/incomplete requests
    are dropped (no throughput to report), same rule as
    ``parsers._sa_run_to_rep``. ``timings_raw`` keeps guidellm's ENTIRE
    per-request stat dict (TTFT/ITL/TPOT/tokens/etc.) verbatim — this is the
    schema-2 "preserve raw distributions" seam: no per-rep field this adapter
    doesn't have a schema slot for (ITL, TPOT, token counts) is discarded,
    it's just not lifted into a named ``Rep`` attribute."""
    if not isinstance(req, dict):
        return None
    return Rep(
        t_s=req.get("request_latency"),
        ttft_ms=req.get("time_to_first_token_ms"),
        decode_ts=req.get("output_tokens_per_second"),
        timings_raw=req,
    )


def _config_observed(doc: dict[str, Any], entries: list[dict[str, Any]]) -> Config | None:
    """DISPLAY-only resolved config (parsers.py convention: ``Parsed.
    config_observed`` is stamped onto the record for viewing, never fed to
    ``cell_key``). Reconstructs a readable ``--backend``/``--profile`` argv
    fragment from the doc's own spec, plus the LAST entry's actually-resolved
    strategy (rate/streams/concurrency) as ``kv`` — the real observed load
    point, not just what was requested."""
    spec = ((doc.get("config") or {}).get("spec")) or {}
    backend = spec.get("backend") or {}
    profile = spec.get("profile") or {}
    argv: list[str] = []
    if backend.get("target"):
        argv += [
            "--backend",
            f"kind=openai_http,target={backend['target']},model={backend.get('model', '')}",
        ]
    if profile.get("kind"):
        argv += ["--profile", f"kind={profile['kind']}"]

    kv: dict[str, str] = {}
    strategy = (entries[-1].get("config") or {}).get("strategy") if entries else None
    if isinstance(strategy, dict):
        if strategy.get("type_"):
            kv["strategy_type"] = str(strategy["type_"])
        for key in ("rate", "streams", "max_concurrency", "worker_count"):
            if strategy.get(key) is not None:
                kv[key] = str(strategy[key])

    if not argv and not kv:
        return None
    return Config(argv=argv, kv=kv)


def parse_benchmarks(doc: dict[str, Any], kind: str) -> Parsed:
    """Parse a GuideLLM ``benchmarks.json`` document into one cell's results
    (module docstring; ``Parsed`` is ``parsers.py``'s pure result shape).

    Summary carries only what ``schema.Summary`` has slots for today
    (decode t/s med+stddev from ``output_tokens_per_second``, prefill t/s med
    from ``prompt_tokens_per_second``, TTFT p50/p95) — GuideLLM's own p99 and
    its ITL/TPOT percentiles have nowhere typed to go without a schema
    change (reported to the orchestrator rather than hacked into an
    unrelated field). Nothing is lost in the meantime: every successful
    request's FULL raw stat dict rides in ``reps[].timings_raw``, so any
    percentile is one computation away from the reps array — this is the
    "preserve raw distributions" contract, satisfied at the rep level even
    where the aggregate Summary can't hold it."""
    entries = _select_entries(doc, kind)
    if not entries:
        return Parsed(engine_observed=Engine(kind="guidellm"))

    reps: list[Rep] = []
    for entry in entries:
        successful = (entry.get("requests") or {}).get("successful") or []
        reps.extend(rep for rep in (_rep_from_request(r) for r in successful) if rep is not None)

    metrics = entries[-1].get("metrics") or {}
    summary = Summary()
    decode_stat = _successful_stat(metrics, "output_tokens_per_second")
    prefill_stat = _successful_stat(metrics, "prompt_tokens_per_second")
    ttft_stat = _successful_stat(metrics, "time_to_first_token_ms")
    if decode_stat:
        med = decode_stat.get("median")
        summary.decode_ts_med = float(med) if isinstance(med, (int, float)) else None
        stddev = decode_stat.get("std_dev")
        summary.decode_ts_stddev = float(stddev) if isinstance(stddev, (int, float)) else None
    if prefill_stat:
        med = prefill_stat.get("median")
        summary.prefill_ts_med = float(med) if isinstance(med, (int, float)) else None
    if ttft_stat:
        summary.ttft_ms_p50 = _percentile(ttft_stat, "p50")
        summary.ttft_ms_p95 = _percentile(ttft_stat, "p95")

    engine = Engine(
        kind="guidellm",
        tool_version=str((doc.get("metadata") or {}).get("guidellm_version") or ""),
    )
    return Parsed(
        reps=reps,
        summary=summary,
        engine_observed=engine,
        config_observed=_config_observed(doc, entries),
    )
