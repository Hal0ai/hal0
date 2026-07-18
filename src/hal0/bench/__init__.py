"""hal0.bench — the in-tree benchmarking system (design: docs/archive/handoffs/benchmark-system-design-2026-07-05.md).

Keeps a throughput/latency benchmark dataset *current by itself*: an operator
(or the Hermes agent) declares suites once (§4), a pure-function planner diffs
those suites against a content-addressed result store to find the exactly-stale
cells (§6), and a resumable runner drives them through hal0's privileged seam —
the `hal0-benchctl` sudo wrapper for Tier A llama-bench sweeps and the
installed `server_ab.py` for Tier B/C live-server measurements — recording a
rich per-run schema-2 record (full provenance + raw per-repetition samples +
telemetry, not just a median) into an append-only `records.jsonl`. From that
store it publishes the public model-roster contract and flags regressions.

Ported in-tree from the out-of-tree benchlab lab repo (Hal0ai/hal0-bench) on
2026-07-10; the record schema, store layout, and CLI verbs are unchanged.
State root: `/var/lib/hal0-bench` (override: $HAL0_BENCH_STATE, legacy
$BENCHLAB_STATE). Surfaces: `hal0 bench <verb>` (cli.py) and
`/api/benchmarks/*` (hal0.api.routes.benchmarks).
"""

__version__ = "0.1.0"
