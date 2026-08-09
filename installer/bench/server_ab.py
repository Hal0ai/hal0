#!/usr/bin/env python3
"""server_ab.py — server-level A/B measurement for hal0 slots (Tier B; Tier A
llama-bench cells run through hal0.bench.harness + the hal0-benchctl exec seam).

llama-bench cannot measure the server-only levers: MTP speculative decode
(``--spec-draft-*``), prompt-cache reuse (``--cache-reuse``), busy-wait poll,
or the embed/rerank endpoints. This script measures those against LIVE slots
through hal0-api + the slot's own llama-server port. It supersedes the ad-hoc
/root/bench_mtp.py with a versioned, restorable harness.

Runs as the unprivileged hal0/agent user — no sudo, no seam: it only PUTs slot
config through hal0-api (same surface the dashboard uses) and always restores
the slot's original extra_args afterwards (try/finally).

Modes
-----
  ab      Apply each --variant "label:<extra llama-server args>" to the slot's
          [server].extra_args (appended after the originals, so last-wins dedup
          lets the variant override profile/bundle flags), restart, wait ready,
          run N timed /completion calls. Reports prefill+decode t/s medians and
          draft-acceptance when the server exposes it in timings. One variant =
          a plain labelled measurement; two or more = a side-by-side A/B.
  reuse   Built-in A/B of --cache-reuse 256 vs 0 on a shared-prefix trace:
          per variant, two /completion calls with the same long prefix and
          different suffixes; the second call's prompt timings show the reuse
          win (agentic TTFT proxy).
  embed   No config change: N timed POST /v1/embeddings calls with a long
          input; sanity-checks vector dims + reports latency.
  rerank  No config change: N timed POST /v1/rerank calls (query + docs);
          sanity-checks score spread + reports latency.
  batch   Continuous-batching sweep: for each --np value, set the slot's
          `parallel` field (PUT /config {parallel}), restart, drive
          --concurrency simultaneous /completion streams, and report aggregate
          t/s, per-stream median, and p95 TTFT. The Tier C measurement behind
          the concurrency-batching plan. Restores the original `parallel`.
  mtp     MTP draft-param sweep over --spec-nmax x --spec-pmin at --depth
          (2k/32k/128k) and a chosen sampler (--temp/--top-p/--top-k: greedy vs
          production). Relaunches per cell so the draft params take effect —
          the ROCmFPX runner at 7aa484a IGNORES the per-request speculative.*
          override (--per-request opts into it anyway, for a future build that
          honors it). Reports decode t/s + draft acceptance per cell (B-MTP).

Results: JSON to --out (default /var/lib/hal0/benchmarks/server-ab/<stamp>.json).

GPU rule: like llama-bench, numbers are only clean when nothing else is using
the GPU. This script talks to ONE slot; stop other GPU slots first (or accept
contention). It does not stop slots itself — the slot under test must be up.

Stdlib only (urllib) so it runs bare on the box without hal0's venv.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_API = "http://127.0.0.1:8080"
RESULT_DIR = Path("/var/lib/hal0/benchmarks/server-ab")

# ~2k-token-ish deterministic prompt (repeated technical text; no RNG so runs
# are comparable across boxes and days).
_PARA = (
    "The unified memory architecture of the Strix Halo platform allows the "
    "integrated GPU to address the full LPDDR5X pool, which changes the "
    "trade-offs for large language model inference: model weights are not "
    "copied across a PCIe boundary, prompt processing is compute bound while "
    "token generation is bandwidth bound, and the optimal batch and micro "
    "batch sizes differ from discrete GPU systems. "
)
LONG_PROMPT = _PARA * 40  # ≈ 2k tokens
REUSE_PREFIX = _PARA * 60  # ≈ 3k tokens shared prefix for the reuse trace

# _PARA is ~50 tokens; used to synthesize a prompt of an approximate token
# depth for the depth axis (runbook: tune at 2k AND 32k/128k, not just 2k).
_PARA_TOKENS = 50


def _build_prompt(depth_tokens: int) -> str:
    """A deterministic prompt of roughly *depth_tokens* tokens (repeated _PARA,
    at least one rep). Lets the same cell run at 2k / 32k / 128k fill so a
    'best' flag is reported per depth, not just at the shallow default."""
    reps = max(1, round(depth_tokens / _PARA_TOKENS))
    return _PARA * reps


def _http(method: str, url: str, body: dict | None = None, timeout: float = 600.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    return json.loads(payload) if payload else {}


def _get_slot(api: str, name: str) -> dict:
    slots = _http("GET", f"{api}/api/slots")
    items = slots if isinstance(slots, list) else slots.get("slots", slots.get("data", []))
    for s in items:
        if s.get("name") == name:
            return s
    sys.exit(f"slot {name!r} not found via {api}/api/slots")


def _wait_ready(port: int, timeout_s: float = 300.0) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    sys.exit(f"slot port {port} did not become healthy within {timeout_s:.0f}s")


def _sampler_body(args: argparse.Namespace) -> dict:
    """The sampler half of a /completion body, from the CLI axis. temp 0 =
    greedy (upper-bound MTP acceptance); a production sampler (e.g. temp 0.6
    top-p 0.95 top-k 20) is what agents actually see — the runbook runs both."""
    body: dict[str, Any] = {"temperature": float(getattr(args, "temp", 0.0) or 0.0)}
    top_p = getattr(args, "top_p", None)
    top_k = getattr(args, "top_k", None)
    if top_p is not None:
        body["top_p"] = float(top_p)
    if top_k is not None:
        body["top_k"] = int(top_k)
    return body


def _spec_override(n_max: int | None, p_min: float | None, n_min: int | None) -> dict:
    """Per-request `speculative.*` override (fork accepts n_max/n_min/p_min in
    the /completion JSON — MTP param sweeps run WITHOUT a server restart)."""
    spec: dict[str, Any] = {}
    if n_max is not None:
        spec["n_max"] = int(n_max)
    if n_min is not None:
        spec["n_min"] = int(n_min)
    if p_min is not None:
        spec["p_min"] = float(p_min)
    return {"speculative": spec} if spec else {}


def _completion(
    port: int,
    prompt: str,
    n_predict: int,
    cache_prompt: bool,
    *,
    sampler: dict | None = None,
    speculative: dict | None = None,
    ignore_eos: bool = True,
) -> dict:
    """One /completion call; returns llama-server's timings dict (+ our wall).

    ``sampler`` defaults to greedy (temp 0) for reproducibility; pass
    ``_sampler_body(args)`` for a production sampler. ``speculative`` carries a
    per-request MTP override (``_spec_override(...)['speculative']``).

    ``ignore_eos`` defaults True: a decode-throughput measurement needs the full
    ``n_predict`` tokens, but a strict chat template (e.g. the 35B's Froggeric)
    emits EOS after ~1 token on a raw /completion prompt, collapsing
    ``predicted_n`` to 1 and making the t/s meaningless. Off only for the reuse
    trace, where the natural stop is fine."""
    body: dict[str, Any] = {
        "prompt": prompt,
        "n_predict": n_predict,
        "cache_prompt": cache_prompt,
        "ignore_eos": ignore_eos,
    }
    body.update(sampler or {"temperature": 0})
    if speculative:
        body["speculative"] = speculative
    t0 = time.monotonic()
    out = _http("POST", f"http://127.0.0.1:{port}/completion", body)
    wall = time.monotonic() - t0
    t = dict(out.get("timings") or {})
    t["wall_s"] = round(wall, 3)
    return t


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(vals), 2) if vals else None


def _summarize_runs(runs: list[dict]) -> dict:
    summary = {
        "prompt_per_second": _median([r.get("prompt_per_second") for r in runs]),
        "predicted_per_second": _median([r.get("predicted_per_second") for r in runs]),
        "wall_s": _median([r.get("wall_s") for r in runs]),
    }
    # Speculative-decode acceptance, when the server reports it (draft_n /
    # draft_n_accepted appear in timings only while a spec mode is active).
    drafted = sum(r.get("draft_n") or 0 for r in runs)
    accepted = sum(r.get("draft_n_accepted") or 0 for r in runs)
    if drafted:
        summary["draft_acceptance_pct"] = round(100.0 * accepted / drafted, 1)
    return summary


def _apply_extra_args(api: str, slot: str, extra_args: str | None) -> None:
    """PUT [server].extra_args (None deletes the key = back to profile flags)."""
    _http("PUT", f"{api}/api/slots/{slot}/config", {"server": {"extra_args": extra_args}})
    _http("POST", f"{api}/api/slots/{slot}/restart", {})


def mode_ab(args: argparse.Namespace) -> dict:
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    original = slot.get("llamacpp_args") or None  # current [server].extra_args

    variants: list[tuple[str, str]] = []
    for v in args.variant:
        label, _, flags = v.partition(":")
        variants.append((label.strip() or flags.strip() or "variant", flags.strip()))

    results: dict[str, Any] = {}
    try:
        for label, flags in variants:
            merged = f"{original} {flags}".strip() if original else flags
            print(f"[ab] {label}: extra_args = {merged!r}", flush=True)
            _apply_extra_args(args.api, args.slot, merged or None)
            _wait_ready(port)
            runs = []
            for i in range(args.n):
                t = _completion(port, LONG_PROMPT, args.max_tokens, cache_prompt=False)
                print(f"  run {i + 1}/{args.n}: {t}", flush=True)
                runs.append(t)
            results[label] = {"extra_args": merged, "runs": runs, "median": _summarize_runs(runs)}
    finally:
        print(f"[ab] restoring original extra_args = {original!r}", flush=True)
        _apply_extra_args(args.api, args.slot, original)
    return results


def mode_reuse(args: argparse.Namespace) -> dict:
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    original = slot.get("llamacpp_args") or None

    results: dict[str, Any] = {}
    try:
        for label, flags in (
            ("cache-reuse-256", "--cache-reuse 256"),
            ("cache-reuse-0", "--cache-reuse 0"),
        ):
            merged = f"{original} {flags}".strip() if original else flags
            print(f"[reuse] {label}: extra_args = {merged!r}", flush=True)
            _apply_extra_args(args.api, args.slot, merged or None)
            _wait_ready(port)
            # Call 1 warms the cache with prefix+A; call 2 (prefix+B) is the
            # measurement — with reuse the shared prefix is KV-shifted, without
            # it the whole prompt reprocesses.
            _completion(
                port,
                REUSE_PREFIX + "Summarize the first paragraph.",
                32,
                cache_prompt=True,
                ignore_eos=False,
            )
            second = _completion(
                port,
                REUSE_PREFIX + "List three key claims.",
                32,
                cache_prompt=True,
                ignore_eos=False,
            )
            print(f"  second-call timings: {second}", flush=True)
            results[label] = {"extra_args": merged, "second_call": second}
    finally:
        print(f"[reuse] restoring original extra_args = {original!r}", flush=True)
        _apply_extra_args(args.api, args.slot, original)
    return results


def mode_embed(args: argparse.Namespace) -> dict:
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    text = _PARA * 30  # long single input — exercises the -ub 8192 sizing rule
    lat, dims = [], None
    for i in range(args.n):
        t0 = time.monotonic()
        out = _http("POST", f"http://127.0.0.1:{port}/v1/embeddings", {"input": text})
        lat.append(round(time.monotonic() - t0, 3))
        vec = (out.get("data") or [{}])[0].get("embedding") or []
        dims = len(vec)
        if not dims or all(v == 0 for v in vec[:8]):
            sys.exit(f"embed sanity FAILED on run {i + 1}: dims={dims} (zero/empty vector)")
    return {"dims": dims, "latency_s": lat, "median_latency_s": _median(lat)}


def mode_rerank(args: argparse.Namespace) -> dict:
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    docs = [
        _PARA,
        "Bananas are a good source of potassium and are yellow when ripe.",
        "Prompt processing on unified-memory APUs is compute bound.",
        "The weather in Reykjavik is frequently windy in winter.",
    ]
    lat, spread = [], None
    for i in range(args.n):
        t0 = time.monotonic()
        out = _http(
            "POST",
            f"http://127.0.0.1:{port}/v1/rerank",
            {
                "query": "How does unified memory change LLM inference trade-offs?",
                "documents": docs,
            },
        )
        lat.append(round(time.monotonic() - t0, 3))
        scores = [r.get("relevance_score", 0.0) for r in out.get("results", [])]
        if len(scores) != len(docs):
            sys.exit(f"rerank sanity FAILED on run {i + 1}: {len(scores)}/{len(docs)} scores")
        spread = round(max(scores) - min(scores), 4)
        if spread == 0:
            sys.exit(
                "rerank sanity FAILED: zero score spread — classifier head missing "
                "(bad GGUF conversion) or --embedding and --reranking combined on one instance"
            )
    return {"score_spread": spread, "latency_s": lat, "median_latency_s": _median(lat)}


def _one_stream(port: int, prompt: str, n_predict: int) -> dict:
    """A single concurrent /completion call (own prompt so slots don't collide
    on identical prefixes); returns per-stream timings + wall."""
    return _completion(port, prompt, n_predict, cache_prompt=True)


def mode_batch(args: argparse.Namespace) -> dict:
    """Continuous-batching sweep: for each --np value, set the slot's `parallel`
    field (PUT /config {parallel}), restart, then drive C concurrent
    /completion streams and report AGGREGATE t/s, per-stream median, and TTFT
    spread — the numbers that decide whether a slot class should batch (plan
    Tier C). Streams share a long common prefix with distinct suffixes to
    exercise slot routing + prompt cache. Restores the original `parallel` at
    the end.
    """
    import concurrent.futures as cf

    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    original = slot.get("parallel")  # None = inherit profile
    np_values = [int(x) for x in str(args.np).split(",") if x.strip()]

    results: dict[str, Any] = {}
    try:
        for np in np_values:
            conc = args.concurrency or np  # default: saturate the slots
            print(f"[batch] parallel={np}, concurrency={conc}", flush=True)
            _http("PUT", f"{args.api}/api/slots/{args.slot}/config", {"parallel": np})
            _http("POST", f"{args.api}/api/slots/{args.slot}/restart", {})
            _wait_ready(port)
            rounds: list[dict] = []
            for r in range(args.n):
                # Distinct suffix per stream so N sequences don't alias to one
                # cached slot; shared LONG_PROMPT prefix mimics agents sharing a
                # system prompt.
                prompts = [
                    f"{LONG_PROMPT} Request {i} round {r}: continue in one sentence."
                    for i in range(conc)
                ]
                t0 = time.monotonic()
                with cf.ThreadPoolExecutor(max_workers=conc) as ex:
                    streams = list(ex.map(lambda p: _one_stream(port, p, args.max_tokens), prompts))
                wall = time.monotonic() - t0
                gen = sum(s.get("predicted_n") or args.max_tokens for s in streams)
                rounds.append(
                    {
                        "wall_s": round(wall, 3),
                        "aggregate_tps": round(gen / wall, 2) if wall else None,
                        "per_stream_tps": [s.get("predicted_per_second") for s in streams],
                        "ttft_s": [s.get("prompt_ms", 0) / 1000.0 for s in streams],
                    }
                )
                print(
                    f"  round {r + 1}/{args.n}: aggregate {rounds[-1]['aggregate_tps']} t/s",
                    flush=True,
                )
            agg = [x["aggregate_tps"] for x in rounds]
            per = [v for x in rounds for v in x["per_stream_tps"]]
            ttft = [v for x in rounds for v in x["ttft_s"]]
            results[f"np{np}"] = {
                "parallel": np,
                "concurrency": conc,
                "rounds": rounds,
                "median": {
                    "aggregate_tps": _median(agg),
                    "per_stream_tps": _median(per),
                    "ttft_p95_s": round(sorted(ttft)[int(0.95 * (len(ttft) - 1))], 3)
                    if ttft
                    else None,
                },
            }
    finally:
        print(f"[batch] restoring parallel = {original!r}", flush=True)
        _http("PUT", f"{args.api}/api/slots/{args.slot}/config", {"parallel": original})
        _http("POST", f"{args.api}/api/slots/{args.slot}/restart", {})
    return results


def _csv_ints(raw: str) -> list[int]:
    return [int(x) for x in str(raw).split(",") if x.strip()]


def _csv_floats(raw: str) -> list[float]:
    return [float(x) for x in str(raw).split(",") if x.strip()]


def mode_mtp(args: argparse.Namespace) -> dict:
    """MTP draft-param sweep over the --spec-nmax x --spec-pmin grid at a given
    --depth and sampler, reporting decode t/s + draft acceptance per cell so the
    winner is picked on NET decode (greedy acceptance is a ceiling, not a ship
    metric). Draft-KV quant is NOT swept here (use --mode ab).

    Default is RELAUNCH-PER-VALUE: each cell writes `--spec-draft-n-max/-n-min/
    -p-min` into [server].extra_args and restarts (like mode_ab). The
    per-request `speculative.*` JSON override was tried first (it would let the
    whole grid run against one warm server) but the ROCmFPX runner at 7aa484a
    SILENTLY IGNORES it — every cell then reads the launch-time config and the
    sweep is meaningless. --per-request opts back into that path for a future
    runner build that honors it (with a loud caveat)."""
    slot = _get_slot(args.api, args.slot)
    port = int(slot["port"])
    original = slot.get("llamacpp_args") or None
    prompt = _build_prompt(args.depth)
    sampler = _sampler_body(args)
    n_maxes = _csv_ints(args.spec_nmax)
    p_mins = _csv_floats(args.spec_pmin)
    n_min = int(args.spec_nmin)

    if args.per_request:
        print(
            "[mtp] WARNING: --per-request sends speculative.* in the /completion "
            "JSON; the ROCmFPX runner at 7aa484a IGNORES it (all cells read "
            "identical). Only use on a runner build known to honor it.",
            flush=True,
        )

    results: dict[str, Any] = {}
    try:
        for n_max in n_maxes:
            for p_min in p_mins:
                label = f"nmax{n_max}_pmin{p_min}"
                if args.per_request:
                    spec = _spec_override(n_max, p_min, n_min)["speculative"]
                    _wait_ready(port)
                else:
                    # Relaunch so the draft params actually take effect.
                    flags = (
                        f"--spec-draft-n-max {n_max} --spec-draft-n-min {n_min} "
                        f"--spec-draft-p-min {p_min}"
                    )
                    merged = f"{original} {flags}".strip() if original else flags
                    _apply_extra_args(args.api, args.slot, merged)
                    _wait_ready(port)
                    spec = None
                print(
                    f"[mtp] {label} (depth~{args.depth}, temp {sampler['temperature']}, "
                    f"{'per-request' if args.per_request else 'relaunch'})",
                    flush=True,
                )
                runs = []
                for i in range(args.n):
                    t = _completion(
                        port,
                        prompt,
                        args.max_tokens,
                        cache_prompt=False,
                        sampler=sampler,
                        speculative=spec,
                    )
                    print(f"  run {i + 1}/{args.n}: {t}", flush=True)
                    runs.append(t)
                results[label] = {
                    "n_max": n_max,
                    "p_min": p_min,
                    "n_min": n_min,
                    "runs": runs,
                    "median": _summarize_runs(runs),
                }
    finally:
        if not args.per_request:
            print(f"[mtp] restoring original extra_args = {original!r}", flush=True)
            _apply_extra_args(args.api, args.slot, original)
    return results


def _provenance(args: argparse.Namespace, slot: dict | None) -> dict:
    """Reproducibility header (plan risk 6): the local-only runner image, its
    decode-tune profile, and the slot's resolved argv/env — without these the
    numbers can't be reproduced on a rebuilt box."""
    prov: dict[str, Any] = {}
    if args.runner_image:
        prov["runner_image"] = args.runner_image
    if args.decode_tune:
        prov["decode_tune"] = args.decode_tune
    if args.note:
        prov["note"] = args.note
    prov["depth_tokens"] = args.depth
    prov["sampler"] = _sampler_body(args)
    if slot is not None:
        prov["slot_resolved_args"] = slot.get("llamacpp_args")
        prov["slot_env"] = slot.get("env") or (slot.get("server") or {}).get("env")
    return prov


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--mode", required=True, choices=["ab", "reuse", "embed", "rerank", "batch", "mtp"]
    )
    ap.add_argument("--slot", required=True, help="slot name (must be loaded/running)")
    ap.add_argument("--api", default=DEFAULT_API, help=f"hal0-api base (default {DEFAULT_API})")
    ap.add_argument(
        "--variant",
        action="append",
        default=[],
        help='ab mode: "label:<extra llama-server args>" (repeatable)',
    )
    ap.add_argument(
        "--np",
        default="1,2,4,8",
        help="batch mode: comma list of --parallel values to sweep (default 1,2,4,8)",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=0,
        help="batch mode: simultaneous streams per np (default 0 = match np)",
    )
    ap.add_argument("--n", type=int, default=3, help="timed runs/rounds (default 3)")
    ap.add_argument("--max-tokens", type=int, default=256, help="decode length per run")
    ap.add_argument("--out", default=None, help="result JSON path (default under server-ab/)")
    # Depth axis — approximate prompt-fill in tokens (runbook: 2k/32k/128k).
    ap.add_argument(
        "--depth",
        type=int,
        default=2000,
        help="mtp mode: approximate prompt-fill in tokens (default 2000; runbook sweeps 2k/32k/128k)",
    )
    # Sampler axis — greedy (temp 0, acceptance ceiling) vs production sampler.
    ap.add_argument(
        "--temp", type=float, default=0.0, help="mtp mode: sampling temperature (0=greedy)"
    )
    ap.add_argument(
        "--top-p", dest="top_p", type=float, default=None, help="mtp mode: top-p (optional)"
    )
    ap.add_argument(
        "--top-k", dest="top_k", type=int, default=None, help="mtp mode: top-k (optional)"
    )
    # MTP draft-param grid. Relaunch-per-value by default (7aa484a ignores the
    # per-request override — see --per-request).
    ap.add_argument(
        "--spec-nmax", default="1,2,3,4", help="mtp mode: comma list of speculative n_max"
    )
    ap.add_argument(
        "--spec-pmin", default="0.0,0.25,0.5,0.75", help="mtp mode: comma list of speculative p_min"
    )
    ap.add_argument(
        "--spec-nmin", type=int, default=0, help="mtp mode: speculative n_min (default 0)"
    )
    ap.add_argument(
        "--per-request",
        action="store_true",
        help="mtp mode: send speculative.* per request instead of relaunching "
        "(IGNORED by the ROCmFPX runner at 7aa484a — only for a build that honors it)",
    )
    # Provenance (plan risk 6) — the local-only runner image is not reproducible
    # without capturing what built it.
    ap.add_argument("--runner-image", default=None, help="runner image ref for the results header")
    ap.add_argument(
        "--decode-tune", default=None, help="ROCMFP4_DECODE_TUNE profile for the header"
    )
    ap.add_argument("--note", default=None, help="free-text note for the results header")
    args = ap.parse_args()

    # One --variant is a plain labelled measurement (the bench runner's chat
    # cells pass exactly one, carrying the cell's config variant); two or more
    # is an A/B comparison. Zero would measure nothing.
    if args.mode == "ab" and not args.variant:
        ap.error("--mode ab needs at least one --variant entry")

    fn = {
        "ab": mode_ab,
        "reuse": mode_reuse,
        "embed": mode_embed,
        "rerank": mode_rerank,
        "batch": mode_batch,
        "mtp": mode_mtp,
    }[args.mode]
    results = fn(args)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else RESULT_DIR / f"{stamp}-{args.mode}-{args.slot}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "timestamp": stamp,
        "mode": args.mode,
        "slot": args.slot,
        "n": args.n,
        "max_tokens": args.max_tokens,
        "provenance": _provenance(args, _get_slot(args.api, args.slot)),
        "results": results,
    }
    out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")
    print(
        json.dumps(
            {k: v.get("median", v) if isinstance(v, dict) else v for k, v in results.items()},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
