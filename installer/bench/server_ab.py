#!/usr/bin/env python3
"""server_ab.py — server-level A/B measurement for hal0 slots (Tier B of the
profile-matrix; see profile-matrix.sh for Tier A / llama-bench cells).

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
          draft-acceptance when the server exposes it in timings.
  reuse   Built-in A/B of --cache-reuse 256 vs 0 on a shared-prefix trace:
          per variant, two /completion calls with the same long prefix and
          different suffixes; the second call's prompt timings show the reuse
          win (agentic TTFT proxy).
  embed   No config change: N timed POST /v1/embeddings calls with a long
          input; sanity-checks vector dims + reports latency.
  rerank  No config change: N timed POST /v1/rerank calls (query + docs);
          sanity-checks score spread + reports latency.

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
from datetime import datetime, timezone
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


def _http(method: str, url: str, body: dict | None = None, timeout: float = 600.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — localhost only
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
            with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    sys.exit(f"slot port {port} did not become healthy within {timeout_s:.0f}s")


def _completion(port: int, prompt: str, n_predict: int, cache_prompt: bool) -> dict:
    """One /completion call; returns llama-server's timings dict (+ our wall)."""
    t0 = time.monotonic()
    out = _http(
        "POST",
        f"http://127.0.0.1:{port}/completion",
        {
            "prompt": prompt,
            "n_predict": n_predict,
            "temperature": 0,
            "cache_prompt": cache_prompt,
        },
    )
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
        for label, flags in (("cache-reuse-256", "--cache-reuse 256"), ("cache-reuse-0", "--cache-reuse 0")):
            merged = f"{original} {flags}".strip() if original else flags
            print(f"[reuse] {label}: extra_args = {merged!r}", flush=True)
            _apply_extra_args(args.api, args.slot, merged or None)
            _wait_ready(port)
            # Call 1 warms the cache with prefix+A; call 2 (prefix+B) is the
            # measurement — with reuse the shared prefix is KV-shifted, without
            # it the whole prompt reprocesses.
            _completion(port, REUSE_PREFIX + "Summarize the first paragraph.", 32, cache_prompt=True)
            second = _completion(port, REUSE_PREFIX + "List three key claims.", 32, cache_prompt=True)
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
            {"query": "How does unified memory change LLM inference trade-offs?", "documents": docs},
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True, choices=["ab", "reuse", "embed", "rerank"])
    ap.add_argument("--slot", required=True, help="slot name (must be loaded/running)")
    ap.add_argument("--api", default=DEFAULT_API, help=f"hal0-api base (default {DEFAULT_API})")
    ap.add_argument("--variant", action="append", default=[],
                    help='ab mode: "label:<extra llama-server args>" (repeatable)')
    ap.add_argument("--n", type=int, default=3, help="timed runs per variant (default 3)")
    ap.add_argument("--max-tokens", type=int, default=256, help="decode length per run")
    ap.add_argument("--out", default=None, help="result JSON path (default under server-ab/)")
    args = ap.parse_args()

    if args.mode == "ab" and len(args.variant) < 2:
        ap.error("--mode ab needs at least two --variant entries")

    fn = {"ab": mode_ab, "reuse": mode_reuse, "embed": mode_embed, "rerank": mode_rerank}[args.mode]
    results = fn(args)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else RESULT_DIR / f"{stamp}-{args.mode}-{args.slot}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "timestamp": stamp,
        "mode": args.mode,
        "slot": args.slot,
        "n": args.n,
        "max_tokens": args.max_tokens,
        "results": results,
    }
    out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")
    print(json.dumps({k: v.get("median", v) if isinstance(v, dict) else v for k, v in results.items()}, indent=2))


if __name__ == "__main__":
    main()
