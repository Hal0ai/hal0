# vulkanfpx → rocmfpx collapse — design

**Date:** 2026-08-30
**Status:** approved in chat (operator), spec for the implementation plan
**Branch:** lands on `feat/specialty-distributions` as one PR
**Relates to:** cascade PR #2127 (merged), runner-images-v3 plan
(`docs/superpowers/plans/2026-08-30-runner-images-v3.md` — see §7)

## Problem

There is no vulkanFPX quant or binary. The runner is ROCmFPX; the shipped
image happens to carry a working Vulkan backend. `RUNNER_IMAGES` keeps two
keys — `rocmfpx` and `vulkanfpx` — that resolve to the same image
(`DEFAULT_ROCMFPX_IMAGE`) and differ only in which backend they *select*.
`runner_for_backend`'s own docstring concedes the twins exist "so a future
runner split has somewhere to land, not because they differ today."

Since PR #2127 the slot drawer's Backend cascade models one binary serving
two backends natively (`rocmfpx · rocm` / `rocmfpx · vulkan` pairs, the
slot's `device` picks the lane), so the key's backend-selector job is gone.
What remains is a misnomer that leaks into TOML, CLI output, env vars, and
logs — and a duplicate key the runner-images-v3 plan has to patch around
(`CANONICAL_FAMILY`).

Decisions taken in brainstorming (2026-08-30):

1. **Collapse the twins** — do not merely rename or restyle the label.
2. **Survivor key is `rocmfpx`** — the name is already honest; the
   dual-backend fact lives in `supported_backends` and the pair labels.
3. **Collapse lands first**, before the runner-images-v3 plan executes, so
   v3 drops its `CANONICAL_FAMILY` alias instead of building it.

Pre-GA is the last cheap moment for persisted-key surgery.

## Design

### 1. Registry collapse (`src/hal0/runners/__init__.py`)

- Delete the `vulkanfpx` entry from `RUNNER_IMAGES`.
- `rocmfpx` is unchanged: `backend="rocm"` (its default lane),
  `supported_backends=("rocm", "vulkan")`, image `DEFAULT_ROCMFPX_IMAGE`.
- `runner_for_backend("vulkan")` returns the `rocmfpx` runner. Its
  docstring loses the "future split parking spot" rationale — a real future
  split reintroduces a key with a real name.
- `FPX_RUNNER_KEYS` shrinks to `frozenset({"rocmfpx"})`. Its one call site
  (`providers/container.py` quant/runner guard, #1790) sees canonical keys
  only because lookups normalize first (§2).
- New module-level alias table + resolver:

  ```python
  #: Legacy runner keys → the canonical key that replaced them. Permanent —
  #: persisted slot TOML, model preferred_runner, and operator muscle memory
  #: outlive any release; the cost is one dict entry.
  RUNNER_ALIASES: dict[str, str] = {"vulkanfpx": "rocmfpx"}

  def canonical_runner_key(key: str) -> str: ...
  ```

- `get_runner()` resolves through `canonical_runner_key` before the dict
  lookup, so every consumer that resolves a key string (model registry
  `preferred_runner`, profiles' lazy family lookup, launch path) heals old
  values with no per-consumer code.
- `runner_matches(runner, backend=...)` must stop vetoing on the single
  `runner.backend` field (`runners/__init__.py:343`): when
  `runner.supported_backends` is non-empty it is the membership test;
  the bare `backend` field remains only a display/default-lane fact.
  Without this, `rocmfpx` fails a `backend="vulkan"` match that the old
  `vulkanfpx` row used to satisfy.

### 2. Persisted-value normalization — alias at choke points, no rewrite

Never-rewrite doctrine holds: disk TOML is not migrated in place.

- **Config load:** slot `binary` values and `[slots].default_images` keys
  pass through `canonical_runner_key` at parse. One `log.warning` per
  aliased value (`runner_images.alias_key key=vulkanfpx canonical=rocmfpx`).
  The next operator Save writes the canonical key naturally.
- **Env override:** `resolve_runner_image` for `rocmfpx` checks
  `HAL0_TOOLBOX_IMAGE_ROCMFPX` first, then legacy
  `HAL0_TOOLBOX_IMAGE_VULKANFPX` as a fallback tier, warning when the
  legacy name is the one that hit.
- The alias is **permanent**, not a deprecation shim with a removal date.

### 3. UI / API fallout — near zero by construction

- system-info's `backends` payload loses the `vulkanfpx` row. The #2127
  cascade already enumerates `(binary · backend)` pairs from
  `supported_backends`, so the drawer shows `rocmfpx · rocm` and
  `rocmfpx · vulkan` with no UI change.
- A stale persisted `binary="vulkanfpx"` never reaches the UI — it is
  normalized at config load — so the drawer's out-of-vocab option remains
  reserved for genuinely unknown keys.
- The `slotRunnerKey` fallback in `ui/src/api/hooks/useRuntimes.ts` matches
  by backend when `binary` is empty; with one row per lineage it now
  resolves unambiguously for vulkan slots.
- Display label ("ROCmFPX Combined") is the v3 plan's `display_name`
  field — explicitly out of scope here.

### 4. Tests and docs

- Update the ~10 python test files and 3 ui test files that spell
  `vulkanfpx` (`tests/providers/test_image_resolution.py`,
  `tests/runners/…`, `ui/src/dash/__tests__/hw-cascade.test.ts`,
  `ui/tests/e2e/specs/runtimes-page-v3.spec.ts`,
  `ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts`, …). Most edits are
  mechanical fixture renames; e2e fixtures that modelled the dual-binary
  image collapse to one binary with two supported backends.
- New tests (failing first, per TDD):
  - `canonical_runner_key` / `get_runner("vulkanfpx")` returns the
    `rocmfpx` runner.
  - Config load normalizes slot `binary` and `[slots].default_images`
    aliased keys, and warns.
  - `runner_for_backend("vulkan")` → `rocmfpx`.
  - `runner_matches(rocmfpx, backend="vulkan")` is true (supported_backends
    membership), false for `cuda`.
  - Legacy env var `HAL0_TOOLBOX_IMAGE_VULKANFPX` still resolves, with
    warning.
  - `test_registry.py` invariants keep passing with the shrunk registry.
- Docstring sweep: `runners/__init__.py` module doc, `schema.py:2205`
  family-key list, `install/brain_model.py` and `registry/curated.py`
  comments that name the twins.
- CHANGELOG entry states the contract: key removed, alias permanent, env
  fallback honored, no TOML rewrite.

### 5. Explicit non-goals

- No GHCR image renames (2026-08-01 ruling: breaks pulls/digest pins/CI).
- No display-name work (v3 plan owns `display_name`).
- No renaming of the `rocmfpx` survivor, quant filenames
  (`*-rocmfpx.gguf`), profile names (`rocmfpx-rocm`, `rocmfpx-moe`), or
  `quant_from_rocmfpx_filename` internals — all already honest.
- No promptforge changes.

### 6. Verification

- Full python suite + ui vitest + the two slot-drawer e2e specs green.
- Manual: a slot TOML hand-edited to `binary = "vulkanfpx"` launches on the
  vulkan lane, warns once at load, and the drawer shows `rocmfpx · vulkan`
  selected with no out-of-vocab option.

### 7. Coordination with runner-images-v3

Edit `docs/superpowers/plans/2026-08-30-runner-images-v3.md` and its design
spec: the `CANONICAL_FAMILY` mechanism (plan Step 3, spec §3 "family keying
fix") is superseded — replace with a note pointing at this spec. One
canonical key per image lineage makes the family fold a no-op; the config
validator warning for unknown keys stays useful and remains in v3. The
other session must see this edit before executing that task.
