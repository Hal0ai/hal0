# hal0 Handoff — FLM Rework + Remaining Rework Phases

> **Date:** 2026-07-22 · **For:** Next session/agent picking up hal0 v1.0 work
>
> **Canonical state documents (read in this order):**
> 1. This handoff
> 2. `docs/rework/REWORK_BOARD.md` — live status
> 3. `docs/rework/REWORK.md` — finish line + per-lane DoD
> 4. `docs/rework/REWORK_BOARD_PROTOCOL.md` — single-writer rule, lane lifecycle
> 5. `docs/rework/flm-1.0-rework-plan.md` — FLM rework details
> 6. `docs/rework/onnx-npu-support-plan.md` — ONNX/OGA NPU plan (post-v1.0)
> 7. `docs/rework/memory-sync-batch.md` — 24 docs for hindsight import into `pi-coder::hal0` bank
> 8. `docs/rework/hal0-specs/` — per-lane specs
>
> **Board:** `docs/rework/REWORK_BOARD.md` is single-writer. If you don't hold the token,
> deliver row deltas to the orchestrator; never edit the board directly.

---

## 1. Git State — What's Where

```
main:                                    f07a1cb6
  └─ merge/rework-descar-into-main:      b91567fc  (24 commits ahead, PR #1330 open)
       └─ rework/descar:                 (identical to merge branch)
```

**PR #1330:** `merge/rework-descar-into-main` → `main`
- 24 commits: seeded-profile 1.0 (#1328), registry-prune (#1305), P3-runtime-db inc4
- 3 test fix commits: device_class=None, intent text, fit degradation, Ruff F631, CLI docs parity
- 1 CI trigger commit (empty)
- **CI status:** running — python+UI were failing previously. Troubleshooting in progress.
- **Conflicts:** 2 trivial (release.yml, install.sh — both auto-resolved). No remaining conflicts.
- **Gate:** wait for CI green → squash-merge to main.

---

## 2. Rework Checkpoint Status

| Ckpt | Theme | Status | What Remains |
|------|-------|--------|--------------|
| **R1** | Secure + installable | ✅ ON MAIN | Merged. |
| **R2/R2.1** | Model layer | ✅ ON MAIN | SQLite registry, file-set pulling, store GC, modality taxonomy. Done. |
| **R3** | Slot runtime | ✅ ON MAIN | Slot-ID, PortAuthority, Quadlet, deep interface, GTT. Done. |
| **R4** | Brain + Hermes | ✅ ON MAIN | Brain module, convergent installer, HP-core/memory/provider/executor, steward-auth. Done. |
| **R5 Phase 0-3** | Surface sync + structure | ✅ ON MAIN | typed-bodies (#1322), MCP-autogen (#1323), FLAGS-own §2 flag-fold + §7 chat_template (#1325), docs collapse, anti-scar rules. Done. |
| **R5 Phase 4** | Deploy windows + launch | ⏸ OPEN | See §3 below. |

---

## 3. REMAINING WORK — R5 Phase 4 (Migration Windows + Launch)

### 3.1 Merge Branch Landing (IMMEDIATE)

**PR #1330** — land `merge/rework-descar-into-main` onto `main`:
- Seeded-profile 1.0 rework (16 profiles, 10 static seeds, family_defaults cleared, brain tool_model default)
- Registry prune (#1305)
- P3-runtime-db inc4 (bilingual slot layer + migrate CLI)
- **Blocked on:** CI green. python tests had 3 known failures (fixed), UI has lint issue (troubleshooting).

### 3.2 Migration Windows (orchestrator-run LIVE steps, not agents)

| Lane | What | Notes |
|------|------|-------|
| **P2-memory** | Honcho→Hindsight migrate per workspace | Use deterministic Honcho fixtures on fresh halo143. Never mutate lxc105. `hal0 memory migrate --from honcho --to hindsight`. Verify `[honcho]` config tolerance. Ordered deletion after. |
| **P2-config** | capabilities.toml → derived view over slots/*.toml | 3-release window + create-on-select. FLAGS-own migration same window. |
| **FLAGS-own migrator** | Run `migrate_slot_flags_fold` | Dry-run first on halo143. Divergent-share refusal path. Idempotent. `apply` needs `deploy_window=True`. |
| **SLOT-B M5 live flip** | Live unit `@name→@id` + podman rename | Bilingual runtime must be deployed FIRST (P3-runtime-db inc0-4). Then `hal0 slot migrate-id-keying --yes --stop-services`. Log: `docs/rework/deploy-validation/2026-07-19-idflip-143-attempt1.log`. |
| **P2-updater-b** | Verify + trim (pipeline already implemented, ~1,918 lines) | Delete extra mechanisms. Resolve CLI `--channel nightly`/API mismatch. |
| **P3-runtime-db** | state.json/pull-jobs/events → SQLite | One table at a time. Coordinate with M5. |
| **Live install-validation** | Both boxes (halo150/143) | INSTALL-target reboot-autostart, `doctor perms --fix` + default-store pull, upgrade-in-place, uninstall/reinstall no-ghost-slots. |
| **descar→main promotion** | Version-string bump to v1.0.0 | pyproject/UI version bump. Sunset stamps already retargeted to v1.0.0. |
| **ComfyUI repin** | `docker.io/kyuz0` → `ghcr.io/hal0ai/hal0-comfyui@fd8c8930` | Cross-repo. |
| **cpu-runner lineage** | Wire `hal0-toolbox-cpu:v1` or ratify vulkan-reuse | Decision owed to user. |

---

## 4. DEFERRED — Post-v1.0.0

### 4.1 FLAGS-own §7 Tail: `slot.device` → Model Runner Axis

- **Deferred by user** (2026-07-20). Past v1.0.0.
- `chat_template` already shipped (#1325).
- `slot.device` field: cross-subsystem primitive, ~10 reader files, needs live GPU validation.
- `profile.image` delete: blocked on updater stale-pin escape hatch retirement.
- Board row: #23 / 201. Spec: `docs/rework/hal0-specs/spec-flags-ownership.md` §7.

### 4.2 HP Deferred Items

| Lane | Board Row | Status |
|------|-----------|--------|
| HP-voice | 101 | ⏸ post-core |
| HP-automation | 104 | ⏸ post-core |
| HP-context | 105 | ⏸ post-core |
| HP-legacy-suite | 108 | ⏸ post-core |
| HP-realtime inc-2 | R4 tail | Silero VAD, streaming ASR, vibevoice server |

### 4.3 Security Follow-ups

| Item | Board Row |
|------|-----------|
| `/api/slots/{name}/logs/stream` redact | 154 — third log leak path, still unredacted |

### 4.4 Router Decomposition Gap

- `models.py` at 891 LOC vs DoD ≤550
- `slots.py` at 1396 LOC vs DoD ≤800
- Board row 177.

### 4.5 Other Deferred

| Item | Reason |
|------|--------|
| §20 bench | Needs GPU box. hal0 llama.cpp forks reject newer GGUFs. |
| ComfyUI host-net veto | User window still open (R4 tail). |
| UI D4-D6 | Security/migration-UX/diagnostics panel. |
| P3-routers DoD gap | models.py/slots.py line counts. |

---

## 5. FLM Rework — Complete Plan

**Full document:** `docs/rework/flm-1.0-rework-plan.md`

**Architecture rule:** FLM is NOT an inference slot. It's a single NPU process running Chat+STT+Embed as a trio. Do not try to make it behave like a llama-server slot. The architectural differences are intentional and documented in the plan's §7.

### Phase 1 — Data/Config Alignment (~30 min)

| Task | Files |
|------|-------|
| Strip `device_class = "npu"` from `[profile.flm]` | `seed_profiles.toml`, `test_seed_profiles.py` |
| Populate HW grid fields in FLM seed: `n_gpu_layers = 0`, `threads = 0`, `binary = ""` | `flm.toml`, `test_slot_schema.py` |
| Sunset-stamp env-var fallback in `image_ref()` | `flm.py` |

### Phase 2 — Models Page: FLM Tab (~2 hrs)

| Task | Files |
|------|-------|
| Add "NPU / FLM" tab (4th tab) | `ui/src/dash/models.jsx` |
| Fetch from `GET /api/slots/flm/models` (already exists) | No API change |
| Download icon for not-installed, checkmark for installed (ALL tabs) | `models.jsx` |
| 3-dot menu per row: quick settings, assign to slot, delete | `models.jsx` |

### Phase 3 — NPU Slot Edit (~1.5 hrs)

| Task | Files |
|------|-------|
| Per-role model pickers: Chat + STT + Embed each get a `<select>` | `slot-modals.jsx` |
| Schema: `NpuConfig.chat_model`, `asr_model`, `embed_model` | `schema.py` |
| Container spec: pass per-role model flags to `flm serve` | `flm.py` |

### Phase 4 — Docs & Tests (~30 min)

| Task | Files |
|------|-------|
| Sunset stamps: `HAL0_SUNSET: v1.1.0` on env-var + alias | `flm.py` |
| Document trio dispatch in ARCHITECTURE.md | `ARCHITECTURE.md` |
| Tests: profile, schema, provider, e2e | various |

---

## 6. ONNX NPU Support Plan (Post-v1.0.0 Only)

**Full document:** `docs/rework/onnx-npu-support-plan.md`

Three NPU paths planned:

| Path | Runtime | Use Case | v1.0? |
|------|---------|----------|-------|
| **FLM** | `flm serve` | Chat+STT+Embed trio | ✅ v1.0 |
| **Raw ONNX** | `onnxruntime.InferenceSession` + Vitis AI EP | Embed, STT, image, any non-LLM ONNX model | ❌ Post-v1.0 |
| **OGA** | `onnxruntime-genai` (Model, Generator, Tokenizer) | LLM chat only (Llama, Qwen, Gemma, Phi, Mistral) | ❌ Post-v1.0 |

**Pre-requisite (Phase 0):** Verify Ryzen AI SW 1.7.0 runs in podman container on Strix Halo NPU. ONNX model loads via Vitis AI EP. Benchmark vs FLM. If Phase 0 fails → defer entire ONNX track.

**Provider strategy:** Two providers (not three — Raw ONNX + OGA). Both standard inference slot model (one model = one slot, not FLM's trio). NPU exclusivity across all three providers.

---

## 7. Memory Sync Batch

**File:** `docs/rework/memory-sync-batch.md`

24 knowledge documents from thinMint's Claude Code memory directory, formatted for import into the **Hindsight** dynamic bank:

- **Bank:** `pi-coder::hal0` (agent=pi-coder, project=hal0, endpoint `10.0.1.142:9177`)
- **Dataset:** `"default"` (the shared/common bank name)
- **Status:** Hindsight on CT105 was unreachable at sync time. Batch file ready for import when connectivity restored.
- **Contents:** hal0-api-deploy-layout, hal0-rework-deploy-halo-lxc, hal0-rework-working-setup, hal0-brain-toolcall-leak, hal0-honcho-local, rocmfp4-quant-procedure, ai-models-access-model, hal0-backup-fuse-hangs, pbs-datastore-truenas-tank, pve-gtt-hidden-memory, hal0-runner-images-provenance, hal0-box-uid-mismatch, hal0-langfuse-podman, media-qbit-nfs-rootsquash, thinmint-remote-desktop-krdp, work-scope-hal0-only, hindsight-hermes-claude-integration, minimax-config-single-point-of-failure, minimax-api-rate-limit, minimax-swarm-write-sandbox, openwhispr-hal0-config, hal0ai-hf-publishing, hal0-memory-default-bank

---

## 8. `.gitignore` Update

**Merged to main** (`f07a1cb6`): `.pi/` and `.pi-subagents/` directories are now gitignored. These pi agent artefacts will no longer dirty the working tree on branch switches.

---

## 9. Ways of Working (Unchanged)

From `hal0-rework-ways-of-working.md`:

- **One branch:** `rework/descar` → `main`. No parallel branches.
- **Board single-writer:** one orchestrator owns `REWORK_BOARD.md`.
- **Agent tiers:** Sonnet for build, Opus for hard code, Fable for review, Haiku for mechanical sweep.
- **Capped verify gate:** `ruff check` + `format --check` + import smoke + `make check-sunset` + targeted pytest. NEVER full pytest locally (podman/systemd hang).
- **Docs are suspect:** Verify against code; never cite ARCHITECTURE/CONTEXT as truth.
- **Deploy-affecting = both boxes** (150 privileged / 143 unprivileged).
- **Every code touch = capped gate.** Docs-only = dangling-link grep.
- **Verification lesson:** On ANY signature change, `grep -rn <name> tests/` and run every caller test.

---

## 10. Decisions Still Owed to User

Batch these at the next phase boundary:

| Decision | Context |
|----------|---------|
| ComfyUI host-net loopback veto | hostnet-render made ComfyUI web UI loopback-only (was LAN :8188). User window still open. |
| Updater nightly channel | Drop from API or add to CLI — they disagree today. |
| cpu-runner lineage | Wire `hal0-toolbox-cpu:v1` + manifest_key, or ratify vulkan-reuse with a note. |
| HP-voice/automation/context promote-or-defer | Currently ⏸ below R4 exit bar. |
| God-module LOC tracking | Per-checkpoint burn-down — yes/no. |
| Release version number | Sunset stamps retargeted to v1.0.0. Confirm or re-target. |

---

## 11. Quick-Start for Next Session

```bash
cd /home/mint/hal0
git fetch origin
git checkout merge/rework-descar-into-main
# Check CI: gh pr checks 1330
# If green: gh pr merge 1330 --squash
# Then read: docs/rework/flm-1.0-rework-plan.md
# Start FLM Phase 1 (data/config alignment — ~30 min)
```
