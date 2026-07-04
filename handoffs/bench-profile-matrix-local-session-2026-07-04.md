# Local-session prompt: run the profile re-tune bench matrix and apply the results

**How to use this file:** open a Claude Code session **on the Strix Halo box**
(local CLI or desktop app, repo checked out, hal0 installed and serving) and
paste the prompt below verbatim. Everything from the line after `--- PROMPT ---`
to the end of the file is the prompt. It is written to be self-contained — the
local session does not need this conversation's context.

Prerequisites on the box (all already true on a current install):
- hal0 ≥ the release carrying the virtual-seeds + MTP-separation work
  (`profile-matrix.sh` and `server_ab.py` present in `/usr/lib/hal0/bench/`,
  or run them from the repo's `installer/bench/`).
- The `hal0-benchctl` sudo seam installed (`sudo -n /usr/lib/hal0/bin/hal0-benchctl help`).
- A window where brief GPU downtime is acceptable (`--exclusive` stops and
  restarts serving slots around each sweep).

--- PROMPT ---

You are working on the hal0 repo on this Strix Halo box (gfx1151, 128 GB
unified memory). Your job: execute the seed-profile re-tune benchmark matrix,
interpret the results, apply the WINNING flag changes to the seed profiles,
refresh the bench numbers, and open a PR. The research and rationale live in
`handoffs/llamacpp-strix-halo-profile-consolidation-2026-07-04.md` (§2 facts,
§3 proposed flags, §6.3 the matrix) — read it first. The bench tooling docs are
`installer/bench/README.md`, and the `hal0-bench` / `hal0-tune` agent skills
apply throughout: measure, don't guess; one variable at a time; a change that
doesn't beat baseline beyond noise is dropped.

Work on a fresh branch off latest `main` named
`claude/profile-flag-retune-bench`. Commit as you complete each phase.

## Phase 0 — Preflight (10 min)

1. `git pull`, confirm `SEED_PROFILES` in `src/hal0/config/schema.py` still
   matches the flags this matrix targets (rocm/rocm-dnse/rocm-moe/vulkan/
   cpu-llm + the embed/rerank lanes; MTP bundle via `build_mtp_flag_bundle`).
2. Confirm the seam works: `sudo -n /usr/lib/hal0/bin/hal0-benchctl help`.
3. Pick the profile-class representative models actually installed under
   `/mnt/ai-models` (check with `ls`): a 35B-A3B MTP MoE (CROWN or ACE-SABER
   build), the dense Qwen3.6-27B, and note which slots serve them
   (`GET http://127.0.0.1:8080/api/slots`). If the defaults inside
   `profile-matrix.sh` don't match what's installed, pass `--moe/--dense`.
4. Record the CURRENT baseline: today's `PROFILE_BENCH` numbers in
   `src/hal0/config/schema.py` and the current `/var/lib/hal0/benchmarks/SUMMARY.md`
   if present.

## Phase 1 — Tier A: llama-bench cells (1–2 h GPU time)

Run: `installer/bench/profile-matrix.sh` (or `/usr/lib/hal0/bench/profile-matrix.sh`).
Use `--dry-run` first to sanity-check the seam commands, then run for real
(needs the GPU to itself; `--exclusive` handles stop/restart).

Cells and the decision each one feeds:
| Cell | Question | Feeds |
|---|---|---|
| moe-batch | is `-b 4096 -ub 4096` the MoE pp win, or does `-ub 2048` hold? | `rocm-moe` `-b/-ub` |
| dense-batch | confirm `-b 8192 -ub 2048` for dense | `rocm-dense` `-b/-ub` |
| vulkan-ub | is RADV's sweet spot 1024 (vs seeded 512)? | `vulkan` `-ub` |
| kv-rocm / kv-vulkan | symmetric q8_0 vs f16 at 32k depth, per backend | keep q8 KV on rocm; document q8 as valid Vulkan override |
| threads | `-t 8` vs `16` (expect ~noise at full offload) | drop `--threads-batch 32`, keep `--threads 16` |

Then `sudo -n /usr/lib/hal0/bin/hal0-benchctl aggregate` and read
`/var/lib/hal0/benchmarks/SUMMARY.md` + `index.json` (sweep rows are tagged
`sweep`; cells are distinguished by their flag values in the row metadata).

Decision rule: adopt a value only if it beats the current seeded value by more
than the run-to-run spread (llama-bench reports stddev; treat < ~3% as noise).
Record pp512 AND tg128 — a pp win that costs tg needs a judgment call, note it.

## Phase 2 — Tier B: server-level A/Bs (1–2 h)

Use `installer/bench/server_ab.py` (talks to hal0-api + the slot port; restores
the slot's original extra_args automatically). Run each against the slot named
in Phase 0.3; make sure no OTHER GPU slot is serving during measurements.

1. **MTP draft depth** (on an MTP slot — MoE first, then the dense MTP slot):
   `./server_ab.py --mode ab --slot <slot> --variant "n-max-2:--spec-draft-n-max 2" --variant "n-max-4:--spec-draft-n-max 4"`
   → compare `predicted_per_second` and `draft_acceptance_pct`. Upstream sweet
   spot is 2–3; the seeded bundle uses 4. If n-max 2 wins or ties, change the
   default in `build_mtp_flag_bundle` (`src/hal0/config/schema.py`).
2. **Cache reuse**: `./server_ab.py --mode reuse --slot <agent-slot>`
   → compare the second call's prompt timings between cache-reuse-256 and 0.
   If 256 wins clearly, add `--cache-reuse 256` to the rocm chat profiles'
   flags (NOT vulkan — it must stay Gemma-safe; Gemma + cache-reuse has
   upstream bugs, see the consolidation handoff fact 9).
3. **Poll**: `./server_ab.py --mode ab --slot <slot> --variant "poll:--poll 100 --poll-batch 1" --variant "no-poll:"`
   → if within noise, DELETE `--poll 100 --poll-batch 1` from rocm-dnse/rocm-moe
   (simpler flags win ties).
4. **Embed/rerank sanity** (after pointing an embed and a rerank slot at the
   new `embed`/`rerank` seed profiles):
   `./server_ab.py --mode embed --slot <embed-slot>` and
   `./server_ab.py --mode rerank --slot <rerank-slot>`
   → these are pass/fail sanity + latency baselines. A zero-spread rerank
   score means a bad GGUF conversion or a combined embed+rerank instance —
   fix the slot, not the profile.

## Phase 3 — Apply the winners (code changes)

All flag edits go in `SEED_PROFILES` in `src/hal0/config/schema.py` ONLY
(seeds are virtual — profiles.toml must not be touched; the overlay ships the
change to every install). For each adopted change:

1. Update the profile's `flags` string, and the `# comment` above it with the
   measured justification (this repo's house style — see the existing entries).
2. Also apply the already-research-validated non-bench changes from handoff §3
   that this bench run CONFIRMS or doesn't contradict: `-ngl 999` explicit on
   all GPU profiles, `--jinja` on all LLM profiles, threads simplification.
3. Update `PROFILE_BENCH` in the same file with the NEW measured tg t/s per
   re-tuned profile (this is the dashboard's card hero number — never leave a
   stale value next to new flags).
4. If MTP n-max changed: update `build_mtp_flag_bundle` and the
   `test_mtp_bundle_literal_match` / mtp_override tests.
5. Check golden argv tests: `pytest tests/providers tests/config tests/profiles -q`
   — update pinned flag strings deliberately (each is a documented behaviour
   change, name it in the commit message).
6. Run the full gate: `pytest tests/ -q`, `ruff check src tests`,
   `ruff format --check src tests`.

## Phase 4 — Verify serving still works (30 min)

1. Restart the main chat/agent slots on the re-tuned profiles; confirm
   `/health`, then run one real completion through the dispatcher
   (`POST /v1/chat/completions`) per slot class: MoE-MTP, dense-MTP, vulkan,
   embed, rerank.
2. Watch `journalctl -u hal0-slot@<name>` for the launch argv — confirm the
   new flags are exactly what launches (the resolved-command preview in the
   dashboard slot drawer must agree).
3. Confirm MTP auto-gating: the MTP slots show `--spec-type draft-mtp` in
   their argv; a non-MTP model slot on the same profile does NOT.

## Phase 5 — Ship

1. Update `handoffs/llamacpp-strix-halo-profile-consolidation-2026-07-04.md`
   §6.3 status: append a short "measured results" table (cell → winner → delta).
2. CHANGELOG entry under `[Unreleased]` summarizing the re-tuned flags and new
   bench numbers.
3. Commit(s) with the measured numbers in the message, push
   `claude/profile-flag-retune-bench`, open a PR titled
   "profiles: bench-driven seed flag re-tune (Strix Halo matrix results)".
   Include the SUMMARY.md excerpt and the server-ab JSON medians in the PR body.

Constraints and cautions:
- NEVER adopt asymmetric KV quant (K≠V) — known slow path on both backends.
- MTP requires `--parallel 1` (upstream constraint) — keep it in MTP-bearing
  profiles.
- Gemma models must stay on f16 KV via per-model registry defaults — do not
  let a profile-level `-ctk/-ctv q8_0` be the only thing between a gemma slot
  and the 10x pp trap; check `registry/curated.py` defaults if you touch KV.
- If a sweep contradicts the research (e.g. rocWMMA FA regressions at depth),
  trust the local measurement and say so in the PR.
- Don't run benches while anything else uses the GPU; `--exclusive` exists for
  a reason. Restore serving when done (Phase 4 doubles as the check).
