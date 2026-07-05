# Concurrency & continuous-batching optimization plan

**Status:** EXECUTION PLAN — researched, not yet implemented. Grounded in (a) a
source-read of the dispatch/config/argv pipeline on main (post-v0.8.5b2), and
(b) a web-research pass verified against llama.cpp master source (fetched
2026-07-05) plus the only published Strix Halo `-np` sweep data
(hogeheer499-commits/strix-halo-guide, raw CSVs). Companion to the
seed-profile consolidation handoff (2026-07-04); shares its bench-gated
shipping discipline.

**Problem statement.** hal0's slot model is architecturally correct for
concurrent callers — one shared llama-server per model, dispatcher fans many
clients in — but every llama.cpp seed profile pins `--parallel 1`, and the
dispatcher's serving wrapper is bookkeeping, not a gate. Concurrent agents
therefore serialize inside llama-server's single sequence slot and thrash one
prompt cache. The shared-server win is real; the continuous-batching win on
top of it is currently left on the table.

---

## 1. Research facts the plan is built on

Verified against llama.cpp master (2026-07-05) unless marked measured/inferred:

1. **Upstream default is now multi-client.** `-np` defaults to `-1 = auto` →
   **4 slots + unified KV** when unset. Any explicit `-np N` (including our
   `--parallel 1`) opts out of auto and gets split-KV unless `-kvu` is passed.
2. **Context division:** without `-kvu`, `--ctx-size C` is statically
   pre-sliced to C/N per slot (a long request fails even if other slots are
   idle). With `-kvu`, one shared pool of C tokens; every slot may use up to
   the full C; idle slots' KV is purged to the host prompt cache under
   pressure (`--cache-idle-slots`, default on; `--cache-ram` default 8 GiB).
   Total KV memory is identical either way.
3. **MTP × batching is NO LONGER mutually exclusive.** PR #22673 (May 16)
   hard-errored on `n_parallel > 1`; PR #22838 (May 11, parallel drafting) +
   #23269 (May 19, MTP cleanup) + #23287 removed the restriction — current
   master runs `--spec-type draft-mtp -np 8`. Upstream's own May caveat:
   correct but "highly suboptimal" pending refactor. **No gfx1151 benchmark
   of batched MTP exists — ours would be the first.** ⚠ Applies only to
   builds ≥ ~late-May 2026: whether OUR toolbox images (fork of
   rocm-7.2.4-rocmfp4-server vintage) carry the parallel-drafting commits
   must be checked per image before relying on it (runbook step P2.0).
4. **Measured Strix Halo scaling (MoE A3B, 4096 ctx/slot, 128 tok/req):**
   aggregate t/s at np1→2→4→8→16: Vulkan/RADV 58.8→96.1→138.8→170.9→189.7;
   ROCm 48.6→82.2→127.0→177.2→207.8. Qwen3-Coder-30B on Vulkan REGRESSED at
   np16 (173→130). Operational split from that guide: **Vulkan for 1–4
   concurrent, ROCm for 8–16.** Per-stream speed ≈ single-stream/N; p95 ITL
   grows from 16 ms (np1) to ~74–82 ms (np16). The Reddit "-np 8 sweet spot,
   16 worse" claim is backend-dependent, not universal.
5. **MoE batching scales sub-linearly by mechanism** (expert dispersion:
   batching streams more unique expert weights per step), consistent with the
   measured ~2.9x at np8. Dense scales closer to linear but from a far lower
   single-stream base here. High-np throughput is also CPU-sampling-bound
   upstream (backend-sampling work #17004 is the fix track).
6. **Slots are nearly memory-free.** Compute buffers scale with `-ub`, not
   `-np`; KV cost is set by the total ctx pool. A3B-class GQA at 131k q8_0 ≈
   6 GiB. On 128 GB with a 20 GB model, `-kvu -np 8 -c 262144` is comfortable.
7. **Prompt-cache machinery under concurrency already exists upstream:** LCP
   slot routing (`--slot-prompt-similarity`, default 0.10) pins same-prefix
   conversations to warm slots; `--cache-reuse` salvages chunks via KV-shift;
   host RAM cache restores evicted agents at memcpy speed. Agentic best
   practice: `-np` ≈ concurrently-active distinct contexts, generous
   `--cache-ram`, unified KV.
8. **Embed/rerank pattern:** `-c = np × max_input_len`, `-b = -ub =
   max_input_len` (non-unified split is CORRECT here — hard per-request
   guarantees, no shared-pool benefit for stateless single-pass scoring).
9. **Strategic watch:** upstream llama-server now has a router mode
   (multi-model spawn/route/autoload, `--models-max`) that overlaps hal0's
   slot manager. Track it; don't duplicate; consider it for a future backend.

In-tree facts:

10. `SlotConfig.workers` ("Number of parallel request workers", default 1) is
    DEAD — round-tripped and shown in the drawer, never emitted to argv.
11. The argv layer is ready: `-np` ↔ `--parallel` is in `FLAG_ALIASES`
    (last-wins dedup), so a slot-level emission cleanly overrides the
    profile's `--parallel 1`. `-kvu` needs adding to the alias table.
12. `_effective_mtp` has no parallel awareness; the drawer's MtpControl has a
    reason-line mechanism ready to carry a batching hint.
13. The bench stack has no concurrency dimension anywhere: PROFILE_BENCH,
    the roster board, llama-bench cells, and server_ab.py are all
    single-stream; the roster page explicitly says "one model at a time".

---

## 2. Design decisions

**D1 — `parallel` becomes a first-class slot field (not a profile flag).**
Concurrency is a workload property of the slot (an agent fan-in slot wants 4–8;
an interactive chat slot wants 1–2 for per-stream speed), exactly like `mtp`
and `context_size` are slot concerns. `SlotConfig.parallel: int | None`,
default None = inherit the profile's flags (today: 1). Emitted in the
`slot_overrides` argv segment (beats profile, loses to hand-authored
`extra_args`). The dead `workers` field is NOT repurposed — its haloai
semantics ("request workers") never meant sequence slots, and silently
activating a long-dead field on upgrade is a PS-2-class trap. Deprecate it:
drop from the drawer, keep round-tripping one release, log when it differs
from default.

**D2 — parallel > 1 implies unified KV + honest ctx semantics.**
When the effective parallel is N>1, emit `--parallel N --kv-unified` together.
Rationale: hal0's ctx_size is operator-authored as "the context this slot
has"; silent C/N splitting (research fact 2) would shrink every agent's window
by N behind the operator's back — the exact class of surprise the resolved-
command work exists to prevent. Unified keeps ctx_size meaning "shared pool,
each request may use up to C", which is also the right shape for bursty
agents. The drawer shows a computed hint: "N slots share this pool;
worst-case simultaneous full-ctx requests get ~C/N". Follow-on knob (same PR
or fast-follow): surface `--cache-ram` as a profile-flag bump for agent-class
profiles (16 GiB+ on a 128 GB box) so evicted agents restore from RAM.

**D3 — MTP × parallel: warn + bench, not a hard gate.**
Research fact 3 retired the hard mutual exclusion the earlier plan assumed.
Rule: `_effective_mtp` stays as is; when effective MTP is ON and effective
parallel > 1, emit a one-line launch log (`mtp.batched_speculation`) and the
MtpControl reason line notes "batched slot — speculation gain unverified on
this hardware". Hard-refuse ONLY if the image build predates parallel
drafting — and since we cannot cheaply introspect fork build vintage, P2.0
makes that an on-box check once, then the constraint is encoded as a profile
comment (images older than the checked build keep `--parallel 1` + MTP).

**D4 — seed profiles stay `--parallel 1` until the bench gate; slots carry
the concurrency.** Same discipline as the flag re-tune: no throughput-
sensitive default changes without our own numbers (fact 4 is another box,
other quants, other builds). Exception class: the runbook's expected outcome
is a recommendation like "agent-class slots: parallel 4–8 on ROCm" applied as
SLOT config on the box (and later, if warranted, a `parallel` default in the
agent seed slot TOML — not in profiles). Embed/rerank: document the
`-c = np × max_input` recipe in the profile comments now (fact 8; split KV
deliberate); actual `-np` for them is also a runbook cell.

**D5 — bench: `server_ab.py --mode batch` + runbook Tier C.**
Extend server_ab.py with a batch mode: N concurrent `/completion` streams
(shared long prefix + distinct suffixes to exercise slot routing), reporting
aggregate t/s, per-stream median, TTFT spread, and (when present) draft
acceptance — the four numbers that decide everything above. Runbook grows a
Tier C matrix: `-np 1/2/4/8/16` × {rocm, vulkan_radv} × {MoE A3B, dense 27B}
× {MTP on/off where supported}, plus one `--cache-ram`/`-sps` A/B on the
shared-prefix trace. Acceptance criteria written into the runbook: adopt
`parallel N` for a slot class only if aggregate ≥ 2x at ≤ 2.5x p95 ITL, and
keep interactive slots at low N regardless (per-stream ≈ 1/N is a product
decision, not a bug).

**D6 — surface it in the UI honestly.** Drawer gains a Parallel field
(number, restart-required, with the D2 pool hint); slot cards/provenance
already show resolved argv so `--parallel/-kvu` visibility is free; a later
nicety is exposing llama-server's `requests_deferred` metric on the card so
operators can SEE queueing (the symptom that motivates raising N).

**D7 — docs.** One "Concurrency & continuous batching" concept page (docs
site repo): shared-slot vs N instances, what `--parallel`/`--kv-unified` do,
the measured scaling table + backend split (fact 4), MoE caveat, MTP×batching
status, prompt-cache-under-concurrency behavior, and the roster-board
methodology note that all published numbers are single-stream. Roster board
gains a batched-throughput column once Tier C numbers exist.

---

## 3. Phased execution

**P1 — plumbing (no behavior change by default; ships any time):**
- `SlotConfig.parallel` (int|None, ge=1) + drawer field + slot_view entry;
  emitted as `--parallel N` (+ `--kv-unified` when N>1) in slot_overrides;
  `-kvu` added to FLAG_ALIASES; launch/preview parity via the shared
  assembler (free); golden tests incl. override-beats-profile and
  extra_args-beats-slot.
- D3 warn path in `_resolve_llama_scalars` (launch-gated, like the MTP
  auto-off breadcrumb).
- Deprecate `workers` (drawer removal + log-on-nondefault).
- Tests: argv goldens, MtpControl reason line, schema round-trip.

**P2 — measurement (on-box, GPU windows needed):**
- P2.0: per-image capability check — does the pinned toolbox build accept
  `--spec-type draft-mtp -np 4`? (Startup succeeds vs the #22673-era error.)
  Record per image in the bench notes; gate D3's soft-vs-hard behavior.
- server_ab.py `--mode batch` + runbook Tier C matrix + acceptance criteria
  (D5). Feeds PROFILE_BENCH's future batched metric and the roster column.

**P3 — adoption (bench-gated):**
- Apply winning `parallel` values to the box's agent-class slot configs;
  promote to seeded slot TOML defaults only if wins are decisive and stable.
- Embed/rerank `-np` per Tier C; profile comments updated with measured
  numbers (virtual seeds ship the comment/flag change to every install).
- Docs page + roster batched column (D7); CHANGELOG the behavior notes.

**Explicit non-goals now:** per-request priority (upstream has none),
dispatcher-side queueing/admission (llama-server's deferred queue + metrics
is the mechanism; duplicate nothing), NPU/FLM slots (single hardware
context), adopting upstream router mode (watch item only).

## 4. Risks / open questions

1. **Fork build vintage** (P2.0) — if the rocmfp4 images predate parallel
   drafting, MTP slots must keep `--parallel 1` until an image rebase; the
   plan degrades gracefully (D3 hard branch).
2. **Vulkan np16 regression + CPU-sampling bound** — expect Tier C to show a
   knee; acceptance criteria exist so a knee produces a number, not a debate.
3. **`--fit` interplay** — explicit `-c` + `-np` on a tight box could OOM
   where auto-fit would have shrunk; unified KV + our explicit ctx resolution
   means we keep responsibility for the math (D2 hint shows it).
4. **Idle-purge vs pinned agents** — `--cache-idle-slots` clearing an idle
   agent's VRAM KV is correct for memory but adds a restore hiccup on its
   next turn; `--cache-ram` sizing (D2) is the mitigation; Tier C's
   shared-prefix trace measures the hiccup.
