# Lane: routing (stateful, order 2)

The OpenAI-compatible surface and the dispatcher. This is where hal0 either is or is not a drop-in
endpoint, so wire-format correctness matters more than model output quality — the test models are
tiny and their answers are irrelevant.

## Checks

1. **Model refs.** `GET $API/v1/models` — record the id set. Then `POST /v1/chat/completions`
   (non-streaming, `max_tokens` ~40) with each plausible ref shape: the slot name (`brain`), the
   raw model id, and the namespaced alias (`hal0/brain`). Which resolve? Which 404? Is the error
   JSON clean and actionable? Any ref advertised in `/v1/models` that is not servable is a
   finding.
2. **Streaming.** Same request with `stream=true`: SSE chunks well-formed, `[DONE]` terminator
   present, no truncation.
3. **Embeddings** — regression `profile-flags-argv` (#1787). `POST /v1/embeddings` against the
   embed slot's ref: returns a vector, dimensionality sane, a batch of 2 inputs works. rc.4
   returned 501 "start with --embeddings" while the slot reported ready.
4. **Rerank.** `POST` the rerank route with a query and three documents: scores returned and
   ordered sensibly. Same 501 failure mode in rc.4.
5. **Legacy.** `POST /v1/completions` — supported, or a clean documented error.
6. **Dispatcher behaviour on an unloaded model.** Request a model that is registered but NOT
   loaded. Watch the hal0-api journal during the call. Per ADR-0023 Rule 9 the anchor slot
   answers and discloses the substitution in the response `model` field — that is by design
   (see `known-issues.yaml`). What you are checking is that the disclosure actually happens and
   the journal's `resolution_path` names a slot that is really loaded.
7. **Tool-call passthrough.** A chat completion with a trivial `tools` array: does the request
   reach the runner with tools intact, or is it silently stripped? Check the runner's own view,
   not just the response. Silent stripping is the shape of #1789.
8. **Concurrency.** Four parallel short chat requests to one slot: all complete, no 5xx, no
   interleaved corruption.
9. **Timeouts and long generations.** One request with a large `max_tokens` — does anything in
   the path (proxy, gateway, client default) cut it off mid-stream without saying so?

## Leave behind

Brain and embed loaded and healthy. Any model you caused to auto-load, unload again.
