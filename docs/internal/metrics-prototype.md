# Metrics prototype — `scripts/prototype_ttft/`

A small TUI + live probe that lets you reason about hal0's TTFT
sampling and fleet-aggregation model without booting the API or any
slot. **Use this when you want to**:

- Explain the dashboard's "fleet avg TTFT" or "fleet avg KV-cache %"
  tile to a new contributor — pressing keys is faster than tracing
  code paths.
- Change the aggregation rule (window length, weighting, exclusion
  policy) — drive the TUI through the realistic event sequences
  first, *then* edit `src/hal0/slots/ttft_samples.py`.
- Validate a measurement against the running stack — `make
  proto-ttft-live` fires one real streaming chat completion and
  compares client-side TTFT to what `/api/slots/metrics` reports.

## Run

```sh
ssh -t hal0 'cd /opt/hal0 && make proto-ttft'
ssh hal0   'cd /opt/hal0 && BASE=http://127.0.0.1:8088 TOKEN=… make proto-ttft-live'
```

(The `-t` flag is needed for the TUI — raw-mode stdin needs a PTY.)

## How it relates to production code

```
   scripts/prototype_ttft/metrics_core.py     ← teaching mirror
                    ║
                    ║   same data model
                    ║   (SlotSamples, FleetMetrics)
                    ║
   src/hal0/slots/ttft_samples.py             ← production
                    │
                    ▼
   src/hal0/api/routes/v1.py
     ├─ _dispatch_and_forward     records t_start
     └─ _instrument_streaming_…   marks first-chunk → appends sample
                    │
                    ▼
   src/hal0/api/routes/slots.py
     └─ /api/slots/metrics        surfaces per-slot ttft + kv_cache
                    │
                    ▼
   ui/src/components/SlotCard.vue          (per-slot TTFT, KV%)
   ui/src/views/Dashboard.vue              (fleet avg TTFT, avg KV%)
```

The prototype and production share the data model on purpose. If you
change one, mirror the other so the TUI doesn't drift from what the
API actually does.

## What the keys exercise

| Key   | Production analog                                              |
|-------|----------------------------------------------------------------|
| `1-5` | `_dispatch_and_forward` records `t_start` on a streaming POST  |
| `qwert`| First non-empty SSE chunk emitted by `_counting()` wrapper    |
| `s`   | 4 concurrent chats hitting primary with mixed prefill cost     |
| `k/d` | llama-server's `kv_cache_usage_ratio` gauge moving             |
| `c`   | Client cancellation before first chunk (no sample recorded)    |
| `a`   | The 60s sample window expiring on quiet slots                  |
| `R`   | Server restart wipes all samples (`app.state.ttft_events`)     |

## Don't delete

This was kept around after the May 2026 prototype landed because the
aggregation rule is the kind of thing that gets revisited (window
length, weighting choices, what counts as a "slot with data"). The
TUI is faster than a unit test for catching "huh, that's misleading"
cases before they ship.
