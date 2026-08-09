"""parsers.py — engine output → schema-2 results (DESIGN §3.2, the P2 parsers).

These are the two "# P2:" parsers the runner's control flow was stubbed around,
implemented against REAL on-box output (fixtures in tests/fixtures/ captured from
this box's llama-bench and server_ab.py). They are kept here as PURE functions of
already-loaded JSON — no filesystem, no network — so they unit-test directly
against the captured fixtures and the runner is a thin caller.

Two tiers (DESIGN §1):
  * Tier A — ``parse_llama_bench``: ``harness.run_cell`` captures llama-bench's
    ``-o json`` ARRAY (one row per pp/tg test) from the seam's stdout plus the
    provenance dict it builds alongside (the v1 ``.meta.json`` shape). Each row carries per-rep ``samples_ts`` /
    ``samples_ns`` arrays — so ``reps[]`` is the raw per-repetition throughput,
    not just the median (the whole point of schema v2).
  * Tier B — ``parse_server_ab``: server_ab.py writes one session JSON whose
    ``results`` shape depends on ``mode`` (ab/reuse → per-variant ``runs``;
    embed/rerank → ``latency_s``). Per-run llama-server timings → ``reps[]``.

Neither parser invents numbers: a field the engine did not report stays null
(DESIGN §3.2 "full detail, not just the median" — and its converse, never a
fabricated value). Observed engine provenance (image, llama.cpp build) is
returned for DISPLAY; see planner._build_identity for why it is NOT in cell_key.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from .schema import Config, Engine, Rep, Summary, Telemetry


@dataclass
class Parsed:
    """The results half of a record, parsed from one engine output for one cell."""

    reps: list[Rep] = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)
    engine_observed: Engine = field(default_factory=Engine)  # DISPLAY-only provenance
    telemetry: Telemetry = field(default_factory=Telemetry)
    config_observed: Config | None = None  # DISPLAY-only resolved argv/kv/ctx


def _med(values) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(vals), 4) if vals else None


def _stdev(values) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.stdev(vals), 4) if len(vals) >= 2 else None


# --------------------------------------------------------------------------- #
# Tier A — llama-bench (pp/tg) via the seam
# --------------------------------------------------------------------------- #


def llama_bench_row_kind(row: dict) -> str | None:
    """Classify a llama-bench row: a pp test has n_gen==0 (n_prompt>0), a tg test
    has n_prompt==0 (n_gen>0). Anything else (both set / both zero) → None."""
    n_prompt = row.get("n_prompt") or 0
    n_gen = row.get("n_gen") or 0
    if n_gen == 0 and n_prompt > 0:
        return "pp"
    if n_prompt == 0 and n_gen > 0:
        return "tg"
    return None


def _resolved_config_from_row(row: dict) -> Config:
    """Reconstruct the RESOLVED llama.cpp config the sweep actually ran from a
    llama-bench row (its fields are the post-dedup values the engine used), so
    the run drawer's "resolved argv/env" shows real flags even when the model has
    no registry ``extra_args``. This is DISPLAY-only — it is stamped onto the
    record for viewing but does NOT change cell_key (see runner._assemble /
    planner._build_identity: cell_key stays the plan-time content-address).

    The env block stays empty: llama-bench's JSON does not report the process env
    (HSA_OVERRIDE_GFX_VERSION etc. live in the seam/harness, not the row), and we
    never invent values (DESIGN §3.2)."""
    argv: list[str] = []

    def add(flag: str, val) -> None:
        argv.extend([flag, str(val)])

    if row.get("n_batch") is not None:
        add("-b", row["n_batch"])
    if row.get("n_ubatch") is not None:
        add("-ub", row["n_ubatch"])
    if row.get("n_threads") is not None:
        add("-t", row["n_threads"])
    if row.get("n_gpu_layers") is not None:
        add("-ngl", row["n_gpu_layers"])
    if row.get("flash_attn") is not None:
        add("-fa", "1" if row["flash_attn"] else "0")
    if row.get("type_k"):
        add("-ctk", row["type_k"])
    if row.get("type_v"):
        add("-ctv", row["type_v"])
    if row.get("n_prompt"):
        add("-p", row["n_prompt"])
    if row.get("n_gen"):
        add("-n", row["n_gen"])
    if row.get("n_depth"):
        add("-d", row["n_depth"])
    if row.get("split_mode") and row["split_mode"] != "layer":
        add("-sm", row["split_mode"])
    if row.get("tensor_split") and str(row["tensor_split"]) not in ("", "0.00", "none"):
        add("-ts", row["tensor_split"])
    kv = {k: v for k, v in (("main_k", row.get("type_k")), ("main_v", row.get("type_v"))) if v}
    return Config(argv=argv, env={}, kv=kv, ctx=int(row.get("n_depth") or 0))


def _select_row(rows: list[dict], kind: str, depth: int | None) -> dict | None:
    """Pick the row for this cell's kind. A sweep can emit several same-kind rows
    (repeats, or multiple depths); prefer one whose n_depth matches the cell, else
    the LAST (newest) matching row."""
    matches = [r for r in rows if llama_bench_row_kind(r) == kind]
    if not matches:
        return None
    if depth is not None:
        exact = [r for r in matches if (r.get("n_depth") or 0) == depth]
        if exact:
            return exact[-1]
    return matches[-1]


def parse_llama_bench(
    rows: list[dict], meta: dict | None, kind: str, depth: int | None = None
) -> Parsed:
    """Parse a llama-bench ``-o json`` array + ``.meta.json`` into one cell's
    results (DESIGN §3.2). ``kind`` is "pp" or "tg"; the matching row's
    ``samples_ts`` become ``reps[]`` (prefill t/s for pp, decode t/s for tg)."""
    meta = meta or {}
    row = _select_row(rows or [], kind, depth) or {}
    is_pp = kind == "pp"

    samples_ts = list(row.get("samples_ts") or [])
    samples_ns = list(row.get("samples_ns") or [])
    if not samples_ts and row.get("avg_ts") is not None:
        samples_ts = [row["avg_ts"]]  # a single-rep sweep may omit the samples array

    reps: list[Rep] = []
    for i, ts in enumerate(samples_ts):
        t_s = round(samples_ns[i] / 1e9, 4) if i < len(samples_ns) else None
        if is_pp:
            reps.append(Rep(t_s=t_s, prefill_ts=ts, timings_raw={"sample_index": i}))
        else:
            reps.append(Rep(t_s=t_s, decode_ts=ts, timings_raw={"sample_index": i}))

    summary = Summary()
    if samples_ts:
        med = _med(samples_ts)
        if is_pp:
            summary.prefill_ts_med = med
        else:
            summary.decode_ts_med = med
            # prefer llama-bench's own stddev_ts; fall back to computing it.
            summary.decode_ts_stddev = row.get("stddev_ts") or _stdev(samples_ts)

    build_number = row.get("build_number")
    build_commit = row.get("build_commit") or ""
    build = f"b{build_number}-{build_commit}" if build_number else build_commit
    engine = Engine(
        kind="llama-bench",
        image=meta.get("image", ""),
        llamacpp_build=build,
    )
    return Parsed(
        reps=reps,
        summary=summary,
        engine_observed=engine,
        config_observed=_resolved_config_from_row(row) if row else None,
    )


# --------------------------------------------------------------------------- #
# Tier B — server_ab.py (chat/reuse/embed/rerank) live-server
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Telemetry — hal0-benchctl's 1 Hz sampler JSONL (Phase 4)
# --------------------------------------------------------------------------- #

#: ≥3 consecutive samples >10% below the run's own peak sclk trips throttled.
_THROTTLE_DROP_FRACTION = 0.10
_THROTTLE_MIN_STREAK = 3


def _throttle_flag(sclk_mhz: list[float]) -> bool | None:
    """None when no sclk samples exist at all (the counter was unreadable —
    §14.3 nullable, never a guessed False); otherwise the streak rule against
    the run's OWN peak clock. Fewer than 3 total samples can never reach the
    streak, so it correctly falls out to False rather than needing a special
    case."""
    if not sclk_mhz:
        return None
    peak = max(sclk_mhz)
    if peak <= 0:
        return None
    threshold = peak * (1 - _THROTTLE_DROP_FRACTION)
    streak = 0
    for v in sclk_mhz:
        if v < threshold:
            streak += 1
            if streak >= _THROTTLE_MIN_STREAK:
                return True
        else:
            streak = 0
    return False


def parse_telemetry_samples(samples: list[dict[str, Any]]) -> Telemetry:
    """Turn hal0-benchctl's 1 Hz raw sampler rows (``{ts,temp_c,power_mw,
    gpu_busy_pct,vram_b,gtt_b,sclk_mhz}``, ``null`` for an unreadable counter —
    installer/wrappers/hal0-benchctl's ``telemetry start`` verb) into the
    derived :class:`Telemetry` a record carries. Every field is computed
    independently from whichever samples actually carry it, so one dead
    counter (e.g. GTT locked down by debugfs perms) never blanks the others.
    An empty sample list (no telemetry.jsonl, or a run that never reached
    ``telemetry start``) returns an all-None ``Telemetry`` — never raises,
    never fabricates a value (DESIGN §3.2 / §14.3)."""

    def nums(key: str) -> list[float]:
        return [s[key] for s in samples if isinstance(s.get(key), (int, float))]

    vram_b = nums("vram_b")
    gtt_b = nums("gtt_b")
    temp_millic = nums("temp_c")  # the sampler's key names the UNIT wrong: it's millidegrees
    power_mw = nums("power_mw")
    sclk_mhz = nums("sclk_mhz")

    return Telemetry(
        vram_peak_mb=round(max(vram_b) / (1024 * 1024)) if vram_b else None,
        gtt_peak_mb=round(max(gtt_b) / (1024 * 1024)) if gtt_b else None,
        gpu_edge_temp_max_c=round(max(temp_millic) / 1000) if temp_millic else None,
        gpu_power_avg_w=round(statistics.mean(power_mw) / 1000) if power_mw else None,
        throttled=_throttle_flag(sclk_mhz),
    )


def _sa_run_to_rep(run: dict) -> Rep | None:
    """One server_ab timed run → a Rep. None for an errored run (no throughput),
    so a failed call is dropped rather than counted as a measurement."""
    if not isinstance(run, dict) or run.get("error") or run.get("predicted_per_second") is None:
        return None
    draft_n = run.get("draft_n")
    accepted = run.get("draft_n_accepted")
    # Only compute acceptance when BOTH counts are present. A run can report
    # draft_n>0 with draft_n_accepted null/absent (partial or older llama.cpp
    # timings) — `None / draft_n` would TypeError and drop the whole run.
    accept = (accepted / draft_n) if (draft_n and accepted is not None) else None
    return Rep(
        t_s=run.get("wall_s"),
        prefill_ts=run.get("prompt_per_second"),
        decode_ts=run.get("predicted_per_second"),
        ttft_ms=run.get("prompt_ms"),  # prompt-processing time ≈ TTFT proxy
        accept_rate=round(accept, 4) if accept is not None else None,
        drafted=draft_n,
        accepted=accepted,
        timings_raw=run,
    )


def parse_server_ab(doc: dict, kind: str | None = None) -> Parsed:
    """Parse one server_ab.py session JSON into a cell's results (DESIGN §3.2).

    ``embed``/``rerank`` → ``results.latency_s`` becomes ``reps[]`` (t_s only;
    there is no decode t/s to report, so throughput summary stays null). ``ab``/
    ``reuse`` → the first variant's ``runs`` (or ``second_call``) become
    ``reps[]`` with decode/prefill/accept, medians in ``summary``. Engine image is
    left blank here — the runner fills it from /api/slots for the live slot."""
    mode = doc.get("mode")
    results = doc.get("results") or {}
    engine = Engine(kind="llama-server")

    if mode in ("embed", "rerank"):
        lat = [x for x in (results.get("latency_s") or []) if isinstance(x, (int, float))]
        reps = [Rep(t_s=x, timings_raw={"latency_s": x}) for x in lat]
        return Parsed(reps=reps, summary=Summary(), engine_observed=engine)

    # ab / reuse: take the first variant that carries timed runs.
    runs: list[dict] = []
    extra_args = ""
    for block in results.values():
        if not isinstance(block, dict):
            continue
        if block.get("runs"):
            runs = block["runs"]
            extra_args = block.get("extra_args", "")
            break
        if block.get("second_call"):
            runs = [block["second_call"]]
            extra_args = block.get("extra_args", "")
            break

    reps = [r for r in (_sa_run_to_rep(run) for run in runs) if r is not None]
    summary = Summary(
        decode_ts_med=_med([r.decode_ts for r in reps]),
        decode_ts_stddev=_stdev([r.decode_ts for r in reps]),
        prefill_ts_med=_med([r.prefill_ts for r in reps]),
        accept_med=_med([r.accept_rate for r in reps]),
    )
    # The server_ab variant's extra_args are the only resolved flags the session
    # file records (the full server argv lives in /api/slots, which the runner
    # folds in separately). Show them; never invent the rest.
    config = None
    if extra_args:
        import shlex

        config = Config(argv=shlex.split(extra_args))
    return Parsed(reps=reps, summary=summary, engine_observed=engine, config_observed=config)
