# Bench Phase 3 — OSS load-gen + eval adapters — Implementation Plan

> Executed subagent-driven: three independent adapter tracks (one agent each,
> isolated worktrees), orchestrator integrates behind the planner/runner seam
> and owns pyproject. Builds ON TOP of the Phase-2 branch (`h0/hal0-bench-overhaul`,
> PR #1761); its PR opens after #1761 merges.

**Goal:** GuideLLM replaces `server_ab.py` for HTTP serving perf (real TTFT/ITL
distributions, sweep concurrency); llama-benchy adds llama-bench-vocabulary
pp/tg×depth over HTTP; tool-eval-bench replaces `src/hal0/bench/evalrun.py`'s
hermes eval tier. Tool selection is SETTLED (operator sign-off, 2026-08 research);
versions re-verified 2026-08-09.

## Pins (verified 2026-08-09)

| Tool | Pin | Install | License |
|---|---|---|---|
| GuideLLM | `guidellm==0.7.3` | PyPI | Apache-2.0 |
| llama-benchy | tag v0.4.0 = sha `446dd42fde2ebbaa1d68a0dfe9dc1e5b833f95ad` | git+https://github.com/eugr/llama-benchy@<sha> (bus-factor 1 — pin the sha, consider vendoring at integration) | MIT |
| tool-eval-bench | tag v2.5.0 | git+https://github.com/SeraphimSerapis/tool-eval-bench@v2.5.0 | MIT |

Notes that bind the adapters:
- GuideLLM v0.7.x CLI: `guidellm run --backend … --profile kind=sweep|constant|concurrent|throughput|synchronous … --constraint kind=<name>,<opts> --output json "path=…"`; `benchmarks.json` is the authoritative record. Python 3.10–3.13. Do NOT build against pre-0.7 scenario files. (Entry-point name must be re-confirmed against the v0.7.3 tag by the adapter agent — research flagged one self-disagreement there.)
- llama-benchy: core pp/tg×depth JSON schema stable through v0.4.0; there is a formal JSON schema upstream — validate fixtures against it. Pin the tag sha, not HEAD.
- tool-eval-bench: scoring hardened ~2026-08-03 (tool-errors no longer credited) — scores from before that are NOT comparable; the adapter must stamp the tool version into every record. Python ≥3.11. Version string is setuptools-scm dev-style — parse leniently. Its optional dep on llama-benchy must not fight our pin (declare extras carefully at integration).

## Architecture (fixed for all three tracks)

- Each adapter is a self-contained module `src/hal0/bench/adapters/<tool>.py`
  (new package `src/hal0/bench/adapters/__init__.py`, empty) plus tests +
  captured fixtures under `tests/bench/adapters/`.
- An adapter owns exactly: (1) building the tool's CLI argv for a cell-shaped
  request, (2) running it via an injectable `runner` callable (same pattern as
  `harness.run_cell` — tests inject fakes, nothing in tests shells out to the
  real tool), (3) parsing the tool's output into the SAME pure result shape
  `parsers.Parsed` produces (reps list, summary stats, engine/config observed),
  ready for `runner._assemble`. Import `schema.py`/`parsers.py` types; do NOT
  modify them (report needed extensions instead).
- Adapters do NOT touch `runner.py`, `planner.py`, `cli.py`, `evalrun.py`,
  `pyproject.toml`, or each other's files. Integration (KNOWN_KINDS/_KIND_TO_MODE
  wiring, dependency additions, evalrun/server_ab retirement) is the
  orchestrator's follow-up once all three land.
- Fixtures: captured REAL output where the tool can run hermetically
  (tool-eval-bench is offline/deterministic; GuideLLM and llama-benchy can be
  pointed at a stdlib-http fake OpenAI/llama-server endpoint in a fixture-capture
  script committed under `tests/bench/adapters/capture_<tool>.py`). Every parser
  test runs from committed fixtures only — CI has no network and no tools.

## Tracks

- **Track G (GuideLLM)**: adapter for the serving-perf kinds (chat-style load):
  map cell (model/slot endpoint, concurrency profile, reps/duration constraint)
  to `guidellm run` argv; parse `benchmarks.json` into per-rep TTFT/ITL/TPOT/
  throughput + summary percentiles.
- **Track B (llama-benchy)**: adapter for HTTP pp/tg×depth: map (endpoint,
  pp/tg sizes, depth) to its CLI; parse its JSON (validate against upstream
  schema) into pp/tg reps + summary, depth-aware.
- **Track E (tool-eval-bench)**: adapter replacing the evalrun hermes tier:
  map (model endpoint, task selection) to its CLI; parse scores into the eval
  record shape `evalrun.py` currently produces (read it for the target shape);
  stamp tool version + scoring-era into every record.

Each track: red-first tests, ruff clean, `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/bench/adapters -p no:cacheprovider`, Conventional Commits, commit early.
