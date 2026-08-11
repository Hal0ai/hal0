# Lane: memory (stateful, order 4)

The hindsight-backed memory subsystem: retain, recall, search, banks, and the async operation
pipeline. rc.4's memory findings were all about the **fresh-install window** — the period before
any model is loaded — so start there before you fix the environment.

## Order matters, and it is a scheduling constraint on the whole run

Do step 1 **before** loading anything, or you destroy the evidence. In rc.5 an earlier stateful
lane had already bound a model to the `utility` slot, so the #1792 regression could not be tested
at all. Either this lane runs before any lane that touches `utility`, or the earlier lanes must
declare `utility` off-limits — say in your report which of the two you got.

## Checks

1. **Fresh-install window** — regression `memory-retain-dead-letter` (#1792). With the utility
   slot in its as-shipped state, attempt a retain. Then inspect the operation: does it queue and
   wait for routing, retry with backoff, or burn its retries and dead-letter permanently?
   Record `hal0 memory status`, and record what `HINDSIGHT_API_LLM_MODEL` points at and whether
   that target ships with a model assigned.
2. **Baseline inventory.** `hal0 memory status`, `hal0 memory --help`, `GET /api/memory/engine`,
   `/api/memory/banks`, `/api/memory/list`. A fresh box should have zero failed operations.
   Enumerate the banks you EXPECT on a fresh install (`shared`, and `agents` for the peer
   registry) and record which exist and when each was created — rc.5 had no `agents` bank at all
   after 16 h of uptime, and with no check the fleet has no signal on whether agent identity
   cards have anywhere to land.
3. **Fix the routing.** Assign a chat model to the utility slot and load it. Confirm the
   hindsight target ref now routes (`POST /v1/chat/completions` with that exact ref). If it
   still does not resolve, find what ref *does* and report the mapping gap precisely.
4. **Retain, then prove it LANDED.** Store a memory containing a literal marker, e.g. *"the
   <BOX> validation marker is MARKER-7734"*. Do not stop at the HTTP 200 and the operation id —
   that is what made rc.5's headline defect invisible to every check. Poll until the store is
   actually non-empty: `GET /api/memory/list` returns items AND the write bank's `fact_count` is
   non-zero, with an explicit deadline. **An op still `processing` past the deadline is a `fail`,
   not a `skipped`.** Regression `memory-extraction-never-lands` (#1834).
5. **Terminal state within a bound.** Independently of check 4, assert that every operation you
   create reaches `completed` or `failed` within a stated time. rc.4 dead-lettered and rc.5 hung
   in `processing`; a check written against either behaviour alone misses the other. Terminal-
   state-within-N is the invariant that survives both. Note that `hal0 memory ops list` shows a
   `batch_retain` PARENT alongside each `retain` child, so counts read double — that is
   bookkeeping, adjudicated (`known-issues.yaml: batch-retain-parent-rows-are-bookkeeping`).
6. **Status must not contradict ground truth** — regression `memory-status-green-while-empty`
   (#1833). Run `hal0 memory status` and `hal0 memory bank list` in the same breath and make the
   contradiction itself the assertion: "Writes landing" is a `fail` while the write bank's
   `fact_count` is 0 with ops outstanding, or while `journalctl -u hindsight-api` is emitting
   `[STUCK_STACK]`.
7. **Contention, measured not argued** — `known-issues.yaml:
   hindsight-extraction-shares-utility-slot`. Read `HINDSIGHT_API_LLM_MODEL`, resolve the serving
   slot's llama-server pid via `/proc/<pid>/cgroup`, then time a trivial chat turn to that same
   ref with and without a retain in flight. Also sample the runner's `/metrics`
   (`requests_processing`, `requests_deferred`) and `/slots` (`params.n_predict`). This is the
   only mechanical way to promote the by-design entry back into a finding, and it is currently
   reachable only by hand.
8. **Recall and search** — regression `fact-extraction-strips-literals` (#1794). GATED on check 4
   actually landing; if nothing landed, report `blocked` and point at #1834. Query for the
   concept and for the literal. State explicitly whether the literal came from extracted facts
   or from the document fallback — the fallback is a workaround, not the fix.
9. **Recovery surfaces.** Retry any failed operations (`hal0 memory ops retry`, the operations
   API, or MCP `memory_operation_retry`). Include the negative path: while an op is `processing`,
   `--all-failed` says "Nothing to retry." and `--id` 409s — that is by-design
   (`known-issues.yaml: memory-ops-processing-not-retryable`), but check the 409 body reaches the
   user and that a way to see the scheduled retry exists (`retry_count` / `next_retry_at`).
   "Nothing to retry" printed over a visibly wedged queue is a passing-looking output for a
   broken situation.
10. **Disable / enable.** `hal0 memory disable` then `enable`, checking `/api/memory/engine`
    between steps. Deferred-apply is by-design (`known-issues.yaml:
    memory-enabled-is-restart-scoped`) — record whether POST /api/memory/add still 200s in that
    window, and whether the restart actually disables the subsystem.
11. **Bank hygiene and error surfacing.** A nonexistent bank must 404 on its sub-resources, not
    return an empty 200 (the api lane sweeps all of them; spot-check here). Send a malformed add
    (wrong field name) — the error should name the field, not surface as `system.internal`.

## Leave behind

Memory enabled, utility slot loaded, brain healthy. Note the marker you stored and the state of
every operation you created — the hermes lane will look for its own marker separately.
