# prototype_ttft — TTFT + KV-cache % measurement and aggregation

**Status: kept reference tool.** Originally a one-off prototype (May
2026) to validate how TTFT samples and KV-cache % gauges should
aggregate across slots for the dashboard. The validated logic was
lifted into `src/hal0/slots/ttft_samples.py`; this directory stays
around as a **teaching aid** — when the model needs to be reasoned
about, tweaked, or explained to a new contributor, the TUI here is the
fastest path to "watch the state change and feel out the edge cases"
without spinning up the full stack.

## What it teaches

- The lifecycle of a single TTFT sample: `request_started` → wait
  for first chunk → `first_chunk` → sample lands → sample ages out of
  the 60s window.
- How `FleetMetrics.avg_ttft()` weights slots equally (one slot's
  noisy single sample doesn't drown a busy slot's average — and vice
  versa).
- Why slots without a value are *excluded* from the fleet avg
  rather than counted as zero — non-llama slots have no KV-cache,
  idle slots have no recent TTFT.
- How `request_cancelled` evicts inflight state without recording a
  sample (cancelled requests don't lie about prefill time).

If `src/hal0/slots/ttft_samples.py` changes shape, mirror the change
here so the TUI keeps working — they share the same data model on
purpose.

## Run

```sh
ssh -t hal0 'cd /opt/hal0 && make proto-ttft'        # logic TUI
ssh hal0 'cd /opt/hal0 && BASE=… TOKEN=… make proto-ttft-live'  # live probe
```

The `-t` flag forces a PTY — without it, raw-mode keystroke reading
fails with `Inappropriate ioctl for device`.

Keys:

- `1`–`5`: start a request on slot N (primary, embed, embed-rerank, stt, tts)
- `q w e r t`: first-chunk arrives on the matching slot
- `s`: simulate a 4-request concurrent burst on primary with first
  chunks at 50 / 80 / 250 / 600 ms — realistic prefill spread
- `k` / `d`: bump / drop KV-cache % on the three llama-backed slots
- `c`: cancel oldest inflight request
- `a`: age out all samples past the 60s window (jump the clock)
- `R`: reset state
- `x`: quit

## Files

- `metrics_core.py` — pure logic: `SlotSamples`, `FleetMetrics`. No
  FastAPI, no I/O. Identical shape to `src/hal0/slots/ttft_samples.py`.
- `tui.py` — terminal shell over `metrics_core`.
- `live_probe.py` — hits a running hal0 endpoint, measures TTFT
  client-side via streaming POST, snapshots `/api/slots/metrics` for
  comparison.
- `NOTES.md` — verdicts from the original prototype session + open
  questions that informed the production wiring.

See also `docs/internal/metrics-prototype.md` for the contributor
pointer.
