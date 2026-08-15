# Lane: routing (stateful, order 2)

The OpenAI-compatible surface and the dispatcher. This is where hal0 either is or is not a drop-in
endpoint, so wire-format correctness matters more than model output quality — the test models are
tiny and their answers are irrelevant.

## Checks

**Pick your workhorse slot first — and run the coherence canary on it** (see `_shared.md`).
Measure the generation rate of each ready chat slot and use the fastest for the wire-format
checks. On a CPU box the brain F16 model needs ~98 s for eight tokens; using it naively consumes
the entire lane budget. In rc.6 this lane green-lit a box that never produced language — valid
SSE and tok/s over pure garbage; the canary is the 2-second guard against that.

1. **Model refs.** `GET $API/v1/models` — record the id set. Assert it is a subset of
   `hal0 model list` ids UNION `hal0 slot list` names (one `comm -23`); rc.5 advertised three
   ghost ids belonging to deleted slots, each of which hard-404s
   (regression `v1-models-never-evicts`, #1837). Then `POST /v1/chat/completions`
   (non-streaming, `max_tokens` ~40) with each plausible ref shape: the slot name (`brain`), the
   raw model id, and the namespaced alias (`hal0/brain`). Which resolve? Which 404? Is the error
   JSON clean and actionable?
2. **Streaming.** Same request with `stream=true`: SSE chunks well-formed, `[DONE]` terminator
   present, no truncation.
3. **Embeddings** — regression `profile-flags-argv` (#1787). `POST /v1/embeddings` against the
   embed slot's ref: returns a vector, dimensionality sane, a batch of 2 inputs works. rc.4
   returned 501 "start with --embeddings" while the slot reported ready.
4. **Rerank.** `POST` the rerank route with a query and three documents: scores returned and
   ordered sensibly (use the seeded `bge-reranker-v2-m3-q4_k_m`; the curated base row is
   unloadable — regression `curated-reranker-base-row-unloadable`). Same 501 failure mode in
   rc.4. Probe the **unversioned** `/rerank` as well as `/v1/rerank` — Cohere- and
   Jina-compatible clients default to the unversioned path and hal0 answers it with 405. Record
   it as pass or warn deliberately rather than discovering it ad hoc. Also time the COLD path:
   a rerank issued while the slot is offline/autoloading must produce first response bytes
   inside the documented load window, and a crash-looping slot must end in a structured 503 +
   Retry-After, never a connection with no bytes past 240 s (see the rc6_note on
   `known-issues: crash-loop-warming-180s-window`).
5. **Legacy.** `POST /v1/completions` — supported, or a clean documented error.
6. **Dispatcher behaviour on an unloaded model.** Request a model that is registered but NOT
   loaded. Watch the hal0-api journal during the call. Per ADR-0023 Rule 9 the anchor slot
   answers and discloses the substitution in the response `model` field — that is by design
   (see `known-issues.yaml`). What you are checking is that the disclosure actually happens and
   the journal's `resolution_path` names a slot that is really loaded.
7. **Error messages must not contradict the registry.** For every 4xx the dispatcher returns,
   check the top-level `error.message` against `hal0 model list`: it must never claim a model is
   "not found in registry" for an id that IS registered. Cheap invariant, caught a real defect
   in rc.5 (#1840 item 7). Add the capability-mismatch shape: a chat completion naming each
   registered NON-chat model (embed, rerank types) — with the embed slot OFFLINE it must 404
   cleanly; with it loaded, the raw upstream 500 pass-through is adjudicated
   (`known-issues: chat-to-embed-slot-500-passthrough`) — read that entry's still_report_if
   before filing.
8. **Tool-call passthrough.** A chat completion with a trivial `tools` array: does the request
   reach the runner with tools intact, or is it silently stripped? Compare the response through
   `:8080` against the same request sent **directly to the runner port** on `127.0.0.1` — the
   gateway response alone cannot distinguish passthrough from re-synthesis. Silent stripping is
   the shape of #1789. Repeat with `tool_choice: "required"` — a sharper stripping detector when
   the test model will not call tools of its own accord (note: llama.cpp b10297 + the brain-sft
   template ignores tool_choice=required entirely; that is a runner behaviour for the brain lane
   to judge, not gateway stripping).
9. **Concurrency.** Four parallel short chat requests to one slot: all complete, no 5xx, no
   interleaved corruption.
10. **Timeouts and long generations.** One request with a large `max_tokens` — does anything in
    the path (proxy, gateway, client default) cut it off mid-stream without saying so? A
    non-streaming request cut off at `[dispatcher] direct_read_timeout_s` (300 s default) is
    by-design (`known-issues.yaml: dispatcher-300s-read-timeout`); measure the elapsed time
    before filing, and note that streaming is exempt.

## Leave behind

Brain and embed loaded and healthy. Any model you caused to auto-load, unload again.
