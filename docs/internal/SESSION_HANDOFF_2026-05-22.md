# Session handoff — 2026-05-22 (evening)

Working doc for multi-session sync ahead of host restart. Captures what shipped today, what's live on the LXC, and the one open decision.

## What shipped today

**Release:** `v0.2.0-alpha.3` — <https://github.com/Hal0ai/hal0/releases/tag/v0.2.0-alpha.3>

Phase 8 (Agents + MCP + Cognee memory) end-to-end:

| PR | Title | Merge SHA |
|---|---|---|
| #130 | feat(pi-coder): switch installer to Hal0ai/pi-mono fork | `09b5c74` |
| #131 | feat(agents): hal0-hermes wrapper replaces upstream `--hal0-config` probe | `8ea7c05` |
| #132 | fix(phase-8): credential route + memory dispatcher + logs redaction + tests | `af72db5` |
| #135 | fix(pi-coder): use `@earendil-works/pi-coding-agent` npm name | `1922d5d` |
| #136 | fix(hermes): pre-install gate checks upstream `hermes`, not wrapper | `60d49ed` |

Other landed in same window (parallel sessions):
- #122–#125, #128, #129, #133, #134 (slot-card HUD + journal panel + Lemonade ADR drafts + lemonade-spike docs)

## Live state on hal0 LXC (10.0.1.142)

```
/opt/hal0 @ 60d49ed → pulled to latest main
hal0-api 0.2.0a3 active
hermes-agent 0.14.0 via pipx → /usr/local/bin/hermes (symlinks)
hal0-hermes wrapper installed @ /usr/local/bin/hal0-hermes
pi v0.75.4 via npm → /usr/bin/pi
/var/lib/hal0/memory/cognee/ populated (sqlite + lance + kuzu)
```

Verified end-to-end via dashboard API:
- `POST /api/agents/install {name:hermes}` → `status:installed`, hermes.env + hermes.toml written
- `POST /api/agents/install {name:pi-coder, switch:true}` → `status:installed`, `/root/.pi/config.toml` written
- MCP admin server: **21 tools** registered (8 read / 5 write / 8 gated)
- MCP memory server: **4 tools**, Cognee 1.0.7 initialized

## Pivots vs original Phase 8 design

1. **Hermes integration changed shape.** Original ADR-0004 §6 assumed user-owned upstream → Hermes grows native `--hal0-config`. Reality: user does not have write access to `NousResearch/hermes-agent`. Pivoted to a hal0-owned `hal0-hermes` wrapper that env-file-injects `HAL0_*` into unmodified upstream `hermes`. Zero upstream changes required.

2. **pi-mono upstream renamed.** `badlogic/pi-mono` is now `earendil-works/pi` (monorepo). NPM package is `@earendil-works/pi-coding-agent` (bin `pi`). hal0 ships a hard fork at `Hal0ai/pi-mono` (mirror) for long-term ownership of the integration surface; installer pulls the upstream-renamed NPM name until we publish `@hal0ai/pi-coding-agent` (Phase 9).

3. **Both pivots backfilled into memory entries** — see "Memory updates" below.

## The one open decision: Lemonade migration

ADR-0006 / `docs/internal/lemonade-migration-plan.md` committed to **total replacement** of all six toolboxes with Lemonade Server. Spike findings (`docs/internal/lemonade-spike-findings-2026-05-22.md`) showed 5/7 modalities FAILED or strict downgrade: embed/rerank load-fail, ASR untested, GPU-Kokoro lost, FLM install silently fails. LLM-Lemonade-ROCm hit hermes-14b parity (the one bright spot).

Late-session re-grill with 4 specialist subagents (results captured in chat, not yet in repo):

- **Agent A (per-modality):** verdict = PARTIAL replacement, not total. Only LLM justifies migration now; embed/rerank/ASR/TTS/NPU should stay on existing toolboxes.
- **Agent B (hybrid topology cost):** quantified glue + risk per option. **Path 2 (LLM-only) recommended.** Path A (total replacement) = 2500–3500 LOC glue / risk 9/10. Path B (LLM-only) = 1500–2000 LOC / risk 4/10.
- **Agent C (alternative backends):** **Surprise — LocalAI never evaluated in original spike.** MIT, 46k stars, gfx1151-capable via llama.cpp/whisper.cpp/sd.cpp under hood, OpenAI + Anthropic compatible, covers LLM+embed+rerank+ASR+TTS+img in one process (no NPU). If NPU drops from v0.2, LocalAI is a strictly better fit than Lemonade.
- **Agent D (op hazards):** XDNA2 ✓ (Strix Halo IS XDNA2 — hazard 6 dissolves). Confirmed total replacement NOT survivable. LLM-only scope = ~11.5 eng-hr/mo recurring.

### Option matrix (refined post-re-grill)

| Path | LLM | Embed/Rerank | ASR/TTS/Img | NPU | Glue LOC | Toolboxes kept | Risk | Strategic |
|---|---|---|---|---|---|---|---|---|
| 1. LLM-only Lemonade | Lemonade | hal0 toolbox | hal0 toolbox | hal0 FLM | 1500–2k | 5 | 4 | safe, modest gain |
| 2. LocalAI for all iGPU | LocalAI | LocalAI | LocalAI | hal0 FLM | 1200–1800 | 1 | 5 | biggest consolidation, drops Lemonade entirely |
| 3. llama-swap composed | composed | composed | composed | hal0 FLM | 2000–2500 | 2–3 | 3 | most control, MIT, you own stack |
| 4. Hybrid Lemon+Local | Lemonade | LocalAI | LocalAI | hal0 FLM | 2000–2500 | 1 | 5 | best modality coverage; two backends |
| 5. Status-quo | hal0 | hal0 | hal0 | hal0 | 0 | 6 | 2 | no win |
| 6. **Spike LocalAI first** | TBD | TBD | TBD | TBD | TBD | TBD | TBD | de-risk before commit |

User picked: **graceful close + handoff** to align sessions before deciding. Default recommendation if we pick up cold: **Path 6 (spike LocalAI first)** since the spike data we have only covers Lemonade.

### Spike LocalAI runbook scaffold (for next session)

Mirror `docs/internal/lemonade-spike-runbook.md` shape. Validate on hal0 LXC 105 (gfx1151 + 100 GB unified):

1. Pull `localai/localai:latest-aio-gpu-hipblas` (or similar gfx1151-capable image).
2. Smoke each modality with hal0's existing model set:
   - LLM: hermes-4-14b-q5_k_m (compare to spike's tok/s baseline)
   - Embed: nomic-embed-text-v1
   - Rerank: bge-reranker-v2-m3
   - ASR: whisper-tiny
   - TTS: kokoro
   - Image: sd-cpp default
3. Record perf + load behavior + eviction policy + metrics surface.
4. Compare side-by-side with Lemonade spike + hal0 toolbox baseline.
5. Decide Path 1 vs 2 vs 4 with three data sets.

## ADR-0006 status

**INVALIDATED** by 4-agent re-grill but not yet amended in repo. Next session should:
- Amend ADR-0006 §Decision 1 ("Total provider replacement") to reflect the new option set (or supersede with ADR-0008).
- Update `docs/internal/lemonade-migration-plan.md` §PR sequence — current 16-step plan assumes total replacement and is wrong-shape for any of paths 1–4.

## Memory updates today

- `hal0_agents_v0.2_design.md` — Hermes upstream-PR-access assumption reverted; wrapper pivot documented.
- `pi_mono_upstream_rename.md` (NEW) — `badlogic/pi-mono` → `earendil-works/pi` + Hal0ai fork carries `parent=earendil-works/pi`.
- `feedback_caveman_dev_mode.md` (NEW) — when user invokes `/caveman`, brief spawned agents in caveman style and have them report caveman.

## Other open items (lower priority)

- 47 locked agent worktrees under `.claude/worktrees/` from prior sessions — disk hog, clean sweep with `git worktree remove` when stable.
- Dependabot moderate vuln on main (`security/dependabot/1`) — not triaged.
- v0.2.0-alpha.3 release is `isPrerelease:true`. Memory `hal0_v0.1.0-alpha-launch.md` notes the alpha-1 release was NOT marked prerelease so `/latest` resolved. Decide policy: keep alpha.x prerelease vs flip for `/latest` visibility.
- README.md:130 + PLAN.md:798 reflect the new agent install pivot (pushed in `2c3f6a8`). hal0-web CONTENT_BRIEF.md synced in `87f1c46`.

## Next-session pickup checklist

1. Read this doc.
2. `ssh hal0` → check `/opt/hal0` is still at `60d49ed` or later main HEAD.
3. Decide: Path 1 vs 2 vs 6 (recommended: spike LocalAI first).
4. If proceeding with any Lemonade/LocalAI path, amend or supersede ADR-0006.
5. Spawn implementation teams in parallel worktrees once strategy is locked.

## Session footprint

- 5 PRs merged (#130, #131, #132, #135, #136).
- 3 specialist agent teams spawned for Phase 8 close-out (team-hermes, team-pi-mono, team-closeout) — all reported PR-ready and cleaned up.
- 4 specialist plan agents for Lemonade re-grill — read-only analysis, results captured in this doc + chat.
- Live verify on hal0 LXC: dashboard agent install, MCP admin/memory enumeration.
- Doc syncs: PLAN.md, README.md (hal0), CONTENT_BRIEF.md (hal0-web).

## v0.2 ship-cut 2026-05-23

The Lemonade migration shipped. ADR-0008 / ADR-0009 / ADR-0010
landed in #154; the 22-PR adoption sequence (PR-2 #137 through PR-22
— docs sync) is closed. README + PLAN + CHANGELOG + the v0.2 upgrade
guide + release notes synced in PR-22. The "one open decision" in the
section above was settled by the post-grill adoption plan
(`docs/internal/lemonade-adoption-plan-2026-05-22.md`) — Path 4
(Lemonade for all iGPU + NPU modalities) chosen, ADR-0006 / ADR-0007
superseded.
