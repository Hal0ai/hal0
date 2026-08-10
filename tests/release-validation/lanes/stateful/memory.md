# Lane: memory (stateful, order 4)

The hindsight-backed memory subsystem: retain, recall, search, banks, and the async operation
pipeline. rc.4's memory findings were all about the **fresh-install window** — the period before
any model is loaded — so start there before you fix the environment.

## Order matters

Do step 1 **before** loading anything, or you destroy the evidence.

1. **Fresh-install window** — regression `memory-retain-dead-letter` (#1792). With the utility
   slot in its as-shipped state, attempt a retain. Then inspect the operation: does it queue and
   wait for routing, retry with backoff, or burn its retries and dead-letter permanently?
   Record `hal0 memory status` — a pristine install reporting "Writes FAILING" is a finding.
   Also record what `HINDSIGHT_API_LLM_MODEL` points at and whether that target ships with a
   model assigned.
2. **Baseline inventory.** `hal0 memory status`, `hal0 memory --help`, `GET /api/memory/engine`,
   `/api/memory/banks`, `/api/memory/list`. A fresh box should have zero failed operations —
   rc.4 had two on arrival.
3. **Fix the routing.** Assign a chat model to the utility slot and load it. Confirm the
   hindsight target ref now routes (`POST /v1/chat/completions` with that exact ref). If it
   still does not resolve, find what ref *does* and report the mapping gap precisely — that
   mismatch was the root cause in rc.4.
4. **Retain.** Store a memory containing a literal marker, e.g. *"the CT151 validation marker is
   MARKER-7734"*. Wait for the async op; check its status through to completion.
5. **Recall and search** — regression `fact-extraction-strips-literals` (#1794). Query for the
   concept ("what is the CT151 validation marker?") and for the literal ("MARKER-7734").
   Does the literal come back? State explicitly whether it came from extracted facts or from
   the document fallback: the fallback is a workaround, and a durable fix needs server-side
   content search that does not exist yet.
6. **Dead-letter recovery.** Retry any failed operations (`hal0 memory ops retry`, the
   operations API, or the MCP `memory_operation_retry` tool). Do they recover losslessly? If
   there is no recovery path at all, that is the finding.
7. **Disable / enable.** `hal0 memory disable` then `enable`, checking `/api/memory/engine`
   between steps. Does the API degrade gracefully and recover cleanly, or does something stay
   broken?
8. **Bank hygiene.** Which banks exist on a fresh box, and when are they created? rc.4 only
   created the `agents` bank on an hal0-api restart. Confirm bank stats are real — a nonexistent
   bank must 404, not return zeroed stats with a 200.
9. **Error surfacing.** Send a malformed add (wrong field name). The error should name the
   field, not surface as `system.internal`.

## Leave behind

Memory enabled, utility slot loaded, brain healthy. Note the marker you stored — the hermes lane
will look for its own separately.
