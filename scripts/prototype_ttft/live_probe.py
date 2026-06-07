"""Live validator — hit a running hal0 endpoint and check that the
measurement model produces accurate numbers against ground truth.

PROTOTYPE — wipe me after answering:

  1. Does client-side TTFT (time of first SSE chunk minus time
     of request submission) line up with what the server's
     _instrument_streaming_throughput wrapper would record? The
     dispatcher adds dispatch overhead — confirm it's bounded.

  2. Does /api/slots/metrics' `kv_cache_usage` track the actual
     llama-server /metrics gauge in real time?

Run:
    BASE=http://localhost:8088 TOKEN=... python3 live_probe.py
    BASE=http://192.0.2.1:8088 TOKEN=... python3 live_probe.py

Reads $BASE/v1/chat/completions with stream=true, prints the
client-side TTFT and tok/s, then polls $BASE/api/slots/metrics for
kv_cache_usage on every slot. No persistence, no abstractions.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("BASE", "http://localhost:8088").rstrip("/")
TOKEN = os.environ.get("TOKEN", "")
MODEL = os.environ.get("MODEL", "")  # empty → server uses primary default
PROMPT = os.environ.get("PROMPT", "Say one sentence about the moon.")


def auth_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def measure_chat_ttft() -> None:
    body = json.dumps(
        {
            "model": MODEL or None,
            "messages": [{"role": "user", "content": PROMPT}],
            "stream": True,
            "max_tokens": 64,
        }
    ).encode()
    # Strip nulls the OpenAI shim refuses (model:null)
    if not MODEL:
        body = body.replace(b'"model": null, ', b"")
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=body,
        headers=auth_headers(),
        method="POST",
    )
    t_send = time.monotonic()
    first_chunk_ts: float | None = None
    chunks = 0
    deltas = 0
    last_chunk_ts = t_send
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                if first_chunk_ts is None and line.strip():
                    first_chunk_ts = time.monotonic()
                chunks += 1
                last_chunk_ts = time.monotonic()
                # Count "delta": occurrences the same way the server
                # instrumentation does, so the per-chunk count maps 1:1.
                deltas += line.count(b'"delta":')
    except Exception as e:
        print(f"  ! request failed: {e}", file=sys.stderr)
        return
    if first_chunk_ts is None:
        print("  ! no chunks received")
        return
    ttft = first_chunk_ts - t_send
    gen_secs = last_chunk_ts - first_chunk_ts
    tps = (deltas / gen_secs) if gen_secs > 0 else 0.0
    print(f"  client TTFT       : {ttft * 1000:7.1f} ms")
    print(f"  chunks / deltas   : {chunks} / {deltas}")
    print(f"  gen tok/s (est)   : {tps:7.2f}")


def fetch_slot_metrics() -> dict[str, dict]:
    req = urllib.request.Request(f"{BASE}/api/slots/metrics", headers=auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r)
    except Exception as e:
        print(f"  ! /api/slots/metrics failed: {e}", file=sys.stderr)
        return {}
    return data.get("slots") or data or {}


def main() -> None:
    print(f"hal0 base: {BASE}")
    print()
    print("[1] kicking a stream to measure client-side TTFT…")
    measure_chat_ttft()
    print()
    print("[2] /api/slots/metrics snapshot:")
    metrics = fetch_slot_metrics()
    if not metrics:
        return
    print(f"  {'slot':<16}{'tok/s':>10}{'kv-cache':>12}{'inflt':>8}{'mem MB':>10}")
    for name, m in metrics.items():
        tps = m.get("tokens_per_sec") or m.get("tps") or 0.0
        kv = m.get("kv_cache_usage")
        kv_s = f"{kv * 100:5.1f} %" if isinstance(kv, (int, float)) else "    —"
        inflt = m.get("requests_processing") or 0
        mem = m.get("mem_rss_mb") or 0.0
        print(f"  {name:<16}{tps:>10.2f}{kv_s:>12}{inflt:>8}{mem:>10.0f}")


if __name__ == "__main__":
    main()
