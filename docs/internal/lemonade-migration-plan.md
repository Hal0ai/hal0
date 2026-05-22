# hal0 → Lemonade Server Migration Plan

**Status:** Post-spike, post-impl-grilling 2026-05-22. Architectural + impl decisions locked. Web-UI rework pending research handoff (`/tmp/hal0-lemonade-research-handoff.md`). v0.2 PR sequence ready to start.

**Vehicle:** hal0 v0.2 (Lemonade + Agents per parallel session). UI rework deferred to v0.2.1.

**Source artifacts:**
- Spike findings: `lemonade-spike-findings-2026-05-22.md`
- Spike runbook: `lemonade-spike-runbook.md`
- Research handoff (pending): `/tmp/hal0-lemonade-research-handoff.md`
- Memories: `hal0_lemonade_migration_v0.2`, `hal0_lemonade_gotchas`

---

## Drivers (post-spike reassessment)

1. ✓ Eliminate per-modality toolbox maintenance burden — still wins
2. ⚠ NPU + iGPU utilization — NPU path broken at spike time (FLM silent fail; libxrt absent), needs install.sh prereq handling
3. ✓ Stop chasing llama.cpp churn — still wins (AMD pins llamacpp-rocm builds per channel)
4. ✓ Strategic AMD legitimacy — still wins
5. ✗ Performance — **evaporated**. Spike: hermes-14b at parity, qwen3.5-0.8b -18%, qwen3.6-35b MoE -13% on ROCm. Vulkan-in-Lemonade is 3x slower than hal0-Vulkan baseline.

Migration still net-positive on the surviving drivers, but the perf narrative needs honest framing in release notes.

---

## Strategic decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Replacement scope | Total — `lemond` is the only backend |
| 2 | Spike host (executed) | LXC 105 alongside live hal0 |
| 3 | NPU strategy | `lemonade backends install flm` at install + install.sh handles libxrt-npu2 prereq |
| 4 | vLLM-ROCm | Out of scope this cycle — re-evaluate post-v0.2 |
| 5 | Release vehicle | v0.2 (combined with Agents per parallel session work) |
| 6 | UI rework | Punt to v0.2.1 pending web-ui.md research |
| 7 | Bundling + pin | manifest.json schema v2 adds `lemonade: { image, digest, version }` |
| 8 | Rollback | Downgrade-only via existing update mechanism; v0.2 deletes old Provider code cleanly |
| 9 | ComfyUI loss | Accepted — sdpp covers 90%; release-notes documented |

## Implementation decisions

| # | Decision | Choice |
|---|---|---|
| 10 | Slot abstraction | Preserve. Each hal0 slot = 1 Lemonade-loaded model. SlotManager retargets to Lemonade load/unload. Per-slot `hal0-slot@.service` retires. |
| 11 | Drive method | HTTP-first /v1/load with reverse-engineered schema (research lands it). `lemonade` CLI subprocess as bootstrap fallback. |
| 12 | Model registration | Generate hal0-customized `server_models.json` from `registry.toml` at install. Runtime user adds via /v1/pull `user.*` namespace. |
| 13 | Process supervision | Containerized lemond in systemd. `/etc/systemd/system/lemond.service` wraps `podman/docker run` with `--device` passthrough. Boot-enabled. |
| 14 | Container source | `ghcr.io/hal0ai/hal0-lemond:vX.Y.Z` — hal0-published wrapper. Base + lemond tarball + `unzip` + `libxrt-npu2` + pre-pulled backends. CI builds + cosign-signs. |
| 15 | `SlotConfig.backend` | Refactor → `SlotConfig.device` (enum `gpu-rocm \| gpu-vulkan \| cpu \| npu`). Default `gpu-rocm`. Schema_version bump + migration. |
| 16 | Metrics shim | `/v1/stats` polling (backend_url /metrics returned 501 in spike). hal0-side metrics aggregator polls `/v1/stats` + `/v1/health` per slot. |
| 17 | Idle-eviction driver | hal0-owned external. SlotManager polls `/v1/health.loaded[].last_use`, calls `POST /v1/unload` when stale per existing 300s policy. |
| 18 | Nuclear-evict-all mitigation | Pre-validate file existence + sha256 BEFORE `/v1/load`. Steers failures into file-not-found path (the one exempt from evict-all). |
| 19 | PR sequencing | Flag-gated incremental (`HAL0_BACKEND=lemonade`); main always shippable; atomic cutover PR. |

---

## ADRs (planned)

- **ADR-0006** Migrate inference to Lemonade Server (strategic): covers decisions 1-15. The big arc.
- **ADR-0007** Nuclear-evict-all mitigation (tactical): covers decision 18, the operational hazard observed live in spike.

ADRs 0007+ for multi-user Cognee, prior plan, are renumbered to 0008+ as needed.

---

## Retirements in v0.2

**Code:**
- `src/hal0/providers/llama_server.py`, `flm.py`, `moonshine.py`, `kokoro.py`, `comfyui.py`
- Hardcoded port + metric-name logic in `src/hal0/api/routes/slots.py` `_scrape_llama_metrics()`
- `hal0-slot@.service` systemd template

**Toolbox infrastructure:**
- `hal0-toolbox-{vulkan,rocm,flm,moonshine,kokoro,comfyui}` Dockerfiles
- `.github/workflows/toolbox.yml` publish pipelines

**Host state:**
- `/opt/hal0/flm-ubuntu` (if exists — spike found it absent)
- `/var/lib/hal0/flm-models` (replaced by Lemonade model store)

---

## Additions in v0.2

**New code:**
- `src/hal0/providers/lemonade.py` — single Provider class
- `src/hal0/lemonade/client.py` — HTTP client (with CLI bootstrap fallback)
- `src/hal0/lemonade/server_models_gen.py` — registry.toml → server_models.json converter
- `src/hal0/lemonade/metrics.py` — /v1/stats poller exposing Prometheus surface
- `src/hal0/lemonade/idle.py` — idle-unload driver
- Pre-validation logic in load path (sha256 + file-exists checks)

**New config:**
- `manifest.json` schema v2 with `lemonade: { image, digest, version }`
- `/var/lib/hal0/lemonade/config.json` template emitted by hal0
- `capabilities.toml` schema_version = 2 (preserve v1 backup for downgrade)
- `SlotConfig.device` field (replaces `backend`)

**install.sh changes:**
- `apt install -y unzip libxrt-npu2` (system deps Lemonade requires)
- Pull `ghcr.io/hal0ai/hal0-lemond:vX.Y.Z` image
- Write `/etc/systemd/system/lemond.service`
- `systemctl enable --now lemond`
- Generate + install `server_models.json` from registry
- Stop installing per-modality docker images

**New CI:**
- `hal0-lemond` image build workflow (replaces six toolbox workflows)
- Cosign signing per release

---

## PR sequence (preliminary; refined as research lands)

1. **ADR-0006 + ADR-0007 drafts** — written from this session's decisions; tightened post-research
2. **`LemonadeClient` skeleton** — HTTP client with CLI fallback, type stubs only
3. **manifest.json schema v2** — `lemonade: {...}` field, validation, `HAL0_BACKEND=lemonade` flag plumbing
4. **`hal0-lemond` image** — Dockerfile + GH workflow + cosign + publish to ghcr.io
5. **`lemond.service`** — install.sh writes systemd unit + container-run command + device passthrough
6. **`server_models_gen.py`** — registry.toml → server_models.json converter + install hook
7. **`SlotConfig.device`** — schema refactor + capabilities.toml schema_version=2 migration
8. **`LemonadeProvider`** — concrete provider implementing Provider ABC, drives `LemonadeClient`
9. **Pre-load validation** — sha256 + file-exists guards before /v1/load (ADR-0007 mitigation)
10. **`/v1/stats` metrics poller** — Prometheus surface from Lemonade stats
11. **Idle-unload driver** — SlotManager poll loop calling /v1/unload
12. **install.sh changes** — apt deps, image pull, service enable, server_models.json install
13. **Capability orchestrator** — switch `apply()` to drive new SlotManager flow
14. **`hal0 capabilities migrate-to-lemonade`** — schema migration command (also auto-run on hal0-api upgrade)
15. **Cutover PR** — flip default backend to Lemonade + delete old Provider code + retire `hal0-slot@.service` template
16. **Toolbox cleanup** — delete six Dockerfiles, six workflows, hal0-toolbox repos archived

Each PR shippable as v0.1.x patch until #15. UI work skipped; lives in v0.2.1.

---

## Open items deferred to research handoff

- `/v1/load` actual request schema (CLI works; direct curl rejected docs body)
- Lemonade WS protocol (used by v0.2.1 UI rework)
- Omni recipe pattern (collection.omni — may inform capability mapping)
- Whether AMD publishes an official docker image for lemonade (informs container source decision)
- Reserved-args extension hook (some args may be configurable via server_models.json, not yet known)
- Pre-load validation hooks (does Lemonade itself expose pre-load validation? Or must hal0 wrap?)

## Operational risks (verbatim from spike)

- **Nuclear evict-all on load failure** — confirmed live. ADR-0007 mitigation.
- **Serialized load queue** — Lemonade loads one model at a time, pending loads queue indefinitely. Hard timeout at hal0 level.
- **Weekly upstream breaking changes** — pin discipline non-negotiable.
- **`unzip` system dep** — install.sh prereq.
- **libxrt-npu2 absent** in current LXC; install.sh prereq for NPU path.
- **/metrics returns 501** on bundled llama-server — fallback to /v1/stats poll confirmed needed.
- **Default backend pick = Vulkan** (slow). Every load must specify `--llamacpp rocm` or equivalent config.
- **/v1/load API field shape** undocumented; CLI works.
