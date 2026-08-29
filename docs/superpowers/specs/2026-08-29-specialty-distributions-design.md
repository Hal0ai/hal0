# Specialty model distributions — design

**Date:** 2026-08-29
**Status:** Approved (operator, 2026-08-29)
**Issues:** #1946 (PromptForge / CIRU ActiveFPX — first consumer), #1947 (CIRU vLLM — future consumer), #1948 (Vulkan restore — build-flag conflict context), #1888/#1891 (never-silent and validate-before-curate lessons), #1890 (quant metadata), #1790 (quant-vs-runner guard pattern).

## Problem

Some model distributions ship more than a GGUF: runtime companion files, required
environment variables, a specific argv envelope, and a runner built with a
particular configuration. The first shipped example is
`jcbtc/Qwen3.8-27B-CIRU-ActiveFPX-PromptForge`:

- Accelerated path needs CIRU ROCmFPX **v2.3** plus the model repo's
  `runtime/qwen38-v3-output-k8-runtime.patch`, composable_kernel pinned
  `fdf4bb7fcc98`, ROCm 7.15, and a **HIP-only build (`GGML_VULKAN=OFF`)** —
  mutually exclusive with the current default image
  (`ghcr.io/hal0ai/hal0-combined:0826`, built `GGML_VULKAN=ON`).
- Three `.pfs` sidecars (FFN 17.1 GB, GDN 4.0 GB, Output-K8 0.7 GB) exported via
  `PROMPTFORGE_SIDECAR` / `PROMPTFORGE_GDN_SIDECAR` /
  `PROMPTFORGE_MTP_OUTPUT_K8_PROXY` plus mode envs; BF16 mmproj; native depth-4
  MTP; 262,144 default context; exact argv profile
  (`-b 2048 -ub 2048 -fa on -ctk f16 -ctv f16 --no-cache-prompt`).
- The GGUF alone (15.44 GiB) runs on stock llama.cpp in a legitimate but
  degraded mode — today hal0 would run it degraded **silently**, the exact
  failure class of #1888.

Today hal0 has zero support: no companion pull, no `PROMPTFORGE_*` injection,
no capability tag on runner images, and a capacity planner that would
under-book the slot by more than the model file (+21 GiB sidecars, 262K KV).

Rather than hardcoding PromptForge, this design introduces a small **specialty
distribution registry** so the next exotic distribution (#1947's vLLM track,
draft-model pairs, LoRA adapter sets, control vectors) is a new registry entry,
not new plumbing.

## Decisions (operator-confirmed)

1. **Scope:** full accelerated path, generalized — a specialty-kind registry,
   PromptForge as the first entry.
2. **Sidecar storage:** ordinary `model_file` rows with new `role` values —
   refcount GC, hardlink dedup, and the downloads pane work unchanged. No
   separate companion store.
3. **Image selection:** a new `RUNNER_IMAGES` key (`promptforge`); the slot
   owns the image, resolution flows through the existing
   `resolve_runner_image` chain. The model never forces the image.
4. **Degraded behavior:** an ActiveFPX model on a non-PromptForge runner
   launches GGUF-only, **surfaced loudly** (slot drawer, health, log) — never
   a silent degrade, and not a hard refusal (the card blesses the GGUF-only
   mode). Per-kind `degraded_ok=False` escape hatch exists for distributions
   where a fallback is *not* legitimate (those 422 like `_guard_fpx_quant_runner`).
5. **Image build:** second flavor in `packaging/runner/` built on the CT130
   pipeline and published to GHCR / catalogued via `images.json` — not a
   hand-build.

## Architecture

No new subsystem. One new declarative module; six existing seams consume it.

### New module: `src/hal0/registry/specialty.py`

A CODE registry, the same philosophy as `RUNNER_IMAGES`
(`src/hal0/runners/__init__.py`): shipping support for a specialty kind means
shipping code anyway, so the truth lives in code, importable and testable,
not in a database table.

```python
@dataclass(frozen=True, slots=True)
class CompanionSpec:
    role: str                # model_file.role value, e.g. "promptforge_ffn"
    pattern: re.Pattern      # filename matcher within the repo tree
    env: str | None          # env var receiving the in-container path;
                             # None = install-only (e.g. runtime patch)
    required: bool = True    # missing => specialty incomplete => degraded

@dataclass(frozen=True, slots=True)
class SpecialtyKind:
    key: str                        # "promptforge"
    quant_marker: str | None        # "ActiveFPX" — matched against quant/filename
    companions: tuple[CompanionSpec, ...]
    mode_env: Mapping[str, str]     # static envs (mode toggles)
    # (the kind's `key` doubles as the capability token a runner must list
    #  in RunnerSupports.specialties — no separate field)
    degraded_ok: bool = True        # is a plain-GGUF fallback legitimate?
    default_ctx: int | None = None  # 262144 for promptforge
    argv_profile: str | None = None # seed profile name carrying the card argv

SPECIALTY_KINDS: dict[str, SpecialtyKind] = {
    "promptforge": SpecialtyKind(...),
}
```

First entry: `promptforge` with companions
`promptforge_ffn` (`PROMPTFORGE_SIDECAR`),
`promptforge_gdn` (`PROMPTFORGE_GDN_SIDECAR`),
`promptforge_output_k8` (`PROMPTFORGE_MTP_OUTPUT_K8_PROXY`),
and `runtime_patch` (env=None, install-only; consumed by the image build, kept
with the model for provenance).

`model_file.role` is free-form TEXT (`src/hal0/db/repository.py:234,264`) and
`FileSetEntry.role` is a plain `str` — **no schema migration, ever**, for any
future kind.

### Seam 1 — classification (`src/hal0/registry/fileset.py:311`)

`role_of()` gains one generic step before its fallthrough: match the filename
against every registered kind's `CompanionSpec.pattern`. A hit returns that
companion's role. Unknown large binaries keep classifying as `config` — the
planner never guesses.

### Seam 2 — detection & pull (`src/hal0/registry/fileset.py`, `pull.py`)

`_build_plan` calls a new `detect_specialty(files, quant) -> str | None`
(quant-marker and companion-presence based). On a hit:

- the kind's companion files join the `FileSetPlan` (counted in
  `total_bytes`, per-file SHA-256 verified by the existing machinery);
- the pull's `registry.update` merge (the same one that backfills
  `chat_template` / `tokenizer_repo`) stamps `metadata.specialty = key` and
  the quant (e.g. `quant="ActiveFPX"`, #1890's field) on the model row.

No new pull path — `run_pull(fileset=...)` is already the N-file vehicle.

### Seam 3 — runner capability (`src/hal0/runners/__init__.py`)

`RunnerSupports` gains `specialties: tuple[str, ...] = ()` (generic — not a
per-kind bool). New registry entry:

```python
"promptforge": Runner(
    "promptforge", DEFAULT_PROMPTFORGE_IMAGE, "llama-server",
    RunnerSupports(mtp=True, jinja=True, mmproj=True,
                   specialties=("promptforge",)),
    "gpu", "rocm", "promptforge",
    supported_backends=("rocm",),   # HIP-only: deliberately NOT vulkan —
                                    # the existing fit-check refuses a
                                    # gpu-vulkan device with no new code
    format_arch="gguf",
),
```

`DEFAULT_PROMPTFORGE_IMAGE` pins the validated GHCR tag in
`src/hal0/config/schema.py` beside `DEFAULT_ROCMFPX_IMAGE`, overridable via
the same env-var / `manifest.json` chain (`manifest_key="promptforge"`).

### Seam 4 — launch gate (`src/hal0/providers/container.py`)

`_guard_specialty_runner(slot_cfg, model_info, runner)` sits beside
`_guard_fpx_quant_runner` (:1603) and is called from the same single choke
point, `_resolve_llama_scalars` — shared by the real launch and the preview
path, so no route bypasses it.

Logic: model has `metadata.specialty = K`; if `K` is in
`runner.supports.specialties`, proceed accelerated. Otherwise:

- `SPECIALTY_KINDS[K].degraded_ok` → **launch degraded**: skip env synthesis,
  return a structured degraded reason
  (`slot.specialty_degraded`, with model/specialty/runner facts) that is
  stamped on the slot state, shown in the drawer and health output, and
  logged. Never silent (#1888).
- `degraded_ok=False` → raise `UnprocessableEntity` (422) with the same fact
  shape as `_guard_fpx_quant_runner`.

A required companion missing from `model_file` rows (partial pull, hand-added
row) also resolves to degraded-with-reason, not a crash.

### Seam 5 — env & argv synthesis (`src/hal0/providers/container.py:2159`)

On the accelerated path, `_resolve_llama_scalars` resolves each companion
`model_file` row (by role) to its in-container path under the read-only
model-store mount, builds `{CompanionSpec.env: path}` plus the kind's
`mode_env`, and merges it **under** `[server].env` — the same precedence rule
`gpu_visibility_env` follows, so an operator's explicit key always wins.

The card's argv envelope becomes a seed profile (`promptforge`: `mtp=True`,
`backend="rocm"`, the card's flags); `SpecialtyKind.default_ctx` feeds
`resolve_effective_context_size` so 262,144 is the default, still capped by
the slot ceiling.

### Seam 6 — capacity (`src/hal0/slots/capacity.py:134`)

`estimate_file_size_kv_mb` and its callers account for:

- **companion bytes**: sum of `size_bytes` over the model's specialty-role
  `model_file` rows (+21 GiB for PromptForge v3);
- **context**: the kind's `default_ctx` participates in `_ctx_tokens_for`'s
  resolution (below `defaults.context_size`, above the GGUF arch fallback).

Today's planner would under-book an ActiveFPX slot by more than the model
file itself; this closes that hole for every future kind with companions.

### Surface

- `GET /api/hardware` runner block (`src/hal0/api/routes/hardware.py:914`)
  adds `specialties` next to `mtp`/`jinja`/`mmproj`.
- Slot drawer: specialty chip on the model, degraded badge with the reason
  when the gate stamped one.
- Model catalog row: specialty shown; `quant` already displayed via #1890.

### Image build (parallel workstream, rides #1948 infrastructure)

`packaging/runner/promptforge/` — a second flavor beside
`packaging/runner/rocmfpx/`:

- base: `ciru-ai/ROCmFPX` tag `qwen3.8-activefpx-promptforge-v2.3` plus the
  model repo's `runtime/qwen38-v3-output-k8-runtime.patch`;
- `manifest.toml` build flags: `-DGGML_HIP=ON -DGGML_HIP_FORCE_MMQ=ON
  -DGGML_VULKAN=OFF`; composable_kernel pinned `fdf4bb7fcc98`; ROCm 7.15
  (TheRock); gfx1151;
- built by the CT130 Gitea pipeline, published as
  `ghcr.io/hal0ai/hal0-promptforge:<tag>`, catalogued via
  `Hal0ai/hal0-runner-images` `images.json`, and added to
  `manifest.json.toolbox_images.promptforge`;
- operator-gated pin: `DEFAULT_PROMPTFORGE_IMAGE` only moves after validation.

### Validation before curation (#1891)

Before any curated-catalog row or default pin: on-box ct150 probe (Paris
smoke), the card's own quality/HumanEval envelope, and MTP acceptance sanity.
Known card caveats to carry into the report: 512-token prompt route regressed
34 %; one HumanEval semantic regression on odd-length input (task 130);
vision+MTP screen not run on v3. Bench lane: decode + prompt at 2048/3524,
MTP acceptance cells.

## Error handling

| Condition | Behavior |
|---|---|
| Specialty model, capable runner, all companions present | Accelerated launch, env + argv synthesized |
| Specialty model, incapable runner, `degraded_ok` | GGUF-only launch, `slot.specialty_degraded` stamped + logged |
| Specialty model, incapable runner, not `degraded_ok` | 422 `slot.unsupported_specialty_for_runner` with facts |
| Required companion missing from `model_file` | Degraded with reason (never crash, never silent) |
| Companion SHA-256 mismatch on pull | Pull fails per existing digest-verify path |
| Operator `[server].env` collides with synthesized env | Operator wins (merge-under rule) |
| Unknown file in a specialty repo | `config` role, installed, never guessed into a companion |

## Testing

- `role_of` golden: `.pfs` names → companion roles; near-miss names → `config`.
- `detect_specialty`: marker hit, companion-only hit, no-hit, ambiguity → `None`.
- Pull integration: fileset includes companions in `total_bytes`; model row
  stamped `metadata.specialty` + `quant`; `model_file` rows carry roles.
- Guard: capable → env present; incapable + `degraded_ok` → degraded reason,
  no env; incapable + `degraded_ok=False` → 422; missing companion → degraded.
- Env precedence: operator `[server].env` key overrides synthesized key.
- Capacity: companion bytes + 262K KV included; non-specialty model unchanged.
- Fit-check: `promptforge` runner on a `gpu-vulkan` device warns/refuses via
  existing `supported_backends` logic (no new code, but a test pins it).
- Existing suites must stay green untouched: specialty absent ⇒ every current
  path byte-identical.

## Sequencing

1. **PR 1** — `specialty.py` registry + `role_of`/`detect_specialty` +
   pull stamping + tests.
2. **PR 2** — `RunnerSupports.specialties`, `RUNNER_IMAGES["promptforge"]`,
   `DEFAULT_PROMPTFORGE_IMAGE`, `_guard_specialty_runner`, env/argv
   synthesis, seed profile + tests.
3. **PR 3** — capacity booking, hardware route, UI chips/badges + tests.
4. **Parallel** — `packaging/runner/promptforge/` + CT130 pipeline +
   `images.json` entry.
5. **Gate** — ct150 validation, bench cells, then the default pin and any
   curated row.

Target: 1.0.x (per #1946's own sequencing note — not a GA gate).

## Out of scope

- #1947's vLLM runtime family (different `RuntimeFamily`; it will consume
  `SPECIALTY_KINDS` for its artifacts but needs its own provider work).
- Auto-selecting the promptforge image for a slot (the slot owns its image).
- A database-driven kind registry or template language (revisit if kinds
  multiply beyond code-release cadence).
