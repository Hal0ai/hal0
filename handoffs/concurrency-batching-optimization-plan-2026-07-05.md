# Concurrency & continuous-batching optimization plan

**Status:** IN PROGRESS. **P1 plumbing landed** (PR #1067: per-slot `parallel`
field + `--kv-unified` emission + `server_ab.py --mode batch` + `workers`
deprecation; behavior-neutral by default). P2/P3 remain bench-gated on a box
GPU window. As of 2026-07-05 the on-box execution substrate changed: a custom
**ROCmFPX superset runner** now backs the FPX slots and resolves the plan's
fork-vintage unknown — see **§1a**. Grounded in (a) a source-read of the
dispatch/config/argv pipeline on main (post-v0.8.5b2), and (b) a web-research
pass verified against llama.cpp master source (fetched 2026-07-05) plus the
only published Strix Halo `-np` sweep data
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
   builds ≥ ~late-May 2026. As of 2026-07-05 this per-image check narrows to
   ONE runner: the ROCmFPX superset image `localhost/hal0-rocmfpx:7aa484a`
   (§1a). MTP single-stream is already proven on it (2.13x tg, 27B ROCmFP4);
   P2.0 now only needs the `-np>1` acceptance check on that one commit, not a
   sweep across the toolbox fleet.
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

## 1a. Execution substrate update (2026-07-05): the ROCmFPX superset runner

Between the plan being written and P1 landing, the box session built and
proved a **single custom runner that supersedes both the ROCm and Vulkan
llama.cpp lanes**. This is not a side quest — it changes what the P2/P3
bench cells run *on*, and it collapses the plan's messiest dimension (the
per-image fork-vintage matrix, fact 3 / risk 1) down to one known artifact.

**What landed on the box:**

- **Runner image** — `ciru-ai/ROCmFPX @7aa484a`, built twice (HIP-only, then
  HIP+Vulkan after a 1-line SPIR-V include fix), packaged as
  `localhost/hal0-rocmfpx:7aa484a` (7.5 GB, **one image serves both lanes**).
  Backups at `/mnt/lab/ROCmFPX/build-*`. Image is **local-only — not pushed
  to a registry.**
- **Models** — `/mnt/ai-models/chadrock3.6-27b-coder-rocmfp4-mtp/` (45 GB:
  27B ROCmFP4 dense + 35B ROCmFPX MoE + both mmproj), registered & tagged in
  `registry.toml` with quant/arch/MTP/KV/template metadata. Both are MTP,
  both carry vision sidecars.
- **Quant detector** — ROCmFP3/4/6/8 + ROCmFPX family resolver added to repo
  `src` (3 tests passing) and hot-deployed to the live venv.
- **Wiring** — profiles `rocmfpx-rocm` + `rocmfpx-moe`; slots `code-fpx`
  (dense 27B → ROCm lane) + `moe-fpx` (35B MoE → Vulkan lane), currently
  **disabled/staged**. Froggeric chat template sourced; a `-ngl` emission bug
  fixed. Live service resolves both slot commands correctly; custom profiles
  confirmed to survive updates (updater analysis).

**Single-stream numbers already measured (the np1 baseline for Tier C):**

| Test | Result |
|---|---|
| 27B ROCmFP4 → ROCm lane | ✅ correct output |
| 35B MoE ROCmFPX → Vulkan lane | ✅ 49.7 tg |
| Standard Q4_K_M → Vulkan lane | ✅ (superset of the old vulkan runner) |
| New vs old ROCm | **+10.8% prefill**, decode = |
| New vs old Vulkan | parity |
| MTP on vs off (27B) | **24.95 vs 11.73 tg = 2.13x** |

**Live-slot quick bench (2026-07-05, both slots enabled, MTP active,
~2k prefill + greedy 256 decode) — the current np1 production anchors:**

| Slot | Model | Lane | Prefill | Decode |
|---|---|---|---|---|
| `code-fpx` | 27B ROCmFP4 (dense) | ROCm0 | 289.8 t/s | 30.6 t/s |
| `moe-fpx` | 35B-A3B ROCmFPX (MoE) | Vulkan0 | 688.5 t/s | 69.3 t/s |

Two bench-design signals in these numbers:
- 27B decode 30.6 vs the earlier 24.95 MTP-on figure — **greedy decode
  inflates draft acceptance**. Tier B/C MUST run each MTP cell twice: a
  greedy lane (upper bound) and a production-sampler lane (temp/top-p as the
  slot actually serves), else we tune to a ceiling agents never reach.
- MoE 69.3 tg vs the 49.7 initial validation figure suggests **MTP also pays
  on the A3B MoE, on the Vulkan lane** (~1.4x if 49.7 was MTP-off — the run
  conditions differ, so this is UNPINNED). An explicit MTP on/off A/B on
  `moe-fpx` is a required Tier B cell before trusting it.

**Implications for this plan:**

- **P2.0 shrinks from a fleet sweep to a one-commit check.** Fact 3's ⚠
  ("check per image") had assumed N toolbox images of unknown vintage. There
  is now ONE runner behind the FPX slots; P2.0 is a single `-np 4
  --spec-type draft-mtp` startup probe against `hal0-rocmfpx:7aa484a`. If it
  starts, batched-MTP is unblocked on the box outright and D3's hard branch
  is dead code for these slots.
- **The Tier C backend dimension is now "lane," not "image."** Because one
  image serves both ROCm (HIP) and Vulkan lanes, the `{rocm, vulkan_radv}`
  axis of the D5 matrix is a launch-flag toggle on the *same* binary — no
  cross-image variance to control for. This is the cleanest possible A/B for
  fact 4's "Vulkan for 1–4, ROCm for 8–16" split.
- **`code-fpx` / `moe-fpx` are the concrete Tier C targets.** The matrix's
  `{MoE A3B, dense 27B}` × `{MTP on/off}` cells map 1:1 onto these two
  staged slots. `moe-fpx` needs ~31 GB GTT free — batching adds only KV-pool
  growth on top (fact 6), so Tier C's `-np` sweep must watch GTT headroom
  against `hal0-slot-agent` contention (see risk 5).
- **The 2.13x MTP win is single-stream.** It is the np1 anchor, not a
  batching result. The open question the runbook answers is whether that 2.13x
  *survives* `-np>1` on this runner, or whether batching's per-step
  expert-streaming (fact 5) erodes the speculation gain — precisely the
  gfx1151-first datapoint fact 3 flags as unmeasured anywhere.
- **Reproducibility gap (risk 6).** The runner image is local-only and the
  venv detector patch is overwritten on the next hal0 release (it lives in
  repo `src`, so it returns on the next build). Before Tier C numbers get
  published as roster/PROFILE_BENCH cells, the image provenance (`@7aa484a` +
  the SPIR-V fix) must be captured so the numbers are reproducible.

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
- P2.0: capability check — does `localhost/hal0-rocmfpx:7aa484a` (§1a, the
  one runner behind the FPX slots) accept `--spec-type draft-mtp -np 4`?
  (Startup succeeds vs the #22673-era error.) One probe, not a fleet sweep;
  records the result that gates D3's soft-vs-hard branch for `code-fpx`/
  `moe-fpx`. If any legacy toolbox images still back other MTP slots, they
  keep the per-image caveat.
- server_ab.py `--mode batch` + runbook Tier C matrix + acceptance criteria
  (D5), run against `code-fpx` (dense 27B ROCmFP4) and `moe-fpx` (35B MoE)
  with the ROCm/Vulkan lane as a launch-flag toggle on the same binary (§1a).
  The measured single-stream numbers (§1a table) are the np1 anchors; the
  headline question is whether the 2.13x MTP win survives `-np>1`. Feeds
  PROFILE_BENCH's future batched metric and the roster column.

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

1. **Fork build vintage** (P2.0) — LARGELY RETIRED for the FPX slots: they
   run one known runner (`hal0-rocmfpx:7aa484a`, §1a), so vintage is a single
   yes/no probe, not a fleet unknown. Residual risk only for any legacy
   toolbox-image MTP slots still in service; those keep `--parallel 1` until
   rebased (D3 hard branch).
2. **Vulkan np16 regression + CPU-sampling bound** — expect Tier C to show a
   knee; acceptance criteria exist so a knee produces a number, not a debate.
3. **`--fit` interplay** — explicit `-c` + `-np` on a tight box could OOM
   where auto-fit would have shrunk; unified KV + our explicit ctx resolution
   means we keep responsibility for the math (D2 hint shows it).
4. **Idle-purge vs pinned agents** — `--cache-idle-slots` clearing an idle
   agent's VRAM KV is correct for memory but adds a restore hiccup on its
   next turn; `--cache-ram` sizing (D2) is the mitigation; Tier C's
   shared-prefix trace measures the hiccup.
5. **GTT contention on `moe-fpx`** — the MoE slot needs ~31 GB GTT free at
   `-np 1`; a batched `-kvu` pool grows KV on top (fact 6). Tier C's sweep
   must run with `hal0-slot-agent` accounted for, and the acceptance gate
   should reject an `-np` that only wins by starving a co-resident slot.
6. **Runner reproducibility** — `hal0-rocmfpx:7aa484a` is local-only (backups
   at `/mnt/lab/ROCmFPX/build-*`) and the quant-detector venv patch is
   overwritten each release (returns from repo `src` on rebuild). Capture
   image provenance (commit + the SPIR-V include fix) before any Tier C
   number is published as a roster/PROFILE_BENCH cell, else the numbers are
   irreproducible.
