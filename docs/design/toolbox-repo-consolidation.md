# Toolbox Repo Consolidation

Status: proposed · Companion to `container-image-overhaul.md` (this is its "Gap 0")

## Goal

Make **`github.com/Hal0ai/amd-strix-halo-toolboxes`** the single repo where every
hal0 runtime container image is generated, versioned, and published. Today image
builds are scattered across three places; consolidate them so `hal0`'s
`manifest.json` points at one package namespace and `hal0 update` rolls forward
from one source of truth.

## Current state (verified 2026-07-12)

**Three build origins, one target registry (`ghcr.io/hal0ai`):**

| Image | Built where today | Notes |
|---|---|---|
| `hal0-rocmfpx` (the wanted default) | `Hal0ai/Hal0_ROCmFPX` (llama.cpp fork) via `.devops/strix-rocmfp4.Dockerfile` + `docker.yml`; also **hand-built/pushed** (`c077206`, local origin) | gfx1151 HIP + FPX; heavy from-source compile |
| `amd-strix-halo-toolboxes:{vulkan-radv-server, rocm-7.2.4-rocmfp4-server, …}` | `Hal0ai/amd-strix-halo-toolboxes` (fork of `kyuz0/…`) `toolboxes/Dockerfile.<flavor>` | mature CI already here |
| `hal0-toolbox-{flm,kokoro,qwen3tts,moonshine}` | `Hal0ai/hal0` `packaging/toolbox/*.Dockerfile` (only qwen3tts has durable CI) | NPU/TTS/STT backends |
| `comfyui`, `llama.cpp:server-cuda` | third-party (`docker.io/kyuz0`, `ghcr.io/ggml-org`) | pinned, stay external |

**`amd-strix-halo-toolboxes` already has the right bones:**
- `toolboxes/Dockerfile.<flavor>` — per-image Dockerfiles (rocm 6.4.4 / 7.2.2 /
  7.2.4 / 7.2.4-rocmfp4[-server] / turboquant / nightlies; vulkan amdvlk / radv /
  radv-server).
- Workflows: `build_and_publish.yml` (dispatch + matrix), `ghcr-publish.yml`,
  `poll-llama-cpp.yaml` (auto-rebuild on new llama.cpp), `prune-old-toolboxes.yml`,
  `upstream-sync.yml` (sync from kyuz0 upstream).
- Package model: **one package, a tag per flavor** — exactly the unified shape.

**Gaps preventing it from being *the* repo:**
1. No `rocmfpx` flavor here (lives in the `Hal0_ROCmFPX` fork).
2. No flm/kokoro/qwen3tts/moonshine here (live in `hal0/packaging/toolbox/`).
3. `build_and_publish.yml` still targets the **upstream `docker.io/kyuz0`** repo
   (inherited from the fork) and its default matrix omits the `-server` /
   `rocmfp4` flavors hal0 actually seeds.
4. Stale — last built 2026-06-25.

## Target architecture

**One package:** `ghcr.io/hal0ai/amd-strix-halo-toolboxes`, **tag per flavor**:

```
:rocmfpx-<llamacpp_ver>-<fpx_ref>   # the unified GPU runner (chat+embed+rerank+FPX)
:rocmfpx-stable                     # rolling alias → the current rocmfpx-* the platform defaults to
:vulkan-radv-server                 # portability Vulkan lane (non-Strix / fallback)
:rocm-7.2.4-rocmfp4-server          # portability ROCm lane
:flm-<ver> :kokoro-<ver> :qwen3tts-<ver> :moonshine-<ver>   # NPU/TTS/STT backends
```

`hal0`'s `manifest.json.toolbox_images` points every entry at this package + a
digest, resolved by `scripts/update-toolbox-digests.sh`. Retire the scattered
`hal0-rocmfpx` and `hal0-toolbox-*` package names (keep them as pushed aliases
for one release for back-compat, then drop).

## Work plan

### ROCmFPX upstream topology (where the FPX llama.cpp comes from)

The rocmfpx runner's llama.cpp is maintained in `Hal0ai/Hal0_ROCmFPX`, a **fork of
`charlie12345/ROCmFPX`** (the root ROCmFPX-family repo, actively developed). Keep
the FPX upstreams as git remotes *on that fork* (the integration workspace), not on
the toolbox repo:

| Remote | Role | Track |
|---|---|---|
| `charlie12345/ROCmFPX` | **Primary** (fork parent) | merge `main`; watch `experimental-rocmfpx-branch`, `agent/promote-experimental-to-main-*` |
| `ciru-ai/ROCmFPX` | **Secondary** — FP3 Vulkan matvec/dequant speed path | cherry-pick speed-path commits |

**Fork decision is the OPPOSITE of the toolbox repo:** `Hal0_ROCmFPX` should STAY a
fork of `charlie12345` — hal0 is a downstream *integrator* of active upstream FPX
llama.cpp work, not its maintainer. (Contrast: `amd-strix-halo-toolboxes` should
detach from kyuz0 because hal0 *is* becoming that layer's maintainer.) The unified
toolbox repo consumes `Hal0_ROCmFPX` at a pinned ref; charlie/ciru-ai syncing
happens one layer down in the fork.

### T0-A — Publish rocmfpx from the fork's existing (inherited) CI  ✅ decided

**Don't rebuild the wheel or move the heavy compile.** `charlie12345/ROCmFPX`
already ships a working `docker.yml` that builds `.devops/strix-rocmfp4.Dockerfile`
(full/light/**server** targets, gfx1151) and pushes to
`ghcr.io/<owner>/<repo>:strix-rocmfp4`, plus a `release.yml` that tags the source.
`Hal0ai/Hal0_ROCmFPX` **inherited that same `docker.yml`** — it's just not
enabled/repointed (why `c077206` was hand-pushed to a `hal0-rocmfpx` package).

Plan:
1. In `Hal0_ROCmFPX`, **enable + repoint its `docker.yml`** to push the `server`
   target to the unified package namespace:
   `ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocmfpx-<llamacpp_ver>-<ref>`
   (a workflow can push to any package the org grants it — the image repo is just
   a variable; overrides the inherited `ghcr.io/${owner}/${repo}` default, which
   would otherwise be the underscore package `hal0_rocmfpx` ≠ the current
   dash `hal0-rocmfpx`).
2. Move the rolling `:rocmfpx-stable` alias on release.
3. The **compile stays in `Hal0_ROCmFPX`** (it's the llama.cpp fork of charlie —
   the natural home for a from-source gfx1151 HIP build; charlie's flow is
   `scripts/build-strix-rocmfp4-mtp.sh`). The unified toolbox repo owns the
   *namespace/manifest*, not this compile. This keeps "one package" without
   duplicating charlie's build into the toolbox repo.

Note the build is heavy (from-source HIP + FPX); if `ubuntu-24.04` GitHub runners
are too slow/small, use the `build-self-hosted.yml` path already present in the
fork.

### T0-B — Fold in the NPU/TTS/STT toolboxes
Move `hal0/packaging/toolbox/{flm,kokoro,qwen3tts,moonshine,cpu}.Dockerfile`
(+ contexts) into `amd-strix-halo-toolboxes/toolboxes/` and add them to the build
matrix. Delete `hal0/.github/workflows/toolbox.yml` (qwen3tts) once its build
lands here. Keep third-party comfyui / cuda external.

### T0-C — Repoint + harden the publish CI
- `build_and_publish.yml`: push to `ghcr.io/hal0ai/amd-strix-halo-toolboxes`
  (not `docker.io/kyuz0`); default matrix = the flavors hal0 seeds (incl.
  `-server`, `rocmfp4-server`, `rocmfpx`, and the NPU/TTS set).
- Tag scheme: immutable `:<flavor>-<ver>` on every build + move the rolling
  `:<flavor>-stable` alias (esp. `:rocmfpx-stable`) on release.
- Keep `poll-llama-cpp.yaml` (auto-rebuild), `prune-old-toolboxes.yml`,
  `upstream-sync.yml`. Add a `workflow_dispatch` + `repository_dispatch` hook so a
  hal0 release can trigger a coordinated rebuild.

### T0-D — Wire `hal0` to the unified package
- `manifest.json.toolbox_images`: every entry → `amd-strix-halo-toolboxes:<flavor>`
  + digest.
- `DEFAULT_ROCMFPX_IMAGE` → `ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocmfpx-stable`
  (feeds the universal `resolve_default_image` already built in
  `feat/container-image-overhaul` / PR #1297).
- `scripts/update-toolbox-digests.sh`: already iterates `toolbox_images`; no change
  beyond the retargeted tags.
- `STALE_ROCMFPX_IMAGE_REFS`: add the old `hal0-rocmfpx:*` and per-flavor sha tags
  so `hal0 update` retags existing slots onto the unified package.

## Sequencing vs the image overhaul

The `feat/container-image-overhaul` work (universal `resolve_default_image` ✓,
seed-profile repoint ✓, retag ✓ — PR #1297) is the **consumer** side and lands
first pointing at the existing `hal0-rocmfpx:c077206`. T0 (this doc) is the
**producer** side —
publishing `:rocmfpx-stable` from the unified repo — and lets us flip
`DEFAULT_ROCMFPX_IMAGE` from a hand-built sha to a CI-published rolling tag. They
meet at `manifest.json` + `DEFAULT_ROCMFPX_IMAGE`.

## Detach from the kyuz0 fork — recommended

**Detach `Hal0ai/amd-strix-halo-toolboxes` from being a GitHub *fork* of
`kyuz0/amd-strix-halo-toolboxes`, but keep kyuz0 as a plain `upstream` git
remote.** Rationale:

- Once this repo hosts the `rocmfpx` gfx1151 runner + the NPU/TTS/STT toolboxes,
  it is hal0's release-critical image factory, not a thin overlay on kyuz0.
- Fork constraints hurt a *publishing* repo: Actions restricted by default,
  read-only `GITHUB_TOKEN` on fork PRs, secret/`packages: write` friction (likely
  why `build_and_publish.yml` still targets `docker.io/kyuz0` unrepointed).
  Standalone gets clean CODEOWNERS, branch protection, release tags, own issues.
- Keep the leverage without the fork: `upstream-sync.yml` merges/cherry-picks
  from kyuz0 as a tracked remote. Keep `LICENSE` + `FORK_NOTES.md` attribution.

Do NOT detach if the repo stays a thin kyuz0 overlay and rocmfpx/NPU live
elsewhere — but that contradicts "one unified repo".

## Open decisions
1. ~~rocmfpx build home~~ — **RESOLVED (T0-A):** compile stays in `Hal0_ROCmFPX`
   (fork of charlie); enable + repoint its inherited `docker.yml` to publish the
   `server` target into the unified package. Runner size TBD (GitHub vs
   self-hosted).
2. **NPU/TTS scope** — consolidate flm/kokoro/qwen3tts/moonshine here now, or leave
   in `hal0` for a later pass (they're not llama.cpp GPU toolboxes).
3. **Package name** — one unified package `amd-strix-halo-toolboxes` with flavor
   tags (recommended; note the current `hal0-rocmfpx` dash vs inherited-CI
   `hal0_rocmfpx` underscore mismatch to reconcile) vs. keeping distinct
   `hal0-toolbox-*` / `hal0-rocmfpx` packages built from one repo.
