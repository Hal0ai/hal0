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
   registry) and record which exist and when each was created. rc.6 proved the seeding write
   runs at install boot but can land in the volatile pgvector fallback (hindsight cold start
   outruns the boot probe) and never gets replayed after `provider_healed` — the bank then only
   appears on a later hal0-api restart. Two remedies are both acceptable, so accept EITHER: (a)
   the durable `agents` bank exists with `created_at` ~= install time (the boot write landed
   durably first try), or (b) the durable bank exists, carries the expected identity cards, and
   its `created_at` is <= the timestamp of the first `provider_healed` journal line (the heal
   path replayed the idempotent card publish). Only fail this check if the bank is STILL absent,
   or exists but is missing cards, after a `provider_healed` line has already appeared in the
   journal. Regression `agents-bank-boot-writes-lost`; grep the boot journal for
   `boot_degraded|degraded_write|provider_healed` to attribute it.
3. **Fix the routing.** Assign a chat model to the utility slot and load it. Confirm the
   hindsight target ref now routes (`POST /v1/chat/completions` with that exact ref). If it
   still does not resolve, find what ref *does* and report the mapping gap precisely. Then
   RETRY THE SAME check-1 marker through the newly-bound target — do not treat check 1's
   pre-routing-fix failure and this post-fix attempt as redundant. The two paths can fail in
   genuinely different ways (a silent dead-letter before routing existed vs a loud 400 after
   routing exists — see regression `hindsight-response-format-400-via-thinking-policy`) and
   testing only one misses half the picture.
   Before assuming a post-fix failure is memory-specific, diff a PLAIN chat call against the
   exact slot/ref hindsight extraction is bound to, in the SAME time window as a failing retain:
   `POST /v1/chat/completions` with a trivial prompt (no `response_format`) against that ref.
   If plain chat works but the retain still 400s, the defect is in the STRUCTURED-OUTPUT request
   hindsight (or hal0-api's rewrite of it) sends, not in the model or slot — this single A/B is
   what distinguished "model/slot broken" from "the extraction request itself is broken" in
   rc.7. Also cross-check hindsight's OWN port directly:
   `POST http://127.0.0.1:9177/v1/default/banks/<bank>/memories/recall` against the hal0-wrapped
   `/api/memory/search|list|recall` for the same query — a mismatch tells you immediately whether
   a query-surfacing defect lives in hal0's REST layer or in the underlying hindsight engine.
4. **Retain, then prove it LANDED — and prove what landed is GROUNDED.** Store a memory
   containing a literal marker, e.g. *"the <BOX> validation marker is MARKER-7734"*. Do not stop
   at the HTTP 200 and the operation id — that is what made rc.5's headline defect invisible to
   every check. Poll until the store is actually non-empty: `GET /api/memory/list` returns items
   AND the write bank's `fact_count` is non-zero, with an explicit deadline. **An op still
   `processing` past the deadline is a `fail`, not a `skipped`.** Regression
   `memory-extraction-never-lands` (#1834) — and BEFORE blaming memory for a non-draining queue,
   run the coherence canary on the extraction slot's model (`_shared.md`); a garbage generator
   cannot terminate any extraction and that is the upstream defect, not this one. When facts DO
   land, verify every fact stored under your document id derives from the marker text — rc.6
   found the extraction prompt's own few-shot examples and Narrator scaffolding stored as facts
   on an undersized anchor (`known-issues: memory-extraction-quality-is-anchor-dependent`,
   regression `memory-extraction-ctx-preflight-missing`).
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
   broken situation. Two additions: run the POSITIVE path too — once an op reaches `failed`,
   `hal0 memory ops retry --all-failed` must actually queue it (status back to processing,
   retries reset), which no run has asserted; and diff what the CLI prints for each 4xx against
   `details.upstream.detail` in the raw REST body — the 409's actionable detail is lost in the
   CLI today (#1840 item 1).
10. **Disable / enable.** `hal0 memory disable` then `enable`, checking `/api/memory/engine`
    between steps. Deferred-apply is by-design (`known-issues.yaml:
    memory-enabled-is-restart-scoped`) — record whether POST /api/memory/add still 200s in that
    window, and whether the restart actually disables the subsystem.
11. **Bank hygiene and error surfacing.** A nonexistent bank must 404 on its sub-resources, not
    return an empty 200 (the api lane sweeps all of them; spot-check here — `/operations` and
    `/config` diverge from the bank root, and `/config` even fabricates default values for a
    bank that 404s one path up). Send a malformed add (wrong field name) — the error should name
    the field, not surface as `system.internal`.

## Leave behind

Memory enabled, utility slot loaded, brain healthy. Note the marker you stored and the state of
every operation you created — the hermes lane will look for its own marker separately.
