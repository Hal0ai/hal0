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

### T0-A — Add the rocmfpx flavor to the toolbox repo
Add `toolboxes/Dockerfile.rocmfpx` that builds the gfx1151 ROCmFPX runner. It
needs the ROCmFPX-patched llama.cpp (the FPX quant support lives in the
`Hal0_ROCmFPX` fork, not stock llama.cpp), so the Dockerfile checks out a
**pinned `Hal0_ROCmFPX` ref** and builds the `server` target
(`ENTRYPOINT llama-server`, `CMAKE_HIP_ARCHITECTURES=gfx1151`) — reuse
`.devops/strix-rocmfp4.Dockerfile` from that fork as the basis. The toolbox repo
stays the single *publish* point; the fork stays the llama.cpp *source of truth*.
(Alternative: keep the compile in `Hal0_ROCmFPX/docker.yml` but repoint its push
to `ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocmfpx-*`. Decide by where the heavy
gfx1151 build should run.)

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
  (feeds the HW-gated resolver already built in `feat/container-image-overhaul`).
- `scripts/update-toolbox-digests.sh`: already iterates `toolbox_images`; no change
  beyond the retargeted tags.
- `STALE_ROCMFPX_IMAGE_REFS`: add the old `hal0-rocmfpx:*` and per-flavor sha tags
  so `hal0 update` retags existing slots onto the unified package.

## Sequencing vs the image overhaul

The `feat/container-image-overhaul` work (HW-gated resolver ✓, repoint seed
profiles, retag) is the **consumer** side and can land first pointing at the
existing `hal0-rocmfpx:c077206`. T0 (this doc) is the **producer** side —
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
1. **rocmfpx build home** — build in the toolbox repo (checkout the `Hal0_ROCmFPX`
   fork at a pinned ref) vs. keep the compile in `Hal0_ROCmFPX` and only repoint
   its push target. Compile is heavy (gfx1151 HIP + FPX); a self-hosted/large
   runner may be needed either way — `Hal0_ROCmFPX` already has
   `build-self-hosted.yml`.
2. **NPU/TTS scope** — consolidate flm/kokoro/qwen3tts/moonshine here now, or leave
   in `hal0` for a later pass (they're not llama.cpp GPU toolboxes).
3. **Package rename** — one unified package with flavor tags (recommended) vs.
   keeping distinct `hal0-toolbox-*` packages built from one repo.
