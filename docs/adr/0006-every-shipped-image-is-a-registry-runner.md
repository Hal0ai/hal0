# ADR-0006: Every shipped runner image is a registry runner

## Status

Accepted.

## Context

#2118 shipped `hal0-combined-upstream` (pristine `ggml-org/llama.cpp`,
built to unblock the qwen4exp architecture) as a deliberately **pin-only**
image: catalogued in `RUNNER_IMAGES` / `GET /api/runner-images`, but wired
to a slot only through the per-slot `image_pin` escape hatch, never given
its own `backends` key in system-info's release catalogue
(`packaging/runner/upstream/manifest.toml`). The image catalogue knew about
it; the release catalogue that drives the slot drawer's Backend cascade did
not.

That split produced a real operator-facing failure, caught testing
Qwen3.8-Flash-Next on ct105 (#2170). The drawer's Backend dropdown is built
by `backendOptions()` (`ui/src/dash/hw-cascade.js:78-138`): when the pinned
ref matches a release-catalogue image (`pinInCatalog`) it enumerates just
that image's backends; when it doesn't — exactly the `combined-upstream`
case — `enumerable` falls back to `null`
(`ui/src/dash/hw-cascade.js:83-89`), and the loop's only filter
(`if (enumerable && r.image !== enumerable) continue;`,
`ui/src/dash/hw-cascade.js:94`) never fires. The dropdown then lists every
runner in the release catalogue, each option carrying *that runner's own*
`provenance` (`ui/src/dash/hw-cascade.js:100-104,120-132`) — a slot pinned
to the upstream llama.cpp build showed Backend options labelled with
ROCmFPX's and other images' build provenance, not its own. #2170 patched
the symptom (`cataloguePinOptions()`,
`ui/src/dash/hw-cascade.js:179-189`, surfaces downloaded-but-uncatalogued
images as pinnable; `slot_view`'s image/image_status resolution was
widened to cover pin-declared slots) without touching the cause: the image
catalogue and the release catalogue are two registries that can disagree
about which images exist, and a slot that lands in the gap between them
degrades to an unlabelled fallback union instead of a real backend list.

The strix runner shipped this cycle (`packaging/runner/strix/`,
`src/hal0/runners/__init__.py`) is the corrective template, and says so in
its own manifest: *"a first-class OPTIONAL runner — `RUNNER_IMAGES["strix"]`,
the promptforge shape, not the `../upstream` pin-only shape"*
(`packaging/runner/strix/manifest.toml`). It carries a real `Runner` entry
(`src/hal0/runners/__init__.py:191-221`) exactly like `promptforge`
(`:169-190`) — cataloged, backend-enumerable, reachable from the slot
drawer without the free-text pin hatch — and is named as the intended
successor to the `combined-upstream` pin for qwen4exp slots.

## Decision

1. **Every image hal0 ships gets a `RUNNER_IMAGES` entry.** Pin-only —
   catalogued for discovery but absent from the release catalogue's
   `backends` — is retired as a shipping shape. An image that isn't worth
   registering as a runner isn't worth shipping.
2. **Optional (non-default) runners follow the promptforge/strix
   template**: one `Runner` entry, one `runtime_family`, one declared
   `supported_backends` tuple, offered everywhere the release catalogue is
   enumerated (the Runtimes page, the slot drawer's Backend cascade) on
   the same footing as a default-family image — reachable by selecting it,
   not only by pinning a raw ref.
3. **Only the combined default image may declare two backends.**
   `rocmfpx`'s `supported_backends=("rocm", "vulkan")`
   (`src/hal0/runners/__init__.py:166`) stays a property of that one
   default-family image; every optional runner declares exactly one
   backend (promptforge: `("rocm",)`, strix: `("vulkan",)`) rather than
   inheriting the dual-backend shape by default.
4. **`image_pin` stays a debug-only hatch.** It remains available for a
   custom build, a rollback, or a hand-edited ref, but it never enumerates
   in `backendOptions()`'s catalogue union and is not how an operator
   normally reaches a shipped runtime — that's what a `RUNNER_IMAGES`
   entry is for.

## Consequences

- The #2170 "catalogued · downloaded" optgroup
  (`cataloguePinOptions()`, `ui/src/dash/hw-cascade.js:179-189`) exists to
  paper over a downloaded-but-uncatalogued class of image; once every
  shipped image carries a `RUNNER_IMAGES` entry, that class has no more
  members to surface and the optgroup is retired.
- Slot profiles gain the option of referencing a runtime by its registry
  key (a `Runner` entry) rather than a raw image ref, once every shipped
  image has one — closing the gap that let a slot land between the two
  catalogues in the first place.
- The Runner Images dashboard list drops its three-way split
  ("Default families" / "Specialized" / "Other catalogued") for a
  two-bucket "Default runtime" / "Optional runtimes" grouping
  (`ui/src/dash/runner-images.jsx`'s `groupRows()`): both buckets are now
  first-class registry entries, differentiated only by whether a family
  currently resolves to that image as its default.
- A future upstream/experimental image built the way `combined-upstream`
  was is not considered shipped merely because it's built and pushed — it
  needs a `Runner` entry before it's done, per this decision.
