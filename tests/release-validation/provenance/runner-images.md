# Runner image provenance

The second of the two recording sites #1970 asks for. The first is the image itself (OCI labels,
written by the recipe); this one is the thread a validator pulls when they have the repo but not
the image — or the image but no idea which tree built it.

**Why this file exists.** `ade07ba` was a hand-build with no tracked recipe. When #1888 turned out
to live somewhere in it, isolating the defect meant reconstructing the image's lineage from
scratch, and that reconstruction — not the fix — was the expensive part. `:0822` is a signed
default pin. It must never be in that position, so before a release cites a runner image, this
file must name the tree it came from.

Read this alongside `regressions.yaml` when a finding implicates generation quality, a GPU lane,
or tool-calling: those are the failure classes that have historically been image-resident rather
than hal0-resident, and the first question is always "which image, built from what".

## The default pin

| | |
|---|---|
| Tag | `ghcr.io/hal0ai/hal0-combined:0822` |
| Consumed by | `hal0.config.schema.DEFAULT_ROCMFPX_IMAGE` — **both** the `rocmfpx` and `vulkanfpx` runners resolve to this one tag |
| Recipe | `packaging/runner/rocmfpx/` |
| Manifest | `packaging/runner/rocmfpx/manifest.toml` |
| Build script | `packaging/runner/rocmfpx/build.sh` |
| Guarded by | `tests/packaging/test_rocmfpx_recipe.py` |

The manifest tag is asserted equal to `DEFAULT_ROCMFPX_IMAGE` by test, against a live import
rather than a duplicated literal. A manifest describing some *other* tag is worse than none: it
reads as provenance while pointing elsewhere.

## Exact refs

| Input | Pinned value |
|---|---|
| Base image | `ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server` |
| Base digest | `sha256:4f5418c1b1e39e5ad9bbadfd2e8381c6b8a70e9b03f667179a7233c6f681462a` |
| Source repo | `https://github.com/charlie12345/ROCmFPX.git` |
| Source ref | `0a59add89b8cba06fb6a0baf25a253a4e45faa78` (full sha; no submodules at this ref) |
| GPU arch | `gfx1151` (Strix Halo), combined HIP + Vulkan in one image |

The base is pinned **by digest**, and `build.sh` pulls `${BASE}@${DIGEST}` in both stages. The tag
is kept alongside for readability only. This matters more than it looks: a mutable base tag means
identical tracked inputs can produce different images on different days, silently, which is the
same shape as the untracked hand-build this whole recipe exists to retire.

`charlie12345/ROCmFPX`, **not** `ciru-ai/ROCmFPX`, is load-bearing. #1888 was bisected to the ciru
tree, whose Vulkan backend emits invalid tokens for every model. See #1948 for the matrix.

### Patch series

Applied in order to a pristine checkout of the pinned ref. Each carries a rationale in the
manifest; the manifest and the `patches/` directory are asserted to be in exact correspondence, so
neither an undeclared patch nor a declared-but-missing one can survive CI.

1. `0001-vocab-minicpm5-pretokenizer-mapping.patch` — the `minicpm5` name→enum mapping, absent
   upstream; without it every minicpm5-pretokenised GGUF (the whole shipped `hal0-brain-sft`
   family) fails to load.
2. `0002-chat-minicpm5-tool-call-parser.patch` — the MiniCPM5 XML tool-call parser. Without it the
   model still emits the call, nothing structures it, and raw markup lands in `content` as an
   HTTP 200 — a shape the runtime's learned-incompatible retry cannot recover from.
3. `0003-vulkan-shaders-serialize-glslc.patch` — caps concurrent `glslc` children to 1. Carried
   from kyuz0; belt-and-braces, explicitly **not** the cause of #1888.

## Verifying it

Runs anywhere with `git`, `python3` and network. **No container runtime required** — that is
deliberate, so this can run on an ordinary CI box, and `tests/packaging` exercises it there:

```sh
bash packaging/runner/rocmfpx/build.sh --check
```

It clones the pinned ref, asserts `HEAD` equals the manifest ref, applies all three patches, and
builds nothing.

**Know what `--check` does not prove.** It covers the source ref and the patch series. It does not
touch dnf resolution, the cmake configure, the four build targets, or either container build. A
green `--check` is not a green build, and it is not a claim that the output matches `:0822`.

## Honesty about `:0822` itself

**The published `:0822` was not built by this recipe.** Inspecting its config blob in the registry
shows it carries only the inherited Fedora base labels (`vendor=Fedora Project`, `version=43`) and
none of the recipe's provenance labels — no `org.opencontainers.image.revision`, no
`dev.hal0.runner.*`. It was pushed 2026-08-20T12:45:54Z, before the recipe existed.

So for `:0822` this recipe is **reconstructed provenance**, not a build record. Treat that as the
standing caveat when a ledger row cites it.

Two things make the reconstruction better evidence than a bare assertion, and both are checkable:

* The base digest above is not "whatever the tag resolves to today". The base manifest's 9 layer
  digests are an **exact ordered prefix** of `:0822`'s 13. The 4 extra layers are this recipe's
  own dnf / `COPY` / `chmod` steps. So `:0822` really was built on this exact base, and the tag
  has not been re-pushed since.
* `:0822`'s `Env` and `Entrypoint` match what the recipe's runtime stage writes —
  `LD_LIBRARY_PATH=/opt/rocmfpx/bin:/opt/rocm/lib`, `HSA_OVERRIDE_GFX_VERSION=11.5.1`,
  `GGML_HIP_ENABLE_UNIFIED_MEMORY=1`, `/opt/rocmfpx/hal0-runner-entrypoint.sh`.

The digest-pinned recipe governs the **next** build. From that build on, the labels answer the
question directly and this caveat can be dropped.

### Known gap

The base image's own derivation is untracked. It is a hal0-namespace republication of the Kyuz0
`amd-strix-halo-toolboxes` family, but no recipe for that derivation exists in this repo, and the
upstream commit that produced it is not known — see the note in `manifest.toml`, which states this
rather than guessing. The digest pin makes the gap survivable (the exact layer set stays
retrievable even if the tag moves) but does not close it. Also unpinned and consciously deferred:
the `dnf install` package versions in both stages.

## What this image was validated against

Before the pin moved. Full matrix and evidence in #1948.

* Boxes: ct150 (`/dev/kfd` present), ct151 (`/dev/kfd` **absent** — the #1888 repro shape).
* Probes: temp-0 `"The capital of France is"` → `"Paris"` on both lanes, FPX and non-FPX models;
  ≥256-token generation terminating with no `)`-repetition wedge; native MiniCPM5 `tool_calls` on
  the Vulkan lane of a kfd-less box; zero devices mapped → readable diagnostic, exit 78, no
  SIGSEGV (#1936).

The first of those is the kit's global **coherence canary** (`lanes/_shared.md`) and it exists
because of this image class: during the rc.6 run two lanes green-lit a box that never produced
language. No lane may trust generated text before it passes.

## Maintaining this file

Update it in the same PR that moves `DEFAULT_ROCMFPX_IMAGE` or changes anything in
`packaging/runner/rocmfpx/`. A stale provenance note is the failure mode it exists to prevent,
one level up.
