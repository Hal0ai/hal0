# NOTES — answers from the prototype session

Fill these in once you've driven the TUI (`make proto-ttft`) and run
the live probe (`make proto-ttft-live`). When the answers feel
solid, lift `metrics_core.py` into `src/hal0/slots/ttft_samples.py`
and delete this directory.

## TUI verdict

- Does **avg-TTFT-across-slots-with-data** read sensibly under the
  burst (`s`) → idle → age-out (`a`) sequence?
- Does the per-slot **current_ttft** decay to `—` after `window_s`
  (60s default), and is 60s the right window for the dashboard?
- Are there cases where one bad slot drags the fleet avg in a way
  that's misleading? Should KV-cache use a weighted avg (by inflight
  requests, or by model size) instead of equal-weight?
- Inflight cancel (`c`) keeps the sample but evicts the inflight
  entry — does that match how `_dispatch_and_forward` will signal
  cancellation in practice?

## Live probe verdict

- Client TTFT vs the server-side measurement that will live in
  `_instrument_streaming_throughput` — what's the dispatch overhead
  in practice? (Subtract `t_first_chunk_client - t_first_chunk_server`
  ≈ network + uvicorn round-trip.)
- `kv_cache_usage` from `/api/slots/metrics` — does it actually move
  when a long-context request is in flight? Does it decay back to 0
  on idle, or stick (KV cache is reserved, not zeroed)?

## Open questions to settle before UI wiring

- [ ] Where does TTFT sampling live? Three candidates:
      (a) inside `_instrument_streaming_throughput` — but it doesn't
          know the request start time without a thread-local or a
          param passed in from `_dispatch_and_forward`.
      (b) `_dispatch_and_forward` records start → passes start_ts
          into `_instrument_streaming_throughput` via a kwarg → the
          first-chunk path records the delta. **Likely answer.**
      (c) An ASGI middleware that wraps every `/v1/*` call. Cleanest
          but invasive; harder to filter to streaming-only.
- [ ] Where do the TTFT samples themselves live? Options:
      (a) `app.state.ttft_samples` (mirrors `tps_events`). Simple.
      (b) New `app.state.fleet_metrics: FleetMetrics`. More
          self-contained but more refactoring.
      Recommend (a) for now: a `defaultdict(deque)` keyed by slot,
      storing `(monotonic_ts, ttft_seconds)`. Mirrors `tps_events`
      exactly so the existing pattern carries through.
- [ ] KV-cache % is already scraped — no change needed there beyond
      surfacing in the UI.
