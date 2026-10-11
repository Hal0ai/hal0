# ADR-0027: A runner's tier is the one source of "default", and every runner is named for what it is

## Status

Accepted (2026-10-11). Amends the ADR-0006 consequence that grouped the
Runner Images list by "whether a family currently resolves to that image as
its default". Records the operator's answer to ADR-0025 open question 1 and
the naming rule ADR-0025 decision 1 depends on; the rest of ADR-0025 stays
proposed.

## Context

Two independent flags each answered "is this a default runtime", and they
disagreed:

- `Runner.is_default` in `src/hal0/runners/__init__.py` was `True` for
  `rocmfpx` only. `/api/system-info` copied it into `backends[].is_default`,
  and the slot drawer's runtime picker (`ui/src/dash/hw-cascade.js`
  `runnerOptions`) and the profile drawer (`ui/src/dash/profiles.jsx`) sorted
  by it.
- The Runner Images page grouped rows by the per-row `is_default` marker
  that `src/hal0/api/routes/runner_images.py` (`enrich_row`) derives from
  `_effective_defaults()`. That marker means "this repo is the image this
  family's unpinned slots launch". Every runner key is its own family, so
  every family has one, and on a live box niche runtimes (PromptForge,
  Qwen3-TTS, Strix) were listed under **Default runtime**.

The names were vague too. The AMD llama.cpp image is `hal0-combined`, its
catalogue row is `rocmfpx-combined`, and its dashboard title was
**Standard**, the name ADR-0025 reserves for the pristine-upstream runner.
The page also showed `images.json` `notes` as the description: maintainer
notes about build hosts, gate runs and reconstructed Dockerfiles, not copy
an operator can choose by.

## Decision

1. **`Runner.tier` is the single source of truth.** `tier` is
   `"default"`, `"optional"` or `"frozen"` (`hal0.runners.RunnerTier`).
   `Runner.is_default` is removed rather than kept as a derived alias,
   because nothing outside the two surfaces below read it.
   - `default`: in the set every box is expected to have, at most one per
     runtime family and device class.
   - `optional`: opt-in, picked on purpose for a specific model or device.
   - `frozen`: kept only to load models already on slots, never bumped.
2. **Both surfaces read it.** `/api/system-info` emits `backends[].tier`,
   and the pickers sort default, then optional, then frozen. The payload
   keeps `backends[].is_default` for API and MCP readers until v2.0.0
   (`HAL0-SUNSET`), computed as `tier == "default"`; the dashboard no
   longer reads it. `/api/runner-images` rows carry
   `runner`, `title`, `blurb` and `tier` from the registry entry whose
   bundled image lives in the row's repo, and `families` carry `title` and
   `tier`. The page groups rows into **Default runtimes**, **Optional
   runtimes**, **Frozen runtimes** and **Other images**. The last holds
   catalogued images no registry runner ships (legacy toolboxes, pin-only
   variants), which are not runtimes and have no tier.
3. **The launch image is a separate fact with separate wording.** The
   per-row `is_default` marker and the `families` strip keep meaning "the
   image this runtime's slots launch". The dashboard calls it the **launch
   image**: the row chip reads `launch image`, the strip is headed **Launch
   images**, the button reads **Set as <title> launch image**, and the
   release source chip reads `release`. None of these use the word
   "default", so they cannot be read as the tier. The API field name
   `is_default` on catalogue rows is unchanged.
4. **The default set** (operator decision, 2026-10-11): the AMD llama.cpp
   GPU runtime (today `rocmfpx`; `upstream` once ADR-0025 lands), `cpu`,
   `flm`, `kokoro`, `moonshine` and `comfyui`. **Optional**: `strix`,
   `qwen3tts`, `cuda` and `promptforge`. `promptforge` stays until
   ADR-0025 decision 7 removes it. When `upstream` lands as the default,
   `rocmfpx` moves to `frozen`; that change belongs to the ADR-0025 PR, not
   this one.
5. **Naming rule.** A runner's `title` says what it is and its `blurb` is
   one plain line saying what it runs on and when you would pick it,
   written for someone who has never heard of the fork behind it. Neither
   mentions how the image was assembled ("combined"). **Standard** is
   reserved for the `upstream` runner. Titles as of this record:
   **ROCmFPX**, **PromptForge (legacy)**, **Strix Halo Vulkan**, **NVIDIA
   CUDA**, **CPU**, **NPU (FastFlowLM)**, **Kokoro speech**, **Moonshine
   transcription**, **Qwen3 TTS**, **ComfyUI**. `images.json` `notes` stay
   on the row as collapsed maintainer notes. The catalogue id
   (`rocmfpx-combined`) and the image repo (`hal0-combined`) are owned by
   `Hal0ai/hal0-runner-images` and the published package, so this record
   does not rename them. The dashboard shows the title in their place.
6. **Agent lane** (ADR-0025 open question 1, answered by the operator): the
   agent slot moves to **Qwen3.8-27B on the `upstream` runner**, not the
   chadrock ladder on the frozen `rocmfpx`. That change ships with
   ADR-0025's runner work, after its hardware gate.

## Options considered

- **Keep `is_default` and fix only the page grouping.** Rejected: the
  page and the picker would still have two owners of one fact (anti-scar
  rule 1), and `is_default` cannot say "frozen".
- **Derive the tier from `runner_for_backend()`.** Rejected: CUDA is what
  that function returns on NVIDIA, but the operator ranks it optional, and
  FLM, Kokoro, Moonshine and ComfyUI never pass through it.
- **Rename the catalogue row's `is_default` API field to `launch_image`.**
  Deferred: the wording problem was on screen, the field already has
  consumers and tests, and the new `tier` field removes the ambiguity in
  the payload.

## Consequences

- `tests/runners/test_registry.py` pins the tier sets, at most one default
  per (family, device class), a non-empty one-line `title`/`blurb` for every
  runner, and the naming rule. ADR-0025's PR changes the tier sets there
  together with `rocmfpx` → `frozen`.
- ADR-0025 decision 2's "loses `is_default`" now reads "moves to the
  `frozen` tier", and its title for `rocmfpx` is **ROCmFPX (frozen)**.
- The catalogue row's `specialties` field no longer drives any UI grouping.
  It stays in the payload until a de-scar pass removes it.

## References

- ADR-0006 (every shipped image is a registry runner)
- ADR-0025 (upstream llama.cpp is the default runner; proposed)
- `src/hal0/runners/__init__.py` (`Runner.tier`, `RUNNER_IMAGES`)
- `src/hal0/api/routes/runner_images.py` (`enrich_row`, `_families_payload`)
- `ui/src/dash/runner-images.jsx` (`groupRows`, `launchImageLabel`)
- `docs/guides/manage-runner-images.mdx`
